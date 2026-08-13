"""scratch/gemini_explore5.py — правильный flow: focus → + → upload file → setFileInputFiles → type → send."""
from __future__ import annotations
import asyncio, base64, json, os, pathlib, sys, time
import httpx, websockets

ML_BASE="http://127.0.0.1:40000"
ML_ID="1716740459457616"
ML_KEY="8afb02927e724b6caadc6363f13f3c61"
SCRATCH=pathlib.Path("scratch"); SCRATCH.mkdir(exist_ok=True)
LOG=SCRATCH/"gemini_explore5.log"

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

def find_test_image():
    for d in (pathlib.Path("~/Desktop").expanduser(),
              pathlib.Path("C:/Users/Atreum/Desktop")):
        if not d.exists(): continue
        for ext in (".jpg",".jpeg",".png"):
            c=list(d.glob(f"*{ext}"))
            if c: return c[0]
    return None

async def get_profile():
    async with httpx.AsyncClient(timeout=20,trust_env=False) as c:
        r=await c.post(f"{ML_BASE}/api/env/page",json={"pageNo":1,"pageSize":3},
            headers={"X-Api-Id":ML_ID,"X-Api-Key":ML_KEY})
        d=r.json().get("data") or {}
        items=d.get("list") or d.get("dataList") or []
        if not items: raise RuntimeError("no profiles")
        it=items[0]
        return str(it.get("envId") or it.get("id"))

async def start(p):
    async with httpx.AsyncClient(timeout=60,trust_env=False) as c:
        r=await c.post(f"{ML_BASE}/api/env/start",json={"envId":p,"isHeadless":False,"cdpEvasion":True},
            headers={"X-Api-Id":ML_ID,"X-Api-Key":ML_KEY})
        return str(r.json()["data"]["debugPort"])

class CDP:
    def __init__(self,ws):
        self.ws=ws; self._id=0; self.events=[]
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
            if "method" in msg: self.events.append(msg)
    async def ev(self,e,t=15.0,await_promise=False):
        r=await self.send("Runtime.evaluate",{"expression":e,"returnByValue":True,"awaitPromise":await_promise},t)
        if "exceptionDetails" in r: return {"__err__":r["exceptionDetails"]}
        return r.get("result",{}).get("value")
    async def shot(self,path):
        r=await self.send("Page.captureScreenshot",{"format":"jpeg","quality":80},15.0)
        pathlib.Path(path).write_bytes(base64.b64decode(r["data"]))
    async def dom(self):
        return await self.ev(r"""(() => {
            const desc = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return {tag:el.tagName, id:el.id||null, aria:el.getAttribute('aria-label'),
                    text:(el.innerText||'').trim().substring(0,60),
                    cls:(typeof el.className==='string'?el.className:'').substring(0,100),
                    rect:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
                    visible: r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none' && +s.opacity>0,
                    disabled: el.disabled||null,
                };
            };
            return {
                fileInputs: Array.from(document.querySelectorAll('input[type=file]')).map(desc),
                contentEditables: Array.from(document.querySelectorAll('[contenteditable="true"]')).map(desc),
                buttons: Array.from(document.querySelectorAll('button')).map(desc).filter(b=>b.visible),
                matIcons: Array.from(document.querySelectorAll('mat-icon')).slice(0,30).map(i=>i.textContent||i.getAttribute('font-icon')),
                overlays: Array.from(document.querySelectorAll('.cdk-overlay-pane, .mat-mdc-menu-panel, [role=menu]')).map(desc),
            };
        })()""")

