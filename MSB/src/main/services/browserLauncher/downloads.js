import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { DOWNLOADS_DIR } from './constants.js';

export function createDownloadHandler(profile, logger) {
  const safeProfileName = (profile.name || profile.id)
    .replace(/[\\/:*?"<>|]/g, '_')
    .slice(0, 40)
    .trim() || profile.id.slice(0, 8);

  // Сохраняем прямо в папку Downloads без подпапки по профилю
  const profileDownloadsDir = DOWNLOADS_DIR;

  try {
    mkdirSync(profileDownloadsDir, { recursive: true });
  } catch {

  }

  const attachDownloadHandler = (pg) => {
    pg.on('download', async (download) => {
      try {
        // suggestedFilename() может быть пустым при "Сохранить как" на странице —
        // в этом случае берём имя из URL, иначе fallback с датой
        let suggested = download.suggestedFilename();
        if (!suggested) {
          try {
            const u = new URL(download.url());
            const base = u.pathname.split('/').filter(Boolean).pop();
            suggested = base ? decodeURIComponent(base) : `download-${Date.now()}`;
          } catch {
            suggested = `download-${Date.now()}`;
          }
        }
        // Убираем недопустимые символы в имени файла
        suggested = suggested.replace(/[\\/:*?"<>|]/g, '_').slice(0, 200) || `download-${Date.now()}`;
        const savePath = path.join(profileDownloadsDir, suggested);
        await download.saveAs(savePath);
        logger.info(
          { profileId: profile.id, file: suggested, savePath, url: download.url() },
          'download saved',
        );
      } catch (err) {
        logger.warn({ profileId: profile.id, err: err.message }, 'download save failed');
        try { await download.cancel?.(); } catch { }
      }
    });
  };

  return { profileDownloadsDir, attachDownloadHandler };
}
