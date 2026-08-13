const CHROME_MAJORS = ['135', '136', '137', '138'];

const UA = {
  win: (v) => `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${v}.0.0.0 Safari/537.36`,
  mac: (v) => `Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${v}.0.0.0 Safari/537.36`,
  lnx: (v) => `Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/${v}.0.0.0 Safari/537.36`,
};

const VIEWPORTS = [
  { width: 1920, height: 1080 },
  { width: 1680, height: 1050 },
  { width: 1600, height: 900 },
  { width: 1536, height: 864 },
  { width: 1440, height: 900 },
  { width: 1366, height: 768 },
];

const TZ_LOCALE = [
  ['America/New_York', 'en-US'],
  ['America/Chicago', 'en-US'],
  ['America/Los_Angeles', 'en-US'],
  ['Europe/London', 'en-GB'],
  ['Europe/Berlin', 'de-DE'],
  ['Europe/Paris', 'fr-FR'],
  ['Europe/Moscow', 'ru-RU'],
  ['Asia/Tokyo', 'ja-JP'],
  ['Asia/Singapore', 'en-SG'],
  ['Australia/Sydney', 'en-AU'],
  ['Asia/Yerevan', 'hy-AM'],
  ['Asia/Tbilisi', 'ka-GE'],
  ['Europe/Istanbul', 'tr-TR'],
  ['Asia/Baku', 'az-AZ'],
];

/**
 * Строит правильный Accept-Language HTTP-заголовок из массива языков.
 * ['hy-AM', 'hy', 'ru', 'en-US', 'en'] → 'hy-AM,hy;q=0.9,ru;q=0.8,en-US;q=0.7,en;q=0.6'
 */
export function buildAcceptLanguage(languages) {
  if (!languages || !languages.length) return 'en-US,en;q=0.9';
  return languages.map((lang, i) =>
    i === 0 ? lang : lang + ';q=' + (Math.max(0.1, 1.0 - i * 0.1)).toFixed(1)
  ).join(',');
}

const WEBGL_VENDORS = [
  'Intel Inc.',
  'NVIDIA Corporation',
  'AMD',
  'Qualcomm',
  'Apple',
];

const WEBGL_RENDERERS = [
  'Intel Iris OpenGL Engine',
  'NVIDIA GeForce RTX 3060',
  'AMD Radeon RX 580',
  'Adreno 650',
  'Apple GPU',
];

const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

