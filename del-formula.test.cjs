'use strict';
/* 自定义公式删除回归：2026-10-09 用户「删除不了 + 确认卡片做好看」。
   跑法：node desktop/del-formula.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const HTML = path.join(__dirname, 'calculator.html');

(async () => {
  const html = fs.readFileSync(HTML, 'utf8');
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto('data:text/html;charset=utf-8,' + encodeURIComponent(html), { waitUntil: 'load' });
  await p.waitForTimeout(500);

  const r = await p.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    // 注入一个测试公式
    customFormulas.push({ name: '测试公式', expr: '1+1', display: '1+1【测试公式】' });
    // 打开公式下拉
    const panel = activePanel || panels[0];
    toggleFormulaDropdown(panel);
    await sleep(200);

    const n0 = customFormulas.length;
    const dropdown = document.getElementById('formulaDropdown');

    // 点删除按钮
    const delBtn = dropdown.querySelector('.fx-del-btn');
    if (!delBtn) return { err: '没渲染出删除按钮' };
    delBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    await sleep(100);

    // 确认卡片应出现
    const card = dropdown.querySelector('.fx-confirm-card.show');
    if (!card) return { err: '确认卡片没展开', n0 };

    // 点确认删除
    const yes = card.querySelector('.fx-confirm-yes');
    yes.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
    await sleep(200);

    return { n0, n1: customFormulas.length, cardGone: !dropdown.querySelector('.fx-confirm-card.show'), dropdownHidden: !dropdown.classList.contains('show') };
  });

  await b.close();

  if (r.err) { console.log('[FAIL] ' + r.err); process.exit(1); }
  assert.equal(r.n1, r.n0 - 1, '删除后 customFormulas 应少一个');
  assert.equal(r.cardGone, true, '确认卡片应消失');
  assert.equal(r.dropdownHidden, true, '下拉应关闭');
  console.log('[OK] 自定义公式删除流程正常：确认卡片 → 删除 → 关闭下拉');
})().catch(e => { console.error('[FAIL] ' + (e && e.message || e)); process.exit(1); });
