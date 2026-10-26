const fs = require('fs');
const { chromium } = require('playwright');
(async () => {
  const html = fs.readFileSync('calculator.html', 'utf8');
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  await page.goto('data:text/html;charset=utf-8,' + encodeURIComponent(html), { waitUntil: 'load' });
  await page.waitForTimeout(800);
  // 关键 DOM 是否存在
  const hasTable = await page.evaluate(() => !!document.querySelector('#calcTable, table, .calc-grid, [id*="grid" i]'));
  const hasFxBtn = await page.evaluate(() => !!document.querySelector('[class*="fx"], button'));
  // 调导出（真运行时，不引 WPS）
  const exportOk = await page.evaluate(() => {
    try {
      if (!window.ForhanXlsx || !window.ForhanXlsx.buildCalcSheet) return 'NO_API';
      const bytes = window.ForhanXlsx.buildCalcSheet(
        { sheetName: 'S', rows: [{ kind: 'item', name: '混凝土', expr: '3*4', unit: 'm3', qty: 1 }] },
        { mode: 'wps' }
      );
      return bytes && bytes.length > 100 ? 'OK:' + bytes.length : 'EMPTY';
    } catch (e) { return 'THROW:' + e.message; }
  });
  console.log('DOM table present:', hasTable);
  console.log('interactive btn present:', hasFxBtn);
  console.log('export(buildCalcSheet) result:', exportOk);
  console.log('console/page errors:', errors.length ? JSON.stringify(errors.slice(0,8)) : 'NONE');
  await browser.close();
})().catch(e => { console.error('SMOKE FAIL', e); process.exit(1); });