async def main():
    sys.stdout=Tee(LOG,sys.stdout)
    sys.stderr=Tee(LOG,sys.stderr)
    p=await get_profile(); dp=await start(p)
    test_img=find_test_image()
    print(f"profile={p} dp={dp} img={test_img}")

    async with httpx.AsyncClient(timeout=10,trust_env=False) as c:
        tabs=(await c.get(f"http://127.0.0.1:{dp}/json/list")).json()
    tab=[t for t in tabs if t.get("type")=="page" and t.get("url","").startswith("http")][0]
    print(f"tab: {tab['url']}")
    async with websockets.connect(tab["webSocketDebuggerUrl"],max_size=50*1024*1024,ping_interval=20) as ws:
        c=CDP(ws)
        await c.send("Page.enable"); await c.send("DOM.enable"); await c.send("Runtime.enable")

        # navigate
        await c.send("Page.navigate",{"url":"https://gemini.google.com/app"})
        for i in range(30):
            await asyncio.sleep(1)
            v=await c.ev("!!document.querySelector('.ql-editor[contenteditable=\"true\"]')")
            if v: print(f"loaded {i+1}s"); break
        await c.shot(SCRATCH/"g5_step0_loaded.jpg")

        # STEP 1: Focus input → should reveal bottom row with + button
        print("\n[STEP 1] Focus input area")
        await c.ev(r"""(() => {
            const el = document.querySelector('.ql-editor[contenteditable="true"]');
            if (!el) return 'no_editor';
            el.focus();
            // Click in case focus alone isn't enough
            el.click();
            // Dispatch a click event
            const rect = el.getBoundingClientRect();
            const ev = new MouseEvent('mousedown', {bubbles:true, clientX:rect.x+10, clientY:rect.y+10});
            el.dispatchEvent(ev);
            return 'focused';
        })()""")
        await asyncio.sleep(1.5)
        await c.shot(SCRATCH/"g5_step1_focused.jpg")
        d=await c.dom()
        plus_btns=[b for b in d.get("buttons",[]) if "добав" in (b.get("aria") or "").lower() or "upload" in (b.get("aria") or "").lower() or "attach" in (b.get("aria") or "").lower()]
        print(f"  + buttons found: {json.dumps(plus_btns, ensure_ascii=False)}")
        if not plus_btns:
            print(f"  ALL visible buttons: {json.dumps([{'aria':b.get('aria'),'text':b.get('text'),'cls':b.get('cls')} for b in d.get('buttons',[])], ensure_ascii=False)}")
            # Click on the input wrapper / parent to expand the bottom row
            print("\n  [alt] Click on parent container of input to expand UI")
            await c.ev(r"""(() => {
                const el = document.querySelector('.ql-editor[contenteditable="true"]');
                if (!el) return 'no_editor';
                let parent = el.parentElement;
                for (let i=0; i<5; i++) {
                    if (parent.className && parent.className.includes('input-area')) break;
                    parent = parent.parentElement;
                }
                parent.click();
                return parent.className;
            })()""")
            await asyncio.sleep(1.5)
            await c.shot(SCRATCH/"g5_step1b_input_area.jpg")
            d=await c.dom()
            plus_btns=[b for b in d.get("buttons",[]) if "добав" in (b.get("aria") or "").lower() or "upload" in (b.get("aria") or "").lower()]
            print(f"  + buttons after parent click: {json.dumps(plus_btns, ensure_ascii=False)}")

        # STEP 2: Click Добавить файлы
        if plus_btns:
            print("\n[STEP 2] Click Добавить файлы")
            await c.ev(r"""(() => {
                const b = document.querySelector('button[aria-label="Добавить файлы"]');
                if (b) b.click();
                return !!b;
            })()""")
            await asyncio.sleep(2)
            await c.shot(SCRATCH/"g5_step2_menu.jpg")
            d=await c.dom()
            print(f"  overlays: {json.dumps(d.get('overlays'), ensure_ascii=False, default=str)}")
            print(f"  menu-related buttons: {json.dumps([{'aria':b.get('aria'),'text':b.get('text'),'cls':b.get('cls')[:50]} for b in d.get('buttons',[]) if b.get('text') or 'menu' in (b.get('cls') or '').lower()], ensure_ascii=False)}")

        # STEP 3: Click "Upload file" / first menu item
        print("\n[STEP 3] Click menu item (Upload / Files)")
        r=await c.ev(r"""(() => {
            // Ищем пункты меню в overlay
            const overlays = document.querySelectorAll('.cdk-overlay-pane button, [role=menuitem]');
            const all = [...overlays, ...document.querySelectorAll('button')];
            const candidates = [];
            for (const el of all) {
                const t = (el.innerText || '').toLowerCase().trim();
                const a = (el.getAttribute('aria-label') || '').toLowerCase();
                if (t.includes('upload') || t.includes('загруз') || t.includes('файл') ||
                    t.includes('с компьютер') || t.includes('with computer') || t.includes('from device') ||
                    t.includes('with this device') || t.includes('drive') || t.includes('диск') ||
                    a.includes('upload') || a.includes('загруз')) {
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

        if isinstance(r,list) and r:
            # Кликаем первого
            await c.ev(r"""(() => {
                const all = [...document.querySelectorAll('.cdk-overlay-pane button, [role=menuitem]'),
                             ...document.querySelectorAll('button')];
                for (const el of all) {
                    const t = (el.innerText || '').toLowerCase();
                    const a = (el.getAttribute('aria-label') || '').toLowerCase();
                    if (t.includes('upload') || t.includes('загруз') || t.includes('файл') ||
                        t.includes('с компьютер') || t.includes('with computer') || t.includes('from device') ||
                        t.includes('drive') || t.includes('диск') ||
                        a.includes('upload') || a.includes('загруз')) {
                        el.click();
                        return 'clicked: ' + (el.innerText || el.getAttribute('aria-label'));
                    }
                }
                return 'not_found';
            })()""")
            await asyncio.sleep(2)
            await c.shot(SCRATCH/"g5_step3_after_upload.jpg")
            fi=await c.ev("Array.from(document.querySelectorAll('input[type=file]')).map(i=>({accept:i.accept, hidden:i.hidden, multiple:i.multiple, parentClass:i.parentElement.className.substring(0,80), rect:(()=>{const r=i.getBoundingClientRect();return{w:Math.round(r.width),h:Math.round(r.height)}})()}))")
            print(f"  file inputs now: {json.dumps(fi, ensure_ascii=False)}")

        # STEP 4: Type prompt
        print("\n[STEP 4] Type prompt")
        await c.ev(r"""(() => {
            const el = document.querySelector('.ql-editor[contenteditable="true"]');
            if (el) { el.focus(); el.innerHTML = ''; el.click(); }
            return !!el;
        })()""")
        prompt="Опиши это фото кратко"
        await c.send("Input.insertText",{"text":prompt},20.0)
        await asyncio.sleep(1)
        await c.shot(SCRATCH/"g5_step4_typed.jpg")
        d=await c.dom()
        # Ищем Send-кнопку теперь
        send_btns=[b for b in d.get("buttons",[]) if not b.get("disabled") and (
            (b.get("aria") and any(k in b["aria"].lower() for k in ["send","отправ","submit"])) or
            "send" in (b.get("cls") or "").lower() or
            (b.get("text") and any(k in b["text"].lower() for k in ["send","отправ","↑","➤"]))
        )]
        print(f"  send candidates: {json.dumps(send_btns, ensure_ascii=False)}")

        # STEP 5: Click Send
        if send_btns:
            print(f"\n[STEP 5] Click Send: {send_btns[0].get('aria') or send_btns[0].get('text') or send_btns[0].get('cls')[:40]}")
            await c.ev(r"""(() => {
                const buttons = Array.from(document.querySelectorAll('button'));
                for (const b of buttons) {
                    const aria = (b.getAttribute('aria-label') || '').toLowerCase();
                    const cls = (b.className || '').toLowerCase();
                    if ((aria.includes('send') || aria.includes('отправ') || cls.includes('send')) && !b.disabled) {
                        b.click();
                        return 'clicked';
                    }
                }
                return 'not_found';
            })()""")
            await asyncio.sleep(3)
            await c.shot(SCRATCH/"g5_step5_sent.jpg")

        # STEP 6: Wait & poll for image
        print("\n[STEP 6] Wait for response up to 90s")
        for i in range(18):
            await asyncio.sleep(5)
            d=await c.dom()
            big_imgs=[b for b in d.get("fileInputs",[])]  # not relevant, just to keep d
            st=await c.ev(r"""(() => ({
                largeImg: Array.from(document.querySelectorAll('img')).filter(i=>{const r=i.getBoundingClientRect();return r.width>256&&r.height>256;}).length,
                buttonCount: document.querySelectorAll('button').length,
                error: Array.from(document.querySelectorAll('*')).find(e=>{
                    const t = (e.innerText || '').toLowerCase();
                    return t.includes('что-то пошло') || t.includes('(1060)') || t.includes('error');
                }) ? (Array.from(document.querySelectorAll('*')).find(e=>{
                    const t = (e.innerText || '').toLowerCase();
                    return t.includes('что-то пошло') || t.includes('(1060)');
                }).innerText.substring(0,200)) : null,
                bodySample: (document.body.innerText || '').substring(0, 300),
            }))()""")
            print(f"  T+{(i+1)*5:>3}с: {st}")
            await c.shot(SCRATCH/f"g5_wait_{(i+1)*5:03d}.jpg")
            if st.get("largeImg",0) > 0:
                print("  ✅ large image appeared!")
                break
            if st.get("error"):
                print(f"  ❌ ERROR: {st['error']}")
                break

        print("\n=== DONE ===")

if __name__=="__main__":
    try: asyncio.run(main())
    except Exception as e:
        import traceback; print(f"FATAL: {e}"); traceback.print_exc()
