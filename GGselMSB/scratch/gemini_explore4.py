"""scratch/gemini_explore4.py — точечный тест: клик Добавить файлы, что в меню, где file input."""
from __future__ import annotations
import asyncio, base64, json, os, pathlib, sys, time
import httpx, websockets

ML_BASE="http://127.0.0.1:40000"
ML_ID="1716740459457616"
ML_KEY="8afb02927e724b6caadc6363f13f3c61"
SCRATCH=pathlib.Path("scratch"); SCRATCH.mkdir(exist_ok=True)
LOG=SCRATCH/"gemini_explore4.log"

class Tee:
    def __init__(self,p,s):
        self._o=s; self.f=open(p,"w",encoding="utf-8")
    def write(self,s):
        try: self._o.write(s)
        except: pass
        try: self.f.write(s); self.f.flush()
        except: pass
    def flush(self):
        try: self._o.flush()
        except: pass
        try: self.f.flush()
        except: pass

async def get_profile():
    async with httpx.AsyncClient(timeout=20,trust_env=False) as c:
        r=await c.post(f"{ML_BASE}/api/env/page",json={"pageNo":1,"pageSize":3},
            headers={"X-Api-Id":ML_ID,"X-Api-Key":ML_KEY})
        r.raise_for_status()
        d=r.json().get("data") or {}
        items = d.get("list") or d.get("dataList") or d.get("records") or []
        if not items: raise RuntimeError(f"no profiles: {r.json()!r}")
        it=items[0]
        return str(it.get("envId") or it.get("id") or it.get("seq"))

async def start(p):
    async with httpx.AsyncClient(timeout=60,trust_env=False) as c:
        r=await c.post(f"{ML_BASE}/api/env/start",json={"envId":p,"isHeadless":False,"cdpEvasion":True},
            headers={"X-Api-Id":ML_ID,"X-Api-Key":ML_KEY})
        return str(r.json()["data"]["debugPort"])

class CDP:
    def __init__(self,ws):
        self.ws=ws; self._id=0
    async def send(self,m,p=None,t=30.0):
        self._id+=1; mid=self._id
        await self.ws.send(json.dumps({"id":mid,"method":m,"params":p or {}}))
        dl=time.monotonic()+t
        while True:
            r=dl-time.monotonic()
            if r<=0: raise TimeoutError(m)
            raw=await asyncio.wait_for(self.ws.recv(),timeout=r)
            try: msg=json.loads(raw)
            except: continue
            if msg.get("id")==mid:
                if "error" in msg: raise RuntimeError(f"{m}: {msg['error']}")
                return msg.get("result",{})
    async def ev(self,e,t=15.0):
        r=await self.send("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":True},t)
        if "exceptionDetails" in r: return {"__err__":r["exceptionDetails"]}
        return r.get("result",{}).get("value")
    async def shot(self,path):
        r=await self.send("Page.captureScreenshot",{"format":"jpeg","quality":80},15.0)
        pathlib.Path(path).write_bytes(base64.b64decode(r["data"]))

