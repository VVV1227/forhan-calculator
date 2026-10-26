'use strict';
/* BUG-001 回归：柱墩卡「插入体积/表面积」后必须能 Ctrl+Z 撤销
   —— 2026-10-09 用户真机实报「柱墩插入完无法撤销」。
   根因：insertXzExpression 直接给 panel.input.value 赋值，绕过唯一会入撤销栈的
   spotWrite() → cellUndoSnap 为空 → Ctrl+Z 处理器两段都跳过 → 按了等于没按。
   本脚本断言「插前文本 → 插入 → Ctrl+Z → 回到插前文本」，且顺带守住：
     ① 插入内容仍正确（含连续插入自动补「+」的 10-07 行为）
     ② 撤销后结果行重算回落（不是只回文本、数还留着）
     ③ 主表插行也可撤销（同病根的第二病灶）
   跑法：node desktop/undo-xz.test.cjs   （须在 worktree 根或 desktop/ 下执行）*/
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const HTML = path.join(__dirname, 'calculator.html');

function extractScripts(html) {
  const out = [];
  const re = /<script[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html)) !== null) if (m[1].trim()) out.push(m[1]);
  return out;
}

(async () => {
  // 0) 语法门禁：先确认没写出 ES 版本问题（本项目要兼容老内核）
  const acorn = require('acorn');
  const html = fs.readFileSync(HTML, 'utf8');
  for (const ev of [2017, 2018, 2019, 2020]) {
    for (const code of extractScripts(html)) acorn.parse(code, { ecmaVersion: ev });
  }
  console.log('[OK] 语法 ES2017~2020 全通过');

  const b = await chromium.launch();
  const p = await b.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('data:text/html;charset=utf-8,' + encodeURIComponent(html), { waitUntil: 'load' });
  await p.waitForTimeout(600);

  const banners = await p.evaluate(() =>
    [...document.querySelectorAll('div')].filter(e => (e.textContent || '').startsWith('⚠ JS错误')).map(e => e.textContent));
  assert.equal(banners.length, 0, '页面不该有 JS 错误横幅: ' + JSON.stringify(banners));
  assert.deepEqual(errs, [], '不该有 pageerror');

  // 1) 柱墩插入 → Ctrl+Z 必须回到插前文本
  //    走真实按钮链路（不调内部函数），保证测的是用户点得到的那条路。
  const r1 = await p.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const ta = document.querySelector('#calcInput') || document.querySelector('.panel textarea') || document.querySelector('textarea');
    if (!ta) return { err: 'no textarea' };
    ta.focus();
    ta.value = '1.5';
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(120);
    const before = ta.value;

    // 打开柱墩卡并填出体积
    openXzCalc();
    await sleep(150);
    const set = (id, v) => {
      const el = document.getElementById(id);
      if (!el) return false;
      el.value = v;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    };
    /* 填法按 computeXz 的真实约束挑最少的一组：
       放坡角 90°（不放坡）+ H 手填 + 底口 b2/B2 → 顶口与斜段自动推出。
       ⚠ 字段 id 是 xz_H（不是 xz_h），改错就静默不出量 —— 按钮会一直 disabled。
       插完断言按钮已 enable，防止「测了个寂寞」。 */
    const filled = set('xz_ang', '90') && set('xz_H', '300') && set('xz_b2', '400') && set('xz_B2', '500');
    await sleep(250);

    // 点「插入体积」
    const btn = document.getElementById('xzInsertVol');
    if (!btn) return { err: 'no xzInsertVol', filled };
    if (btn.disabled) return { err: '插入按钮仍 disabled —— 填的数没凑出体积，测不到撤销', filled };
    btn.click();
    await sleep(200);
    const afterInsert = ta.value;

    // 快照确实入栈了吗
    const hasSnap = !!(window.cellUndoSnap && window.cellUndoSnap.el === ta);

    // 模拟真实 Ctrl+Z
    document.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'z', code: 'KeyZ', ctrlKey: true, bubbles: true, cancelable: true
    }));
    await sleep(200);
    return { before, afterInsert, afterUndo: ta.value, hasSnap, filled };
  });

  if (r1.err) {
    console.log('[FAIL] ' + r1.err);
    await b.close();
    process.exit(1);
  }
  assert.notEqual(r1.afterInsert, r1.before, '插入后文本应变化 —— 否则说明按钮没插进去，测不到撤销');
  assert.match(r1.afterInsert, /柱墩体积/, '插入内容应含【柱墩体积】标注，实际: ' + r1.afterInsert);
  assert.equal(r1.hasSnap, true, '插入后应留下撤销快照（cellUndoSnap.el 指向算式框）');
  assert.equal(r1.afterUndo, r1.before, 'Ctrl+Z 后必须回到插入前的文本，实际: ' + JSON.stringify({ before: r1.before, afterUndo: r1.afterUndo }));
  console.log('[OK] 柱墩插体积 → Ctrl+Z 回到插入前: "' + r1.before + '"');

  // 2) 撤销后结果行跟着回落（不是只回文本、数还留着）
  //    结果行没有固定 id，真实取法是 panel._ansEl（见 renderAnswer）—— 别再猜 #answerVal。
  const r2 = await p.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const ta = document.querySelector('#calcInput') || document.querySelector('.panel textarea') || document.querySelector('textarea');
    const ansOf = () => {
      const pn = activePanel || panels[0];
      return pn && pn._ansEl ? (pn._ansEl.textContent || '').trim() : '(no ansEl)';
    };
    ta.focus();
    ta.value = '0';
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(200);
    const emptyAns = ansOf();

    openXzCalc();
    await sleep(150);
    for (const [id, v] of [['xz_ang', '90'], ['xz_H', '400'], ['xz_b2', '600'], ['xz_B2', '700']]) {
      const el = document.getElementById(id);
      if (el) { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }
    }
    await sleep(250);
    document.getElementById('xzInsertVol').click();
    await sleep(300);
    const ansAfter = ansOf();

    document.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'z', code: 'KeyZ', ctrlKey: true, bubbles: true, cancelable: true
    }));
    await sleep(300);
    return { emptyAns, ansAfter, ansAfterUndo: ansOf(), text: ta.value };
  });
  assert.equal(r2.text, '0', '撤销后算式框应回到 0');
  assert.notEqual(r2.ansAfter, r2.emptyAns, '插入后结果行应有值，实际: ' + JSON.stringify(r2));
  assert.equal(r2.ansAfterUndo, r2.emptyAns, '撤销后结果行必须跟着回落，实际: ' + r2.ansAfterUndo);
  console.log('[OK] 撤销后结果行同步回落: "' + r2.ansAfter + '" → "' + r2.ansAfterUndo + '"');

  // 3) 连续插入仍自动补「+」（10-07 行为不许被撤销修复带坏）
  const r3 = await p.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const ta = document.querySelector('#calcInput') || document.querySelector('.panel textarea') || document.querySelector('textarea');
    ta.focus();
    ta.value = '1+1';
    ta.setSelectionRange(4, 4);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(150);
    openXzCalc();
    await sleep(120);
    for (const [id, v] of [['xz_ang', '90'], ['xz_H', '300'], ['xz_b2', '400'], ['xz_B2', '500']]) {
      const el = document.getElementById(id);
      if (el) { el.value = v; el.dispatchEvent(new Event('input', { bubbles: true })); }
    }
    await sleep(200);
    document.getElementById('xzInsertArea').click();
    await sleep(250);
    return { text: ta.value };
  });
  assert.match(r3.text, /\+\s*[\d.]+【柱墩表面积】/, '连续插入应自动补运算符，实际: ' + r3.text);
  console.log('[OK] 连续插入仍自动补「+」: ' + r3.text);

  // 4) 同病根第二病灶：主表插行也要能撤（appTip 挂了可撤回调 → Ctrl+Z 走 barUndoFn）
  const r4 = await p.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    if (typeof setView === 'function') setView('sheet');
    await sleep(300);
    const n0 = sheetRows.length;
    await sheetAddRow(null, 'item');
    await sleep(300);
    const n1 = sheetRows.length;
    // 走真实链路：Ctrl+Z（处理器里有 barUndoFn 分支），不直接调 barUndoFn
    document.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'z', code: 'KeyZ', ctrlKey: true, bubbles: true, cancelable: true
    }));
    await sleep(400);
    return { n0, n1, n2: sheetRows.length };
  });
  assert.equal(r4.n1, r4.n0 + 1, '插行后应多一行');
  assert.equal(r4.n2, r4.n0, 'Ctrl+Z 撤销插行后应回到原行数，实际: ' + r4.n2 + ' vs ' + r4.n0);
  console.log('[OK] 主表插行 Ctrl+Z 可撤: ' + r4.n0 + ' → ' + r4.n1 + ' → ' + r4.n2);

  // 5) 真源与桌面副本必须逐字节一致（打包读的是 desktop/ 那份；不同步 = 装了旧版）
  const fa = fs.readFileSync(HTML);
  const fb = fs.readFileSync(path.join(__dirname, '..', 'calculator.html'));
  assert.ok(fa.equals(fb), 'calculator.html 与 desktop/calculator.html 不一致 —— 打包会装旧版');
  console.log('[OK] 真源与 desktop 副本逐字节一致 (' + fa.length + ' 字节)');

  // 6) 快捷键在光标处插函数 → 隐式乘形态 `1+1trunc()` 必须能翻译成 Excel 公式
  //    （BUG-004：以前报「不支持的函数 trunc」→ 该行回落写数值 → WPS 看到裸数字）
  const r6 = await p.evaluate(async () => {
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    if (typeof setView === 'function') setView('calc');
    await sleep(250);
    const ta = activePanel.input;
    ta.focus();
    ta.value = '1+1';
    ta.setSelectionRange(4, 4);
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    await sleep(150);
    // 走真实快捷键链路
    ta.dispatchEvent(new KeyboardEvent('keydown', { key: '3', code: 'Digit3', ctrlKey: true, bubbles: true, cancelable: true }));
    await sleep(250);
    return { text: ta.value };
  });
  // ★ 2026-10-09 修复：Ctrl+3 改走 insertAtCursor(it.ins)（与公式下拉 2849 行一致），
  //   无框选时光标处插出 trunc(（光标落在括号内，准备输入参数），不再插 trunc()（带空参右括号）。
  assert.match(r6.text, /1\+1trunc\(/, 'Ctrl+3 应在光标处插出 trunc(，实际: ' + r6.text);
  const F = await p.evaluate(() => window.ForhanXlsx);
  const cases = [
    ['1+1trunc(2)', '1+1*TRUNC(2)'],
    ['2trunc(1.5)', '2*TRUNC(1.5)'],
    ['(1+1)trunc(1.5)', '(1+1)*TRUNC(1.5)'],
    ['1+1sqrt(4)', '1+1*SQRT(4)'],
    ['10log(2)', '10*LOG10(2)'],
    ['2(1.2+3.4)', '2*(1.2+3.4)']
  ];
  for (const [src, want] of cases) {
    const got = await p.evaluate(e => window.ForhanXlsx.exprToExcel(e), src);
    assert.equal(got.ok, true, src + ' 应能翻译，实际: ' + got.why);
    assert.equal(got.formula, want, src + ' 翻译结果不对');
  }
  console.log('[OK] 隐式乘形态的函数调用 ' + cases.length + ' 条全部翻译正确');

  // 7) 反向守护：真正无效的算式仍要拦住（别把「无参 TRUNC」也放过去）
  const r7 = await p.evaluate(() => window.ForhanXlsx.exprToExcel('1+1trunc()'));
  assert.equal(r7.ok, false, '无参 TRUNC 语义就是错的，不该放过（App 侧同样算不出）');
  assert.match(r7.why, /参数个数/, '失败原因应是参数个数，实际: ' + r7.why);
  console.log('[OK] 无参 TRUNC 仍被拦下: ' + r7.why);

  // 8) 导出的序号列不许引用纯文字行（BUG-005：MAX(上一行)+1 撞上分节行 → WPS 红色 <unknown>）
  //    做法：真导一份含「分节行/空行/明细行」的稿，解出 sheet1.xml 逐格核对。
  const rows = [
    { kind: 'section', name: '第一段', node: '', expr: '', unit: '' },
    { kind: 'item', name: '混凝土', node: 'C1', expr: '1+1trunc(2)', unit: 'm3', note: '' },
    { kind: 'blank', name: '', node: '', expr: '', unit: '' },
    { kind: 'item', name: '钢筋', node: 'C2', expr: '2*3', unit: 't', note: '' },
    { kind: 'item', name: '模板', node: 'C3', expr: '1+1sqrt(4)', unit: 'm2', note: '纯文字备注' }
  ];
  const b64 = await p.evaluate(async rr => {
    const bytes = await window.ForhanXlsx.buildCalcSheet({ title: 't', rows: rr }, { mode: 'wps' });
    const u8 = new Uint8Array(bytes);
    let bin = '';
    for (let i = 0; i < u8.length; i++) bin += String.fromCharCode(u8[i]);
    return btoa(bin);
  }, rows);
  const zlib = require('node:zlib');
  // 极简 zip 读取：只取 xl/worksheets/sheet1.xml（本项目导出用 store 模式，无压缩）
  const buf = Buffer.from(b64, 'base64');
  let sx = '';
  {
    // 扫本地文件头
    let off = 0;
    while (off < buf.length - 4) {
      const sig = buf.readUInt32LE(off);
      if (sig !== 0x04034b50) break;
      const method = buf.readUInt16LE(off + 8);
      const csize = buf.readUInt32LE(off + 18);
      const nlen = buf.readUInt16LE(off + 26);
      const elen = buf.readUInt16LE(off + 28);
      const name = buf.slice(off + 30, off + 30 + nlen).toString('utf8');
      const dataOff = off + 30 + nlen + elen;
      if (/xl\/worksheets\/sheet1\.xml$/.test(name)) {
        const raw = buf.slice(dataOff, dataOff + csize);
        sx = (method === 0 ? raw : zlib.inflateRawSync(raw)).toString('utf8');
        break;
      }
      off = dataOff + csize;
    }
  }
  assert.ok(sx.length > 0, '没解出 sheet1.xml —— zip 结构变了？');
  const cellOfRef = ref => {
    const m = sx.match(new RegExp('<c r="' + ref + '"([^>]*)>([\\s\\S]*?)</c>'));
    return m ? { attrs: m[1], body: m[2] } : null;
  };
  const fOf = ref => { const c = cellOfRef(ref); const m = c && c.body.match(/<f[^>]*>([\s\S]*?)<\/f>/); return m ? m[1] : ''; };
  const tOf = ref => { const c = cellOfRef(ref); const m = c && c.body.match(/<t[^>]*>([\s\S]*?)<\/t>/); return m ? m[1] : ''; };

  // 第 4 行是分节行 → 第 5 行序号必须是常量 1，绝不能引用 A4（纯文字）
  assert.equal(fOf('A5'), '1', '分节行之后的第一条明细序号应为常量 1，实际: ' + fOf('A5'));
  assert.equal(fOf('A7'), '1', '空行之后的第一条明细序号应为常量 1，实际: ' + fOf('A7'));
  // 连续两条明细 → 第二条引上一行 +1
  assert.equal(fOf('A8'), 'A7+1', '连续明细应引用上一行 +1，实际: ' + fOf('A8'));
  // D 列：隐式乘补了 * 且函数名大写
  assert.equal(tOf('D5'), '1+1*TRUNC(2)', 'D5 应为 1+1*TRUNC(2)，实际: ' + tOf('D5'));
  assert.equal(tOf('D8'), '1+1*SQRT(4)', 'D8 应为 1+1*SQRT(4)，实际: ' + tOf('D8'));
  // 纯文字备注写在 G 列，且必须是 inlineStr（不能被 WPS 当公式）
  assert.equal(tOf('G8'), '纯文字备注', '纯文字备注应原样落在 G8，实际: ' + tOf('G8'));
  assert.match((cellOfRef('G8') || { attrs: '' }).attrs, /t="inlineStr"/, '纯文字必须是 inlineStr，否则 WPS 会当公式报错');
  console.log('[OK] 导出序号列不引用纯文字行 + D 列隐式乘与函数名大写 + 纯文字备注不被当公式');

  await b.close();
  console.log('\n=== BUG-001 撤销回归全通过 ===');
})().catch(e => { console.error('[FAIL] ' + (e && e.message || e)); process.exit(1); });