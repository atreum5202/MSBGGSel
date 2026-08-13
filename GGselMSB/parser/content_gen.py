"""
parser/content_gen.py
=====================
AI-генерация карточки товара через Gemini с ротацией ключей.

Ротация:
  - Читает GEMINI_API_KEYS (список через запятую) из ENV
  - При ошибке ключа (429, 400, 403) -- помечает его и переходит к следующему
  - Статус каждого ключа хранится в памяти: ok / exhausted / error
  - /api/parser/gemini/status возвращает состояние всех ключей

Безопасно:
  - Нет ни одного рабочего ключа -> fallback (без падений)
  - httpx недоступен -> тоже fallback
  - Любой сбой -> статус gen_failed, товар остаётся с оригинальным названием
"""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
except ImportError:
    pass

try:
    import httpx as _httpx
    _HTTPX_OK = True
except ImportError:
    _HTTPX_OK = False

# -- Конфигурация (читается при вызове, не при импорте) ----------------------
def _cfg_model():
    return os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

def _cfg_max_retry():
    return int(os.getenv("GEMINI_MAX_RETRY", "3"))

def _cfg_timeout():
    return int(os.getenv("GEMINI_TIMEOUT", "30"))

_DEFAULT_GEN_DIR = Path(__file__).resolve().parent.parent / "data" / "images" / "generated"
IMAGE_SAVE_DIR = Path(os.getenv("PARSER_IMAGES_DIR", str(_DEFAULT_GEN_DIR)))

# Папка для web-доступных AI-картинок (Flask отдаёт как /static/generated/)
_STATIC_GEN_DIR = Path(__file__).resolve().parent.parent / "static" / "generated"


# ===========================================================================
#  KeyPool -- ротация и статус ключей
# ===========================================================================
class _KeyStatus:
    def __init__(self, key: str, index: int):
        self.key = key
        self.index = index
        self.masked = key[:8] + "..." + key[-4:]
        self.status: str = "ok"        # ok | exhausted | error
        self.last_error: str = ""
        self.last_used_at: Optional[str] = None
        self.fail_count: int = 0
        self.success_count: int = 0

    def to_dict(self) -> dict:
        return {
            "index":        self.index,
            "masked":       self.masked,
            "status":       self.status,
            "last_error":   self.last_error,
            "last_used_at": self.last_used_at,
            "fail_count":   self.fail_count,
            "success_count": self.success_count,
        }


class GeminiKeyPool:
    """Пул ключей Gemini с ротацией и запоминанием статуса."""

    def __init__(self):
        self._lock = threading.Lock()
        self._keys: List[_KeyStatus] = []
        self._current_idx: int = 0
        self._reload()

    def _reload(self):
        """Перечитывает ключи из ENV."""
        raw = os.getenv("GEMINI_API_KEYS", "").strip()
        if not raw:
            raw = os.getenv("GEMINI_API_KEY", "").strip()
        
        # Загружаем также GEMINI_API_KEY_1, GEMINI_API_KEY_2 и т.д.
        keys_from_env = []
        if raw:
            keys_from_env.extend([k.strip() for k in raw.split(",")])
        for env_k, env_v in os.environ.items():
            if env_k.startswith("GEMINI_API_KEY_"):
                keys_from_env.extend([k.strip() for k in env_v.split(",")])
        
        valid_keys = []
        for key in keys_from_env:
            key = key.strip()
            # Проверка: ключ валидный если длина >= 20
            if not key or len(key) < 20:
                continue
            if key not in valid_keys:
                valid_keys.append(key)

        with self._lock:
            self._keys = [_KeyStatus(k, i) for i, k in enumerate(valid_keys)]
            self._current_idx = 0

    def available(self) -> bool:
        with self._lock:
            return _HTTPX_OK and any(k.status in ("ok",) for k in self._keys)

    def get_next_key(self) -> Optional[_KeyStatus]:
        """Возвращает следующий рабочий ключ или None."""
        with self._lock:
            n = len(self._keys)
            if not n:
                return None
            for i in range(n):
                idx = (self._current_idx + i) % n
                ks = self._keys[idx]
                if ks.status == "ok":
                    self._current_idx = idx
                    return ks
            return None

    def mark_success(self, ks: _KeyStatus):
        with self._lock:
            ks.status = "ok"
            ks.success_count += 1
            ks.last_used_at = datetime.utcnow().isoformat()

    def mark_fail(self, ks: _KeyStatus, error: str, exhausted: bool = False):
        with self._lock:
            ks.fail_count += 1
            ks.last_error = error[:200]
            ks.last_used_at = datetime.utcnow().isoformat()
            ks.status = "exhausted" if exhausted else "error"
            n = len(self._keys)
            if n > 1:
                self._current_idx = (self._current_idx + 1) % n

    def reset_key(self, index: int):
        with self._lock:
            for ks in self._keys:
                if ks.index == index:
                    ks.status = "ok"
                    ks.last_error = ""
                    ks.fail_count = 0
                    break

    def reset_all(self):
        with self._lock:
            for ks in self._keys:
                ks.status = "ok"
                ks.last_error = ""
                ks.fail_count = 0

    def summary(self) -> dict:
        with self._lock:
            total = len(self._keys)
            ok = sum(1 for k in self._keys if k.status == "ok")
            exhausted = sum(1 for k in self._keys if k.status == "exhausted")
            error = sum(1 for k in self._keys if k.status == "error")
            return {
                "total":     total,
                "ok":        ok,
                "exhausted": exhausted,
                "error":     error,
                "available": _HTTPX_OK and ok > 0,
                "keys":      [ks.to_dict() for ks in self._keys],
            }


