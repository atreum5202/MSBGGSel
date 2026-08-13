export default async function botSannysoft({ page }) {
  await page.goto('https://bot.sannysoft.com/', {
    waitUntil: 'networkidle',
    timeout: 60_000,
  });
  const results = await page.evaluate(() => {
    const rows = [];
    for (const tr of document.querySelectorAll('table tr')) {
      const cells = tr.querySelectorAll('td');
      if (cells.length < 2) continue;
      const name = cells[0].innerText.trim();
      const value = cells[1].innerText.trim();
      const cls = cells[1].className || '';
      const status = /passed/i.test(cls) ? 'pass' : /failed/i.test(cls) ? 'fail' : 'info';
      rows.push({ name, value, status });
    }
    return rows;
  });
  const passed = results.filter((r) => r.status === 'pass').length;
  const failed = results.filter((r) => r.status === 'fail').length;
  return { passed, failed, total: results.length, results };
}
