'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(__dirname + '/calculator.html', 'utf8');
const start = html.indexOf('function sheetAddRow(refId, kind, pos) {');
const end = html.indexOf('\n}', start) + 2;
assert(start >= 0 && end > start, 'sheetAddRow exists');
assert(html.includes("sheetAddRow(sheetSel, 'item', 'above')"), 'toolbar inserts above the selected row');

const state = {};
let lastTip = null;          /* ★ 2026-10-09：sheetAddRow 现在会挂「可撤销」提示（appTip 第三参数），
                                桩环境必须补上 appTip / sheetFlash，否则跑出来是 ReferenceError。*/
Object.assign(state, {
  sheetRows: [{ id: 1 }, { id: 2 }, { id: 3 }],
  sheetSel: null,
  FH_ROW_CAP: 99,
  window: { fhCapNow: () => 99 },
  sheetCommitFocus() {},
  sheetUid: (() => { let id = 10; return () => id++; })(),
  sheetIndexOf: id => state.sheetRows.findIndex(row => row.id === id),
  sheetSave() {},
  sheetRender: () => Promise.resolve(),
  document: { querySelector: () => null },
  appTip(msg, ms, undoFn) { lastTip = { msg: msg, undoFn: typeof undoFn === 'function' ? undoFn : null }; },
  sheetFlash() {}
});
vm.runInNewContext(html.slice(start, end) + '; globalThis.addRow = sheetAddRow;', state);

(async () => {
  await state.addRow(2, 'item', 'above');
  assert.deepEqual(state.sheetRows.map(row => row.id), [1, 10, 2, 3]);
  state.sheetSel = null;
  await state.addRow(state.sheetSel, 'item', 'above');
  assert.deepEqual(state.sheetRows.map(row => row.id), [1, 10, 2, 3, 11]);
  /* ★ 2026-10-09 新增：插行必须挂上可撤回调，且撤完真的回到插前（这次改动的核心） */
  assert.ok(lastTip && lastTip.undoFn, '插行后提示条上必须挂「撤销」回调');
  const snapshot = state.sheetRows.map(row => row.id).slice();
  await state.addRow(null, 'item');
  assert.equal(state.sheetRows.length, snapshot.length + 1, '再插一行应多一行');
  lastTip.undoFn();
  await new Promise(r => setTimeout(r, 0));
  assert.deepEqual(state.sheetRows.map(row => row.id), snapshot, '撤销插行后应回到插前行集合');
  console.log('插入行回归通过：选中时上插、无选择时追加末尾、插行可撤销');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
