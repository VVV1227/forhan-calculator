'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(__dirname + '/calculator.html', 'utf8');
const start = html.indexOf('  function trackDrag() {');
const end = html.indexOf('\n  function stopDrag()', start);
assert(start >= 0 && end > start, 'drag autoscroll exists');

function dragAt(y, initialScroll) {
  const wrap = { scrollTop: initialScroll, getBoundingClientRect: () => ({ left: 0, right: 400, top: 100, bottom: 300 }) };
  const row = { dataset: { id: '8' } };
  let selected;
  const context = {
    dragStart: 2, dragLast: 2, dragPoint: { x: 30, y }, dragFrame: 0,
    body: { closest: () => wrap },
    document: { elementFromPoint: () => ({ closest: () => row }) },
    sheetSelectRow: (id, event) => { selected = { id, event }; },
    requestAnimationFrame: () => 1
  };
  vm.runInNewContext(html.slice(start, end) + '; globalThis.run = trackDrag;', context);
  context.run();
  return { scrollTop: wrap.scrollTop, selected, frame: context.dragFrame };
}

const down = dragAt(340, 0);
assert(down.scrollTop > 0);
assert.equal(down.selected.id, 8);
assert.equal(down.selected.event.baseId, 2);
assert.equal(down.frame, 1);

const up = dragAt(60, 100);
assert(up.scrollTop < 100);
console.log('拖选回归通过：鼠标越过上下边缘时滚动并保持起始行锚点');
