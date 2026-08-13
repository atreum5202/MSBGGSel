import { WebContentsView } from 'electron';
import { DEFAULTS } from '../core/constants.js';

const widgets = new Map();

export function showWidget(mainWindow, url) {
  const entry = ensureWidget(mainWindow);
  entry.visible = true;
  entry.resize();
  entry.view.setVisible(true);
  if (url) entry.view.webContents.loadURL(url);
}

export function hideWidget(mainWindow) {
  const entry = widgets.get(mainWindow.id);
  if (!entry) return;
  entry.visible = false;
  entry.view.setVisible(false);
  if (entry.screencast) stopStealthScreencast(mainWindow);
}

export function navigateWidget(mainWindow, url) {
  const entry = ensureWidget(mainWindow);
  entry.view.webContents.loadURL(url);
}

export async function attachStealthScreencast(mainWindow, page) {
  const entry = ensureWidget(mainWindow);
  entry.visible = true;
  entry.view.setVisible(true);
  entry.resize();

  const html = SCREENCAST_HTML.replace('__TITLE__', JSON.stringify(page.url() || 'preview'));
  await entry.view.webContents.loadURL(
    `data:text/html;charset=utf-8;base64,${Buffer.from(html).toString('base64')}`
  );

  const client = await page.context().newCDPSession(page);
  await client.send('Page.startScreencast', {
    format: DEFAULTS.SCREENCAST_FORMAT,
    quality: DEFAULTS.SCREENCAST_QUALITY,
    everyNthFrame: 1,
  });
  const onFrame = async ({ data, sessionId }) => {
    try {
      await entry.view.webContents.executeJavaScript(
        `window.__msbFrame(${JSON.stringify(data)})`,
        true
      );
    } catch {}
    try {
      await client.send('Page.screencastFrameAck', { sessionId });
    } catch {}
  };
  client.on('Page.screencastFrame', onFrame);
  entry.screencast = { client, page, onFrame };
  return true;
}

export async function stopStealthScreencast(mainWindow) {
  const entry = widgets.get(mainWindow.id);
  if (!entry?.screencast) return;
  const { client, onFrame } = entry.screencast;
  try { await client.send('Page.stopScreencast'); } catch {}
  client.off?.('Page.screencastFrame', onFrame);
  try { await client.detach(); } catch {}
  entry.screencast = null;
}

function ensureWidget(mainWindow) {
  let entry = widgets.get(mainWindow.id);
  if (entry) return entry;

  const view = new WebContentsView({
    webPreferences: {
      contextIsolation: true,
      sandbox: true,
      partition: 'persist:msb-widget',
    },
  });
  view.setBackgroundColor('#00000000');
  mainWindow.contentView.addChildView(view);

  const resize = () => {
    if (!entry.visible) return;
    const { width, height } = mainWindow.getContentBounds();
    const sidebar = DEFAULTS.WIDGET_SIDEBAR;
    const header = DEFAULTS.WIDGET_HEADER;
    view.setBounds({
      x: sidebar,
      y: header,
      width: Math.max(0, width - sidebar),
      height: Math.max(0, height - header),
    });
  };

  entry = { view, visible: false, screencast: null, resize };
  widgets.set(mainWindow.id, entry);
  mainWindow.on('resize', resize);
  return entry;
}

const SCREENCAST_HTML = `<!doctype html>
<html><head><meta charset="utf-8"><title>Stealth preview</title>
<style>
html,body{margin:0;background:#000;height:100%;color:#ccc;font-family:sans-serif}
#wrap{position:absolute;inset:0;display:flex;align-items:center;justify-content:center}
img{max-width:100%;max-height:100%;object-fit:contain}
#hint{position:absolute;top:8px;left:8px;font-size:11px;color:#666}
</style></head><body>
<div id="wrap"><img id="frame" alt=""></div>
<div id="hint">stealth screencast · read-only preview</div>
<script>
const img=document.getElementById('frame');
window.__msbFrame=(b64)=>{img.src='data:image/jpeg;base64,'+b64;};
</script>
</body></html>`;
