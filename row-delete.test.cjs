'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(__dirname + '/calculator.html', 'utf8');
const start = html.indexOf('async function sheetDelSelectedRows() {');
const end = html.indexOf('\n/* 输入框', start);
assert(start >= 0 && end > start, 'selected-row delete exists');

const rows = [{ id: 1 }, { id: 2, shotId: 20 }, { id: 3 }, { id: 4 }];
let undo;
const context = {
  sheetRows: rows.slice(), sheetSel: 2, sheetGridSelection: { type: 'row', id: 2, ids: [2, 3] },
  sheetSelectedRowIds: () => [2, 3], sheetSave: () => {}, sheetRender: async () => {},
  appTip: (_message, _duration, fn) => { undo = fn; }, sheetFlash: () => {}
};
vm.runInNewContext(html.slice(start, end) + '; globalThis.run = sheetDelSelectedRows;', context);
(async () => {
  await context.run();
  assert.deepEqual(Array.from(context.sheetRows, row => row.id), [1, 4]);
  assert.equal(context.sheetGridSelection, null);
  assert.equal(typeof undo, 'function');
  undo();
  assert.deepEqual(Array.from(context.sheetRows, row => row.id), [1, 2, 3, 4]);
  assert.equal(context.sheetRows[1].shotId, 20);
  console.log('多行 Delete 回归通过：批量删除、选区清理和撤销恢复附图引用');
})().catch(err => { console.error(err); process.exitCode = 1; });
