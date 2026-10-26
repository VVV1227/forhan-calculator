'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const html = fs.readFileSync(__dirname + '/calculator.html', 'utf8');
const start = html.indexOf('function computeTri() {');
const end = html.indexOf('\nfunction insertTri()', start);
assert(start >= 0 && end > start, 'computeTri exists');

function solve(values) {
  const ids = ['tri_a', 'tri_b', 'tri_c', 'tri_A', 'tri_B'];
  const fields = Object.fromEntries(ids.map(id => [id, { value: values[id] == null ? '' : String(values[id]) }]));
  const classList = { add() {}, remove() {} };
  const context = {
    triUser: new Set(Object.keys(values)),
    triResult: { dataset: {}, innerHTML: '' },
    triArea: { innerHTML: '' },
    triSource: { textContent: '', classList },
    document: { activeElement: null },
    $: id => fields[id] || null,
    renderTriDiagram() {}
  };
  vm.runInNewContext(html.slice(start, end) + '; globalThis.run = computeTri;', context);
  context.run();
  return { result: context.triResult, source: context.triSource.textContent, fields };
}

const oneSideAndAngle = solve({ tri_a: 6, tri_A: 60 });
assert.equal(oneSideAndAngle.result.dataset.val, '6.928');
assert.match(oneSideAndAngle.source, /角 B=30/);

const incomplete = solve({ tri_b: 6 });
assert.equal(incomplete.result.dataset.val, '');
assert.equal(incomplete.fields.tri_a.value, '');
assert.match(incomplete.source, /填任意两条边/);

const angleOnly = solve({ tri_A: 60 });
assert.equal(angleOnly.result.dataset.val, '');
assert.equal(angleOnly.fields.tri_B.value, 30);
assert.equal(angleOnly.fields.tri_B.readOnly, true);

for (const [values, expected] of [
  [{ tri_a: 3, tri_b: 4 }, 5],
  [{ tri_a: 3, tri_c: 5 }, 4],
  [{ tri_b: 4, tri_c: 5 }, 3]
]) {
  const result = solve(values);
  const missing = ['tri_a', 'tri_b', 'tri_c'].find(id => !(id in values));
  assert.equal(result.fields[missing].value, expected, JSON.stringify(values));
  assert.equal(result.fields[missing].readOnly, true);
}

assert.equal(solve({ tri_b: 6, tri_A: 60 }).fields.tri_a.value, 10.392);

const sideAngleCases = [
  [{ tri_a: 6, tri_A: 60 }, '6.928'],
  [{ tri_b: 6, tri_A: 60 }, '12'],
  [{ tri_c: 10, tri_A: 60 }, '10'],
  [{ tri_a: 6, tri_B: 30 }, '6.928'],
  [{ tri_b: 6, tri_B: 30 }, '12'],
  [{ tri_c: 10, tri_B: 30 }, '10']
];
for (const [values, expected] of sideAngleCases) {
  assert.equal(solve(values).result.dataset.val, expected, JSON.stringify(values));
}

const consistentExtraAngle = solve({ tri_a: 6, tri_c: 10, tri_B: 53.1301 });
assert.equal(consistentExtraAngle.result.dataset.val, '10');

const conflictingAngle = solve({ tri_a: 6, tri_c: 10, tri_B: 30 });
assert.equal(conflictingAngle.result.dataset.val, '');
assert.equal(conflictingAngle.result.textContent, '条件冲突');
assert.match(conflictingAngle.source, /角 B 应为 53\.13°/);

const conflictingThirdSide = solve({ tri_a: 5, tri_b: 6, tri_c: 10 });
assert.equal(conflictingThirdSide.result.dataset.val, '');
assert.equal(conflictingThirdSide.result.textContent, '条件冲突');
assert.match(conflictingThirdSide.source, /c 应为 7\.81/);

const conflictingAngleFromBC = solve({ tri_b: 6, tri_c: 10, tri_B: 30 });
assert.equal(conflictingAngleFromBC.result.dataset.val, '');
assert.equal(conflictingAngleFromBC.result.textContent, '条件冲突');
assert.match(conflictingAngleFromBC.source, /角 B 应为 36\.87°/);

const zeroSide = solve({ tri_a: 0, tri_b: 4 });
assert.equal(zeroSide.result.dataset.val, '');
assert.equal(zeroSide.result.textContent, '条件冲突');
assert.match(zeroSide.source, /边 a 必须大于 0/);

console.log('三角计算回归通过：边角组合、冗余输入一致性与非正边长校验');
