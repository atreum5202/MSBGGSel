export async function loadEngines(ctx) {
  if (ctx._enginesLoaded) return ctx._enginesLoaded;

  const engines = { cloakbrowser: null, patchright: null };

  // Patchright загружаем первым — он движок по умолчанию (AUTO).
  // CloakBrowser используется только при явном profile.engine = 'cloakbrowser'.
  try {
    const pr = await import('patchright');
    engines.patchright = pr.chromium ? pr : pr.default || null;
  } catch (err) {
    ctx.logger.warn({ err: err.message }, 'patchright engine unavailable');
  }

  try {
    engines.cloakbrowser = await import('cloakbrowser');
  } catch (err) {
    ctx.logger.warn({ err: err.message }, 'cloakbrowser engine unavailable');
  }

  if (!engines.patchright && !engines.cloakbrowser) {
    throw new Error('No stealth engine available. Install `patchright` and/or `cloakbrowser`.');
  }

  ctx._enginesLoaded = engines;
  return engines;
}