# Синглтон -- lazy init (ENV читается при первом вызове, не при импорте модуля)
_key_pool: Optional[GeminiKeyPool] = None
_key_pool_lock = threading.Lock()


def get_key_pool() -> GeminiKeyPool:
    global _key_pool
    if _key_pool is None:
        with _key_pool_lock:
            if _key_pool is None:
                _key_pool = GeminiKeyPool()
    return _key_pool


# ===========================================================================
#  Groq API -- fallback when all Gemini keys exhausted
# ===========================================================================
def _cfg_groq_model():
    return os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

def _cfg_groq_timeout():
    return int(os.getenv("GROQ_TIMEOUT", "30"))


class GroqKeyPool:
    def __init__(self):
        self._lock = threading.Lock()
        self._keys = []
        self._current_idx = 0
        self._reload()

    def _reload(self):
        raw = os.getenv("GROQ_API_KEYS", "").strip()
        if not raw:
            raw = os.getenv("GROQ_API_KEY", "").strip()
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        with self._lock:
            self._keys = [_KeyStatus(k, i) for i, k in enumerate(keys)]
            self._current_idx = 0

    def available(self):
        with self._lock:
            return _HTTPX_OK and any(k.status == "ok" for k in self._keys)

    def get_next_key(self):
        with self._lock:
            n = len(self._keys)
            if not n:
                return None
            for i in range(n):
                idx = (self._current_idx + i) % n
                ks = self._keys[idx]
                if ks.status == "ok":
                    self._current_idx = idx
                    return ks
            return None

    def mark_success(self, ks):
        with self._lock:
            ks.status = "ok"
            ks.success_count += 1
            ks.last_used_at = datetime.utcnow().isoformat()

    def mark_fail(self, ks, error, exhausted=False):
        with self._lock:
            ks.fail_count += 1
            ks.last_error = error[:200]
            ks.last_used_at = datetime.utcnow().isoformat()
            ks.status = "exhausted" if exhausted else "error"
            n = len(self._keys)
            if n > 1:
                self._current_idx = (self._current_idx + 1) % n

    def reset_all(self):
        with self._lock:
            for ks in self._keys:
                ks.status = "ok"
                ks.last_error = ""
                ks.fail_count = 0

    def summary(self):
        with self._lock:
            total = len(self._keys)
            ok = sum(1 for k in self._keys if k.status == "ok")
            exhausted = sum(1 for k in self._keys if k.status == "exhausted")
            error = sum(1 for k in self._keys if k.status == "error")
            return {"total": total, "ok": ok, "exhausted": exhausted,
                    "error": error, "available": _HTTPX_OK and ok > 0,
                    "keys": [ks.to_dict() for ks in self._keys]}


_groq_pool = None
_groq_pool_lock = threading.Lock()


def get_groq_pool():
    global _groq_pool
    if _groq_pool is None:
        with _groq_pool_lock:
            if _groq_pool is None:
                _groq_pool = GroqKeyPool()
    return _groq_pool


