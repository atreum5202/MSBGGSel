import asyncio
import json
import httpx
import websockets
import os
import sys

# Настройки MoreLogin
MORELOGIN_API_BASE = "http://127.0.0.1:40000"
MORELOGIN_API_ID = "1716740459457616"
MORELOGIN_API_KEY = "8afb02927e724b6caadc6363f13f3c61"

# Возьми первый доступный профиль из группы GGSeller
# Или укажи конкретный envId вручную если знаешь

async def get_first_profile():
    async with httpx.AsyncClient(trust_env=False) as c:
        r = await c.post(
            f"{MORELOGIN_API_BASE}/api/env/page",
            json={"pageNo": 1, "pageSize": 5},
            headers={"X-Api-Id": MORELOGIN_API_ID, "X-Api-Key": MORELOGIN_API_KEY}
        )
        print("env/page response:", r.text)
        data = r.json()
        profiles = data.get("data", {}).get("dataList", [])
        if not profiles:
            raise RuntimeError("Нет профилей в MoreLogin")
        return str(profiles[0]["id"])

async def start_profile(env_id):
    async with httpx.AsyncClient(timeout=30, trust_env=False) as c:
        r = await c.post(
            f"{MORELOGIN_API_BASE}/api/env/start",
            json={"envId": env_id, "isHeadless": False, "cdpEvasion": True},
            headers={"X-Api-Id": MORELOGIN_API_ID, "X-Api-Key": MORELOGIN_API_KEY}
        )
        data = r.json()
        return str(data["data"]["debugPort"])

async def cdp(ws, method, params=None, msg_id=1):
    await ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(await ws.recv())
        if msg.get("id") == msg_id:
            return msg.get("result", {})

async def explore():
    env_id = await get_first_profile()
    print(f"Профиль: {env_id}")
    
    debug_port = await start_profile(env_id)
    print(f"debugPort: {debug_port}")
    
    # Получаем список вкладок
    async with httpx.AsyncClient(trust_env=False) as c:
        tabs = (await c.get(f"http://127.0.0.1:{debug_port}/json")).json()
    
    tab = tabs[0]
    ws_url = tab["webSocketDebuggerUrl"]
    print(f"WS: {ws_url}")
    
    async with websockets.connect(ws_url, max_size=50*1024*1024) as ws:
        # Включаем домены
        await cdp(ws, "Page.enable", {}, 1)
        await cdp(ws, "DOM.enable", {}, 2)
        
        # Переходим на Gemini
        print("Открываем gemini.google.com/app ...")
        await cdp(ws, "Page.navigate", {"url": "https://gemini.google.com/app"}, 3)
        await asyncio.sleep(5)
        
        # Делаем скриншот
        shot = await cdp(ws, "Page.captureScreenshot", {"format": "jpeg", "quality": 80}, 4)
        import base64, pathlib
        pathlib.Path("scratch/gemini_screenshot_1_initial.jpg").write_bytes(
            base64.b64decode(shot["data"])
        )
        print("Скриншот 1 сохранён: scratch/gemini_screenshot_1_initial.jpg")
        
        # Ищем все интерактивные элементы
        result = await cdp(ws, "Runtime.evaluate", {
            "expression": """
                JSON.stringify({
                    textareas: Array.from(document.querySelectorAll('textarea')).map(el => ({
                        tag: el.tagName, id: el.id, name: el.name,
                        placeholder: el.placeholder, ariaLabel: el.getAttribute('aria-label'),
                        class: el.className.substring(0,80)
                    })),
                    contentEditables: Array.from(document.querySelectorAll('[contenteditable]')).map(el => ({
                        tag: el.tagName, id: el.id,
                        ariaLabel: el.getAttribute('aria-label'),
                        class: el.className.substring(0,80),
                        role: el.getAttribute('role')
                    })),
                    fileInputs: Array.from(document.querySelectorAll('input[type=file]')).map(el => ({
                        id: el.id, accept: el.accept, ariaLabel: el.getAttribute('aria-label'),
                        class: el.className.substring(0,80)
                    })),
                    uploadButtons: Array.from(document.querySelectorAll('button')).filter(b =>
                        (b.getAttribute('aria-label') || '').toLowerCase().includes('upload') ||
                        (b.getAttribute('aria-label') || '').toLowerCase().includes('image') ||
                        (b.getAttribute('aria-label') || '').toLowerCase().includes('attach') ||
                        (b.title || '').toLowerCase().includes('upload')
                    ).map(b => ({
                        ariaLabel: b.getAttribute('aria-label'),
                        title: b.title,
                        class: b.className.substring(0,80),
                        id: b.id
                    })),
                    submitButtons: Array.from(document.querySelectorAll('button[type=submit], button[aria-label*="Send"], button[aria-label*="send"]')).map(b => ({
                        ariaLabel: b.getAttribute('aria-label'),
                        class: b.className.substring(0,80)
                    }))
                })
            """,
            "returnByValue": True
        }, 5)
        
        elements = json.loads(result["result"]["value"])
        print("\n=== ЭЛЕМЕНТЫ НА СТРАНИЦЕ ===")
        print(json.dumps(elements, ensure_ascii=False, indent=2))
        
        # Сохраняем отчёт
        import pathlib
        pathlib.Path("scratch/gemini_elements.json").write_text(
            json.dumps(elements, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print("\nОтчёт сохранён: scratch/gemini_elements.json")
        
        # Теперь пробуем загрузить тестовое фото с рабочего стола
        # Ищем любое jpg/png на рабочем столе
        desktop = os.path.expanduser("~/Desktop")
        test_images = [f for f in os.listdir(desktop) 
                      if f.lower().endswith(('.jpg','.jpeg','.png'))]
        
        if test_images:
            test_img_path = os.path.join(desktop, test_images[0])
            print(f"\nНайдено тестовое фото: {test_img_path}")
            
            # Ищем file input через CDP
            node_result = await cdp(ws, "Runtime.evaluate", {
                "expression": "document.querySelector('input[type=file]') ? 'FOUND' : 'NOT_FOUND'",
                "returnByValue": True
            }, 6)
            print(f"input[type=file]: {node_result['result']['value']}")
            
            # Если нашли — пробуем загрузить файл
            if node_result['result']['value'] == 'FOUND':
                # Получаем nodeId
                doc = await cdp(ws, "DOM.getDocument", {}, 7)
                search = await cdp(ws, "DOM.querySelector", {
                    "nodeId": doc["root"]["nodeId"],
                    "selector": "input[type=file]"
                }, 8)
                node_id = search.get("nodeId")
                if node_id:
                    await cdp(ws, "DOM.setFileInputFiles", {
                        "nodeId": node_id,
                        "files": [test_img_path]
                    }, 9)
                    print("Файл загружен через DOM.setFileInputFiles!")
                    await asyncio.sleep(3)
                    
                    # Скриншот после загрузки
                    shot2 = await cdp(ws, "Page.captureScreenshot", {"format": "jpeg", "quality": 80}, 10)
                    pathlib.Path("scratch/gemini_screenshot_2_after_upload.jpg").write_bytes(
                        base64.b64decode(shot2["data"])
                    )
                    print("Скриншот 2 сохранён: scratch/gemini_screenshot_2_after_upload.jpg")
        else:
            print("Фото на рабочем столе не найдено, пропускаем загрузку файла")

asyncio.run(explore())
