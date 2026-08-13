# TASK.md — MSB
> **АГЕНТ: читай этот файл первым. Затем AGENT.md для правил работы с проектом.**
> Не задавай вопросов о контексте — всё здесь.

---

## Быстрый старт

| Что | Где |
|-----|-----|
| Путь MSB | `C:\Users\Atreum\Desktop\MSBWorkshop\MSB\` |
| Путь GGselMSB | `C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB\` |
| Запуск MSB | `start.vbs` (в корне MSB) |
| Запуск GGselMSB | `start.vbs` (в корне GGselMSB) |
| API порт | 17248 |
| Логи MSB | `%APPDATA%\MSB\logs\` |
| Build renderer | `npm run build:renderer` |
| Архитектура | Electron + Fastify + React (Vite) |

## Архитектура (коротко)

```
MSBWorkshop/
  MSB/         ← антидетект-браузер (этот проект)
  GGselMSB/    ← Flask-панель продавца (отдельный проект, дёргает MSB по REST)

src/main/        — Electron main process, Fastify API, services
src/renderer/    — React UI (Vite build)
src/main/api/routes/  — все REST endpoints
src/main/services/    — profileManager, browserLauncher, networkCapture, trafficCapture, ...
src/main/lib/         — atomicFs, profileSchema, fingerprint, ...
```

Детали → `AGENT.md`, `LLM_MAP.md`, `MAP.md`

---

## ТЕКУЩАЯ ЗАДАЧА

**Реструктуризация проекта (2026-08-14)**

Что сделано:
- Папки MSB и GGselMSB перенесены в `C:\Users\Atreum\Desktop\MSBWorkshop\` как независимые соседи.
- `constants.js` обновлён: `GGSELLER_PROJECT_DIR` теперь указывает на `MSBWorkshop\GGselMSB`.
- Документация (`AGENT.md`, `LLM_MAP.md`, `TASK.md`, `MAP.md`) приведена в соответствие с реальным состоянием проекта.

Что нужно сделать вручную (одноразово):
1. Закрыть Zed
2. Удалить старую папку `C:\Users\Atreum\Desktop\MSB` (это старая копия)
3. Открыть Zed → добавить папки:
   - `C:\Users\Atreum\Desktop\MSBWorkshop\MSB`
   - `C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB`
4. Удалить `_relocate.ps1` из корня MSB (временный файл миграции)

---

## ЧТО УЖЕ СДЕЛАНО

- [x] Data model v2: provider / group / tags
- [x] MoreLogin API aliases (/api/env/*)
- [x] Dual response envelope (native + morelogin format)
- [x] Recycle bin (soft delete, 7-day retention)
- [x] Rate limiter (60 req/min)
- [x] E2E encryption (JS-level sensitive fields)
- [x] atomicFs — crash-safe write
- [x] profileSchema v1 + migration
- [x] Ajv validation на routes
- [x] GUI rewrite: Sidebar, Topbar, ProfileTable, dark theme
- [x] NetworkCaptureService — CDP ring buffer (2000 записей, per-profile)
- [x] TrafficCaptureService — mitmproxy subprocess (порты 18100–18200)
- [x] AutomationService — pipeline runner (create/start/stop/wait/http/eval/screenshot/traffic/network/crawl/llm)
- [x] CrawlerService — Crawlee (JS) + browser-use (Python LLM walkers)
- [x] AI Agents API — chatgpt/claude/gemini/minimax (start/stop/navigate/prompt/interrupt)
- [x] Anthropic API Adapter — POST /v1/messages → Clewd proxy (streaming + tool_use)
- [x] Workspace launcher — /workspace/launch + /workspace/status (one-click GGselMSB)
- [x] Browser Storage API — localStorage/sessionStorage/IDB/history
- [x] WebSocket Events — /ws/events (profile lifecycle)
- [x] Network Settings API — WebRTC/DNS/headers/TLS per-profile
- [x] Groups: virtual groups + color + description + group-meta.json
- [x] Реструктуризация: MSBWorkshop/MSB + MSBWorkshop/GGselMSB
- [x] Документация полностью актуализирована (AGENT.md, LLM_MAP.md, TASK.md, MAP.md)

---

## СЛЕДУЮЩИЙ ШАГ

_Не определён. Определи из беклога или нового запроса._

---

## ТУПИКИ

_Пусто._

---

## ПРАВИЛА ДЛЯ АГЕНТА

1. Не спрашивай что уже сделано — читай этот файл
2. После каждого шага обновляй этот файл — отмечай [x] и пиши находки
3. Если застрял — опиши тупик в разделе ТУПИКИ и предложи 2-3 варианта
4. Пути к проектам: MSB = `C:\Users\Atreum\Desktop\MSBWorkshop\MSB`, GGselMSB = `C:\Users\Atreum\Desktop\MSBWorkshop\GGselMSB`