async def main():
    sys.stdout=Tee(LOG,sys.stdout)
    sys.stderr=Tee(LOG,sys.stderr)
    p=await get_profile()
    dp=await start(p)
    print(f"profile={p} debugPort={dp}")

    async with httpx.AsyncClient(timeout=10,trust_env=False) as c:
        tabs=(await c.get(f"http://127.0.0.1:{dp}/json/list")).json()
    tab=[t for t in tabs if t.get("type")=="page" and t.get("url","").startswith("http")][0]
    print(f"tab: {tab['url']}")
    async with websockets.connect(tab["webSocketDebuggerUrl"],max_size=50*1024*1024,ping_interval=20) as ws:
        c=CDP(ws)
        await c.send("Page.enable"); await c.send("DOM.enable"); await c.send("Runtime.enable")

        # navigate fresh
        await c.send("Page.navigate",{"url":"https://gemini.google.com/app"})
        for i in range(20):
            await asyncio.sleep(1)
            v=await c.ev("!!document.querySelector('[contenteditable=true]')")
            if v: print(f"loaded in {i+1}s"); break

        # Step 1: click "Добавить файлы" (aria-label="Добавить файлы")
        print("\n[STEP 1] Click Добавить файлы")
        r=await c.ev(r"""(() => {
            const b = document.querySelector('button[aria-label="Добавить файлы"]');
            if (!b) return {found:false};
            b.click();
            return {found:true, rect: b.getBoundingClientRect()};
        })()""")
        print(f"  click result: {r}")
        await asyncio.sleep(2)
        await c.shot(SCRATCH/"gemini_4_menu.jpg")
        snap=await c.ev(r"""(() => {
            const desc = (el) => ({tag:el.tagName, aria:el.getAttribute('aria-label'),
                text:(el.innerText||'').trim().substring(0,60), cls:(el.className||'').substring(0,80),
                rect: (()=>{const r=el.getBoundingClientRect();
                    return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};})(),
                visible: (()=>{const r=el.getBoundingClientRect(); const s=getComputedStyle(el);
                    return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none';})()});
            return {
                buttons: Array.from(document.querySelectorAll('button')).map(desc).filter(b=>b.visible),
                menuItems: Array.from(document.querySelectorAll('[role=menuitem], [role=option], [role=listitem]')).map(desc).filter(b=>b.visible),
                fileInputs: Array.from(document.querySelectorAll('input[type=file]')).map(desc),
                links: Array.from(document.querySelectorAll('a')).map(desc).filter(b=>b.visible && b.text).slice(0,20),
            };
        })()""")
        print("  DOM after click:")
        print(json.dumps(snap, ensure_ascii=False, indent=2, default=str))

        # Step 2: ищем пункт "Upload file" / "Загрузить файл"
        print("\n[STEP 2] Click 'Upload file' в меню")
        r=await c.ev(r"""(() => {
            // Ищем среди menuItems и buttons
            const all = [...document.querySelectorAll('[role=menuitem], [role=option]'),
                         ...document.querySelectorAll('button'), ...document.querySelectorAll('a')];
            const candidates = [];
            for (const el of all) {
                const txt = (el.innerText || '').toLowerCase().trim();
                const aria = (el.getAttribute('aria-label') || '').toLowerCase();
                if (!txt && !aria) continue;
                if (txt.includes('upload') || txt.includes('загруз') || txt.includes('файл') ||
                    txt.includes('компьютер') || txt.includes('с устройства') || txt.includes('from computer') ||
                    aria.includes('upload') || aria.includes('загруз')) {
                    const r = el.getBoundingClientRect();
                    if (r.width>0 && r.height>0) {
                        candidates.push({tag:el.tagName, text:el.innerText.substring(0,50), aria:el.getAttribute('aria-label'),
                            cls:(el.className||'').substring(0,80), rect:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}});
                    }
                }
            }
            return candidates;
        })()""")
        print(f"  menu candidates: {json.dumps(r, ensure_ascii=False, indent=2)}")
        # Пробуем кликнуть первого
        if isinstance(r,list) and r:
            first=r[0]
            print(f"  → click: {first}")
            r2=await c.ev(f"""(() => {{
                const all = [...document.querySelectorAll('[role=menuitem], [role=option]'),
                             ...document.querySelectorAll('button'), ...document.querySelectorAll('a')];
                for (const el of all) {{
                    const t = (el.innerText || '').toLowerCase();
                    const a = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (t.includes('upload') || t.includes('загруз') || t.includes('файл') ||
                        t.includes('компьютер') || t.includes('с устройства') ||
                        a.includes('upload') || a.includes('загруз')) {{
                        el.click(); return 'clicked: ' + (el.innerText || el.getAttribute('aria-label'));
                    }}
                }}
                return 'not_found';
            }})()""")
            print(f"  click result: {r2}")
            await asyncio.sleep(2)
            await c.shot(SCRATCH/"gemini_4_after_upload_click.jpg")
            fi=await c.ev("Array.from(document.querySelectorAll('input[type=file]')).map(i=>({accept:i.accept, hidden:i.hidden, multiple:i.multiple, parent:i.parentElement.tagName}))")
            print(f"  file inputs now: {fi}")

        print("\n=== DONE ===")

if __name__=="__main__":
    try: asyncio.run(main())
    except Exception as e:
        import traceback; print(f"FATAL: {e}"); traceback.print_exc()