def _call_groq(prompt):
    """Groq API call (OpenAI-compatible). Fallback for Gemini."""
    pool = get_groq_pool()
    model = _cfg_groq_model()
    timeout = _cfg_groq_timeout()
    last_err = None
    total_attempts = 3 * max(1, len(pool._keys))
    for attempt in range(total_attempts):
        ks = pool.get_next_key()
        if ks is None:
            raise RuntimeError("No available Groq API keys")
        try:
            resp = _httpx.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {ks.key}", "Content-Type": "application/json"},
                json={"model": model, "messages": [{"role": "user", "content": prompt}],
                      "max_tokens": 1024, "temperature": 0.7},
                timeout=timeout,
            )
            if resp.status_code == 429:
                pool.mark_fail(ks, "HTTP 429", exhausted=True)
                last_err = "HTTP 429"
                time.sleep(min(2 ** (attempt % 4), 16))
                continue
            if resp.status_code in (400, 401, 403):
                err = "HTTP %d: %s" % (resp.status_code, resp.text[:100])
                pool.mark_fail(ks, err)
                last_err = err
                continue
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]
            pool.mark_success(ks)
            return text
        except (_httpx.TimeoutException, _httpx.NetworkError) as e:
            pool.mark_fail(ks, "Network: %s" % e)
            last_err = str(e)
            time.sleep(2)
            continue
        except Exception as e:
            pool.mark_fail(ks, str(e)[:200])
            last_err = str(e)
            continue
    raise RuntimeError("All Groq keys exhausted. Last: %s" % last_err)


import groq as _groq

def _generate_via_groq(prompt: str) -> str:
    if 'NO_PROXY' in os.environ:
        del os.environ['NO_PROXY']
    if 'no_proxy' in os.environ:
        del os.environ['no_proxy']
        
    api_key = os.getenv("GROQ_API_KEY", "")
    if not api_key:
        raise ValueError("GROQ_API_KEY не задан")
    client = _groq.Groq(api_key=api_key)
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1000,
        temperature=0.7,
    )
    return resp.choices[0].message.content

def _call_ai(prompt):
    """Universal AI call: Groq first, Gemini as fallback."""
    try:
        return _generate_via_groq(prompt)
    except Exception as e:
        print(f"Groq failed: {e} — fallback Gemini")

    gemini_pool = get_key_pool()
    if gemini_pool.available():
        try:
            return _call_gemini(prompt)
        except RuntimeError:
            pass  # all Gemini keys exhausted -- try Groq fallback
    groq_pool = get_groq_pool()
    if groq_pool.available():
        return _call_groq(prompt)
    raise RuntimeError("All AI providers exhausted (Gemini + Groq)")




# ===========================================================================
#  Gemini API call с ротацией
# ===========================================================================
def _call_gemini(prompt: str) -> str:
    """
    Вызывает Gemini API с ротацией ключей.
    Возвращает текст ответа или бросает RuntimeError.
    """
    pool = get_key_pool()
    max_retry = _cfg_max_retry()
    timeout = _cfg_timeout()
    model = _cfg_model()
    last_err = None

    total_attempts = max_retry * max(1, len(pool._keys))
    for attempt in range(total_attempts):
        ks = pool.get_next_key()
        if ks is None:
            raise RuntimeError("No available Gemini API keys")

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent?key={ks.key}"
        )
        try:
            resp = _httpx.post(
                url,
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=timeout,
            )

            if resp.status_code == 429:
                err = "HTTP 429 quota exceeded"
                pool.mark_fail(ks, err, exhausted=True)
                last_err = err
                wait = min(2 ** (attempt % 4), 16)  # 1,2,4,8,16 сек — не больше 16
                time.sleep(wait)
                continue

            if resp.status_code in (400, 403):
                err = "HTTP %d: %s" % (resp.status_code, resp.text[:100])
                pool.mark_fail(ks, err, exhausted=False)
                last_err = err
                continue

            resp.raise_for_status()
            text = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
            pool.mark_success(ks)
            return text

        except (_httpx.TimeoutException, _httpx.NetworkError) as e:
            err = "Network: %s" % e
            pool.mark_fail(ks, err, exhausted=False)
            last_err = err
            time.sleep(2)
            continue

        except Exception as e:
            err = str(e)[:200]
            pool.mark_fail(ks, err, exhausted=False)
            last_err = err
            continue

    raise RuntimeError("All keys exhausted. Last error: %s" % last_err)