export function generateFingerprint({ platform } = {}) {
  const chromeVersion = pick(CHROME_MAJORS);
  const plat = platform || pick(['Win32', 'Win32', 'MacIntel', 'Linux x86_64']);
  const userAgent =
    plat === 'MacIntel' ? UA.mac(chromeVersion)
    : plat === 'Linux x86_64' ? UA.lnx(chromeVersion)
    : UA.win(chromeVersion);

  const [timezone, locale] = pick(TZ_LOCALE);
  const viewport = pick(VIEWPORTS);
  const webglVendor = pick(WEBGL_VENDORS);
  const webglRenderer = pick(WEBGL_RENDERERS);
  
  return {
    userAgent,
    platform: plat,
    chromeVersion,
    timezone,
    locale,
    viewport,
    languages: [locale, locale.split('-')[0]],
    hardwareConcurrency: pick([4, 8, 8, 12, 16]),
    deviceMemory: pick([4, 8, 8, 16]),
    // Advanced fingerprinting
    webglVendor,
    webglRenderer,
    webgl2: true,
    webglAntialias: true,
    maxTouchPoints: plat === 'Win32' ? 0 : pick([0, 5]),
    screenColorDepth: 24,
    screenPixelDepth: 24,
    doNotTrack: null,
    pdfViewerEnabled: true,
    plugins: [
      { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer' },
      { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai' },
      { name: 'Native Client', filename: 'internal-nacl-plugin' },
    ],
    mimeTypes: [
      { type: 'application/pdf', suffixes: 'pdf' },
      { type: 'application/x-google-chrome-pdf', suffixes: 'pdf' },
      { type: 'application/x-nacl', suffixes: '' },
      { type: 'application/x-pnacl', suffixes: '' },
    ],
    // WebRTC protection
    webRTC: {
      enabled: true,
      ipHandlingPolicy: 'default_public_interface_only',
      multicastHandling: 'disable',
      nonProxiedUdpEnabled: false,
    },
    // AudioContext fingerprinting
    audio: {
      spoof: true,
      noiseLevel: 0.001,
    },
    // Canvas fingerprinting
    canvas: {
      spoof: true,
      noiseLevel: 0.5,
    },
    // Font fingerprinting
    fonts: {
      spoof: true,
      hideSystemFonts: false,
    },
  };
}

export async function installFingerprintInitScripts(context, fp) {
  const seed        = hash(fp?.userAgent || Math.random().toString());
  const platform    = JSON.stringify(fp?.platform || 'Win32');
  const languages   = fp?.languages || ['en-US', 'en'];
  const languagesJs = JSON.stringify(languages);
  const language0   = JSON.stringify(languages[0] || 'en-US');
  const hc          = fp?.hardwareConcurrency || 8;
  const dm          = fp?.deviceMemory || 8;
  const webglVendor    = JSON.stringify(fp?.webglVendor   || 'Intel Inc.');
  const webglRenderer  = JSON.stringify(fp?.webglRenderer || 'Intel Iris OpenGL Engine');
  const maxTouchPoints = fp?.maxTouchPoints ?? 0;
  const canvasNoise = fp?.canvas?.noiseLevel ?? 0.5;
  const audioNoise  = fp?.audio?.noiseLevel  ?? 0.001;
  const webRTCEnabled = fp?.webRTC?.enabled !== false;
  const webRTCPolicy  = JSON.stringify(fp?.webRTC?.ipHandlingPolicy || 'default_public_interface_only');
  const doNotTrack  = fp?.doNotTrack != null ? JSON.stringify(String(fp.doNotTrack)) : 'null';
  const pdfEnabled  = fp?.pdfViewerEnabled !== false ? 'true' : 'false';
  const sw  = fp?.viewport?.width  || 1536;
  const sh  = fp?.viewport?.height || 864;
  const dpr = fp?.devicePixelRatio || 1;
  const plugins   = fp?.plugins || [
    { name: 'Chrome PDF Plugin',  filename: 'internal-pdf-viewer',            description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',  filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',      filename: 'internal-nacl-plugin',            description: '' },
  ];
  const mimeTypes = fp?.mimeTypes || [
    { type: 'application/pdf',                suffixes: 'pdf', description: '' },
    { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: '' },
    { type: 'application/x-nacl',              suffixes: '',    description: '' },
    { type: 'application/x-pnacl',             suffixes: '',    description: '' },
  ];
  const pluginsJs   = JSON.stringify(plugins);
  const mimeTypesJs = JSON.stringify(mimeTypes);

  const webRTCBlock = webRTCEnabled
    ? `(function(){try{if(window.chrome&&chrome.privacy&&chrome.privacy.network){chrome.privacy.network.ipHandlingPolicy={value:${webRTCPolicy}};}}catch(e){}})();`
    : '';

  const script = [
    `(function(){`,
    `'use strict';`,
    `var _s=${seed}>>>0;`,
    `function rnd(){_s=(_s*1664525+1013904223)>>>0;return _s/4294967296;}`,
    `function def(o,p,v){try{Object.defineProperty(o,p,{get:typeof v==='function'?v:function(){return v;},configurable:true,enumerable:true});}catch(e){}}`,
    // ── automation marker removal
    `try{delete window.__playwright_coverage__;}catch(e){}`,
    `try{delete window.__pw_manual;}catch(e){}`,
    `try{delete window.__pw_coverage__;}catch(e){}`,
    `try{Object.getOwnPropertyNames(window).forEach(function(k){if(/^(__cdc|cdc_)/i.test(k)){try{delete window[k];}catch(e){}}});}catch(e){}`,
    // ── chrome shim
    `(function(){try{if(window.chrome)return;var c={app:{isInstalled:false},runtime:{id:undefined},csi:function(){},loadTimes:function(){var t=Date.now()/1000;return{requestTime:t-1.5,startLoadTime:t-1.2,commitLoadTime:t-1.0,finishDocumentLoadTime:0,finishLoadTime:0,firstPaintTime:0,firstPaintAfterLoadTime:0,navigationType:'Other',wasFetchedViaSpdy:true,wasNpnNegotiated:true,npnNegotiatedProtocol:'h2',wasAlternateProtocolAvailable:false,connectionInfo:'h2'};}};def(window,'chrome',c);}catch(e){}})();`,
    // ── navigator
    `def(navigator,'hardwareConcurrency',${hc});`,
    `def(navigator,'deviceMemory',${dm});`,
    `def(navigator,'platform',${platform});`,
    `def(navigator,'language',${language0});`,
    `def(navigator,'languages',${languagesJs});`,
    `def(navigator,'vendor','Google Inc.');`,
    `def(navigator,'maxTouchPoints',${maxTouchPoints});`,
    `def(navigator,'doNotTrack',${doNotTrack});`,
    `def(navigator,'pdfViewerEnabled',${pdfEnabled});`,
    `try{if(!navigator.connection&&!navigator.mozConnection){def(navigator,'connection',{rtt:50+Math.floor(rnd()*30),downlink:8+Math.floor(rnd()*8),effectiveType:'4g',saveData:false,onchange:null});}}catch(e){}`,
    // ── screen + DPR
    `def(screen,'colorDepth',24);`,
    `def(screen,'pixelDepth',24);`,
    `def(screen,'width',${sw});`,
    `def(screen,'height',${sh});`,
    `def(screen,'availWidth',${sw});`,
    `def(screen,'availHeight',${sh - 40});`,
    `def(window,'devicePixelRatio',${dpr});`,
    // ── plugins + mimeTypes
    `(function(){`,
    `try{`,
    `  var _pl=${pluginsJs};`,
    `  var _mt=${mimeTypesJs};`,
    `  function mkList(arr,tag){var o=Object.create(null);arr.forEach(function(x,i){o[i]=x;o[x.name||x.type]=x;});o.length=arr.length;o.item=function(i){return arr[i]||null;};o.namedItem=function(n){return o[n]||null;};o.refresh=function(){};o[Symbol.iterator]=function(){var i=0;return{next:function(){return i<arr.length?{value:arr[i++],done:false}:{done:true};}};};return o;}`,
    `  def(navigator,'plugins',   mkList(_pl,'PluginArray'));`,
    `  def(navigator,'mimeTypes', mkList(_mt,'MimeTypeArray'));`,
    `}catch(e){}`,
    `})();`,
    // ── permissions (geolocation + notifications)
    `(function(){try{var _oq=navigator.permissions.query.bind(navigator.permissions);navigator.permissions.__proto__.query=function(p){if(p&&p.name==='geolocation'){return Promise.resolve({state:'granted',onchange:null});}if(p&&p.name==='notifications'){var s=(typeof Notification!=='undefined'&&Notification.permission==='granted')?'granted':'default';return Promise.resolve({state:s,onchange:null});}return _oq(p);};}catch(e){}})();`,
    // ── canvas noise
    `(function(){var o=HTMLCanvasElement.prototype.toDataURL;HTMLCanvasElement.prototype.toDataURL=function(){try{var c=this.getContext('2d');if(c&&this.width>0&&this.height>0){var d=c.getImageData(0,0,this.width,this.height);var n=${canvasNoise};for(var i=3;i<d.data.length;i+=4){d.data[i]=Math.max(0,Math.min(255,d.data[i]+(rnd()<0.5?-n:n)));}c.putImageData(d,0,0);}}catch(e){}return o.apply(this,arguments);};HTMLCanvasElement.prototype.toDataURL.toString=function(){return o.toString();};})();`,
    // ── WebGL
    `(function(){function p(proto){if(!proto)return;var o=proto.getParameter;proto.getParameter=function(x){if(x===37445)return ${webglVendor};if(x===37446)return ${webglRenderer};return o.call(this,x);};proto.getParameter.toString=function(){return o.toString();};}try{p(WebGLRenderingContext.prototype);}catch(e){}try{p(WebGL2RenderingContext.prototype);}catch(e){}})();`,
    // ── audio noise
    `(function(){try{var p1=AnalyserNode.prototype,o1=p1.getFloatFrequencyData;p1.getFloatFrequencyData=function(a){o1.call(this,a);var n=${audioNoise};for(var i=0;i<a.length;i++)a[i]+=(rnd()-0.5)*n;};p1.getFloatFrequencyData.toString=function(){return o1.toString();};}catch(e){}try{var p2=AudioBuffer.prototype,o2=p2.getChannelData;p2.getChannelData=function(){var a=o2.apply(this,arguments);var s=Math.max(1,Math.floor(a.length/100));var n=${audioNoise};for(var i=0;i<a.length;i+=s)a[i]+=(rnd()-0.5)*n;return a;};p2.getChannelData.toString=function(){return o2.toString();};}catch(e){}})();`,
    // ── WebRTC
    webRTCBlock,
    // ── Font
    `(function(){try{var _oFF=window.FontFace;window.FontFace=function(f,s,d){return new _oFF(f,s,d);};window.FontFace.toString=function(){return _oFF.toString();};}catch(e){}})();`,
    `})();`,
  ].join('\n');

  await context.addInitScript(script);
}

export async function refreshFingerprint(context, page, newFp, { reload = false } = {}) {
  const target = page || (context.pages?.()?.[0] ?? null);
  if (!target) throw new Error('refreshFingerprint: no page');

  const client = await context.newCDPSession(target);

  if (newFp.userAgent) {
    const acceptLang = buildAcceptLanguage(newFp.languages || (newFp.locale ? [newFp.locale] : []));
    await client.send('Emulation.setUserAgentOverride', {
      userAgent: newFp.userAgent,
      acceptLanguage: acceptLang,
      platform: newFp.platform,
    });
  }
  if (newFp.timezone) {
    try {
      await client.send('Emulation.setTimezoneOverride', { timezoneId: newFp.timezone });
    } catch (e) {
      console.warn('setTimezoneOverride failed:', e.message);
    }
  }
  if (newFp.locale) {
    try {
      await client.send('Emulation.setLocaleOverride', { locale: newFp.locale });
    } catch (e) {
      console.warn('setLocaleOverride failed:', e.message);
    }
  }
  if (newFp.viewport?.width && newFp.viewport?.height) {
    try {
      await target.setViewportSize(newFp.viewport);
    } catch (e) {
      console.warn('setViewportSize failed:', e.message);
    }
  }

  try {
    await installFingerprintInitScripts(context, newFp);
  } catch {}

  try {
    await client.detach();
  } catch {}

  if (reload) {
    try {
      await target.reload({ waitUntil: 'domcontentloaded', timeout: 30_000 });
    } catch {}
  }

  return { applied: true, fingerprint: newFp };
}

function hash(str) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}
