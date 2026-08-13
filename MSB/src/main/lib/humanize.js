let cachedWayfern = null;

async function loadWayfern() {
  if (cachedWayfern !== null) return cachedWayfern;
  try {
    cachedWayfern = await import('wayfern');
  } catch (err) {
    console.warn('[humanize] wayfern unavailable:', err.message);
    cachedWayfern = false;
  }
  return cachedWayfern;
}

export async function attachHumanize(page) {
  const wf = await loadWayfern();
  if (!wf) {
    installFallbackDelays(page);
    return null;
  }
  try {
    return new wf.Wayfern(page, { hooks: {} });
  } catch (err) {
    console.warn('[humanize] Wayfern init failed:', err.message);
    installFallbackDelays(page);
    return null;
  }
}

function installFallbackDelays(page) {
  const jitter = (min, max) =>
    new Promise((r) => setTimeout(r, min + Math.random() * (max - min)));

  const origClick = page.click.bind(page);
  page.click = async (selector, opts = {}) => {
    await jitter(80, 240);
    return origClick(selector, { delay: 50 + Math.random() * 120, ...opts });
  };

  const origType = page.type.bind(page);
  page.type = async (selector, text, opts = {}) => {
    await jitter(120, 300);
    return origType(selector, text, { delay: 60 + Math.random() * 90, ...opts });
  };

  const origFill = page.fill.bind(page);
  page.fill = async (selector, value, opts = {}) => {
    await jitter(60, 180);
    return origFill(selector, value, opts);
  };
}

export function humanDelay(minMs = 400, maxMs = 1200) {
  return new Promise((r) => setTimeout(r, minMs + Math.random() * (maxMs - minMs)));
}