# ===========================================================================
#  Публичное API
# ===========================================================================
def generate_product_card(title: str, category: str, price: float,
                          sales_count: int = 0, seller_rating: float = 0.0,
                          reviews_count: int = 0) -> dict:
    """
    Генерирует карточку + AI-оценку выгодности через Gemini.
    При ошибке возвращает fallback + _status='gen_failed'.
    """
    pool = get_key_pool()
    if not pool.available():
        return {
            "title": title,
            "description": "",
            "tags": "",
            "_status": "gen_failed",
            "_error": "No available Gemini API keys",
        }

    # OLD PROMPT:
    # prompt = (
    #     "Ты аналитик дропшиппинга на ggsel.net (российский маркетплейс цифровых товаров).\n"
    #     "Товар: %s\n"
    #     "Категория: %s\n"
    #     "Цена донора: %.2f RUB\n"
    #     "Продажи: %d\n"
    #     "Рейтинг продавца: %.1f\n"
    #     "Отзывы: %d\n\n"
    #     "Оцени выгодность для перепродажи и создай карточку.\n"
    #     "Ответь ТОЛЬКО JSON без markdown:\n"
    #     "{\n"
    #     '  "profit_score": <0-100, чем выше sales_count и рейтинг -- тем выше>,\n'
    #     '  "recommended_margin_pct": <%% наценки, реалистично для категории>,\n'
    #     '  "my_price": <price * (1 + margin/100), округли до X.99>,\n'
    #     '  "risk_level": "low"|"medium"|"high",\n'
    #     '  "risk_reason": <одна фраза>,\n'
    #     '  "title": <новое SEO название до 80 символов>,\n'
    #     '  "description": <описание 200-400 символов, выгоды покупателя>,\n'
    #     '  "tags": <5-8 тегов через запятую>\n'
    #     "}"
    # ) % (title, category, price, sales_count, seller_rating, reviews_count)

    prompt = (
        "Ты копирайтер премиального магазина atreuM на ggsel.net.\n"
        "Стиль магазина: dark premium, лаконично, с эмодзи-акцентами, без воды.\n\n"
        "Товар: %s\n"
        "Категория: %s\n"
        "Цена донора: %.2f RUB\n"
        "Продажи: %d\n"
        "Рейтинг продавца: %.1f\n"
        "Отзывы: %d\n\n"
        "Оцени выгодность для перепродажи и создай карточку.\n"
        "Ответь ТОЛЬКО JSON без markdown:\n"
        "{\n"
        '  "profit_score": <0-100, чем выше sales_count и рейтинг -- тем выше>,\n'
        '  "recommended_margin_pct": <%% наценки, реалистично для категории>,\n'
        '  "my_price": <price * (1 + margin/100), округли до X.99>,\n'
        '  "risk_level": "low"|"medium"|"high",\n'
        '  "risk_reason": <одна фраза>,\n'
        '  "title": <новое название. Правила: 1 тематический эмодзи в начале (🎮 игры, 🔑 ключи, 🛡️ антивирус, 🎵 музыка, 🎬 видео, 💻 ПО). В конце добавить \" | atreuM\". Максимум 80 символов включая эмодзи и бренд. Без лишних слов типа \"купить\", \"лучший\", \"скидка\">,\n'
        '  "description": <описание 150-300 символов. Структура: главная выгода -> что получит покупатель -> призыв. Используй 2-3 эмодзи как маркеры (✅ ⚡ 🔥 💎 🚀). Тон: уверенный, премиальный, без восклицательного спама. Без фраз \"лучший\", \"уникальный\", \"супер\">,\n'
        '  "tags": <5-7 тегов через запятую, только релевантные, без повторов названия товара>\n'
        "}"
    ) % (title, category, price, sales_count, seller_rating, reviews_count)

    try:
        text = _call_ai(prompt)
        text = re.sub(r"```json|```", "", text).strip()
        data = json.loads(text)

        def _f(v, default=0.0, lo=None, hi=None):
            try:
                val = float(v)
                if lo is not None: val = max(lo, val)
                if hi is not None: val = min(hi, val)
                return val
            except (TypeError, ValueError):
                return default

        profit_score = _f(data.get("profit_score"), 0.0, 0.0, 100.0)
        margin_pct   = _f(data.get("recommended_margin_pct"), 20.0, 0.0, 200.0)
        my_price     = _f(data.get("my_price"), 0.0, 0.0)
        if my_price <= 0 and price > 0:
            my_price = round(float(price) * (1 + margin_pct / 100.0) - 0.01, 2)

        risk = str(data.get("risk_level", "medium")).lower()
        if risk not in ("low", "medium", "high"):
            risk = "medium"

        return {
            "title":                  str(data.get("title", title))[:120],
            "description":            str(data.get("description", ""))[:1000],
            "tags":                   str(data.get("tags", ""))[:200],
            "profit_score":           round(profit_score, 1),
            "recommended_margin_pct": round(margin_pct, 1),
            "my_price":               round(my_price, 2),
            "risk_level":             risk,
            "risk_reason":            str(data.get("risk_reason", ""))[:200],
        }

    except Exception as e:
        return {
            "_status": "gen_failed",
            "_error": str(e),
            "title": title,
            "description": "",
            "tags": [],
            "profit_score": 0,
            "recommended_margin_pct": 0,
            "my_price": 0,
            "risk_level": "unknown",
            "risk_reason": "AI generation failed",
        }


def generate_image(title: str, product_id: str) -> str:
    """Генерирует изображение через Gemini Imagen.
    Сохраняет в static/generated/ и возвращает web-URL вида /static/generated/<pid>.jpg
    (или пустую строку при ошибке).
    """
    pool = get_key_pool()
    ks = pool.get_next_key()
    if not ks:
        return ""

    _STATIC_GEN_DIR.mkdir(parents=True, exist_ok=True)
    prompt = (
        "Product photo for digital goods marketplace. Product: %s. "
        "Style: clean white background, professional product shot, high quality, "
        "no text, no watermarks." % title
    )
    img_url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "imagen-3.0-generate-001:predict?key=%s" % ks.key
    )
    safe_pid = "".join(c if c.isalnum() else "_" for c in product_id)
    try:
        resp = _httpx.post(
            img_url,
            json={"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}},
            timeout=60,
        )
        resp.raise_for_status()
        b64 = resp.json()["predictions"][0]["bytesBase64Encoded"]
        img_path = _STATIC_GEN_DIR / ("%s.jpg" % safe_pid)
        img_path.write_bytes(base64.b64decode(b64))
        pool.mark_success(ks)
        return "/static/generated/%s.jpg" % safe_pid
    except Exception:
        return ""


def enrich_product(product: dict) -> dict:
    """
    Полное AI-обогащение товара.
    Вход:  {product_id, title, category, price, sales_count, seller_rating, reviews_count}
    Выход: +generated_title, generated_desc, generated_tags, profit_score, my_price, ...
    """
    title         = (product.get("title") or "").strip()
    category      = (product.get("category") or "").strip()
    price         = float(product.get("price") or 0)
    sales_count   = int(product.get("sales_count") or 0)
    reviews_count = int(product.get("reviews_count") or 0)
    seller_rating = float(product.get("seller_rating") or 0.0)

    card = generate_product_card(
        title=title, category=category, price=price,
        sales_count=sales_count, seller_rating=seller_rating,
        reviews_count=reviews_count,
    )
    failed = card.get("_status") == "gen_failed"

    img_path = ""
    if not failed and title and product.get("product_id"):
        img_path = generate_image(card.get("title") or title, product["product_id"])

    out = dict(product)
    out["generated_title"]        = card.get("title", title)
    out["generated_desc"]         = card.get("description", "")
    out["generated_tags"]         = card.get("tags", "")
    out["generated_image_url"]    = img_path
    out["profit_score"]           = card.get("profit_score")
    out["my_price"]               = card.get("my_price")
    out["recommended_margin_pct"] = card.get("recommended_margin_pct")
    out["risk_level"]             = card.get("risk_level")
    out["risk_reason"]            = card.get("risk_reason")
    out["status"]                 = "gen_failed" if failed else "ai_enriched"
    if failed:
        out["ai_error"] = card.get("_error", "")
    return out

def generate_review_reply(review_text: str, product_title: str, rating: int) -> str:
    prompt = (
        f"Ты продавец цифровых товаров на ggsel.net. Пиши вежливые ответы покупателям на их отзывы.\n"
        f"Отзыв: {review_text}\n"
        f"Товар: {product_title}\n"
        f"Рейтинг: {rating}\n"
        f"Напиши ответ из 1-3 предложений, благодарность + обещание исправить если есть претензии.\n"
        f"Ответ должен быть коротким, тёплым и человечным."
    )
    res = _call_ai(prompt)
    if res.startswith("ERR_"):
        return ""
    return res.strip()
