'use strict';
/* 水印回归（2026-10-09 第四版·最终）：用户「水印不行…从这里加」——
   前三版（drawing 文字 shape / 页眉图片 &C&G / drawing 图片平铺）都不行，
   最终照抄 WPS 自带「自定义水印」的真实产物结构：customXml/item1.xml 声明
   （命名空间 http://www.wps.cn/officeDocument/2017/etCustomData）。
   本测试用真浏览器跑 buildCalcSheet，断言产物 zip 里：
     ① customXml 三件套齐全   ② 声明里文字/角度/字号/透明度正确
     ③ workbook.xml.rels 挂了 customXml 关系   ④ Content_Types 注册了 itemProps
     ⑤ 不再有 xl/media/watermark.png 与 drawing1.xml（防「几张大图盖上去」复发）
   跑法：node desktop/watermark.test.cjs */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const zlib = require('node:zlib');
const { chromium } = require('playwright');

const HTML = path.join(__dirname, 'calculator.html');

/* 极简 zip 中央目录解析：取部件名清单 */
function zipNames(buf) {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  let eocd = -1;
  for (let i = buf.length - 22; i >= 0; i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new Error('不是合法 zip');
  const n = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  const names = [];
  for (let k = 0; k < n; k++) {
    const fnLen = dv.getUint16(p + 28, true);
    names.push(new TextDecoder().decode(buf.subarray(p + 46, p + 46 + fnLen)));
    p += 46 + fnLen + dv.getUint16(p + 30, true) + dv.getUint16(p + 32, true);
  }
  return names;
}

/* 读 zip 内某个部件的文本内容（store / deflate 都认） */
function zipRead(buf, want) {
  const dv = new DataView(buf.buffer, buf.byteOffset, buf.byteLength);
  let eocd = -1;
  for (let i = buf.length - 22; i >= 0; i--) if (dv.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  if (eocd < 0) throw new Error('不是合法 zip');
  const n = dv.getUint16(eocd + 10, true);
  let p = dv.getUint32(eocd + 16, true);
  for (let k = 0; k < n; k++) {
    const method = dv.getUint16(p + 10, true);
    const compSize = dv.getUint32(p + 20, true);
    const fnLen = dv.getUint16(p + 28, true);
    const extraLen = dv.getUint16(p + 30, true);
    const cmLen = dv.getUint16(p + 32, true);
    const localOff = dv.getUint32(p + 42, true);
    const name = new TextDecoder().decode(buf.subarray(p + 46, p + 46 + fnLen));
    if (name === want) {
      const lfnLen = dv.getUint16(localOff + 26, true);
      const lexLen = dv.getUint16(localOff + 28, true);
      const dataStart = localOff + 30 + lfnLen + lexLen;
      const comp = buf.subarray(dataStart, dataStart + compSize);
      return method === 0 ? comp : zlib.inflateRawSync(comp);
    }
    p += 46 + fnLen + extraLen + cmLen;
  }
  return null;
}
/* ⚠️ zipRead 在 store 模式下返回 Uint8Array，直接 .toString('utf8') 会得到逗号数字串
   （TypedArray 的 toString 不带编码参数）—— 必须用 TextDecoder 解码。 */
function readText(buf, name) {
  const b = zipRead(buf, name);
  return b ? new TextDecoder().decode(b) : '';
}

async function build(page, model, mode) {
  const b64 = await page.evaluate(([m, md]) => {
    const bytes = window.ForhanXlsx.buildCalcSheet(m, { mode: md });
    const u8 = new Uint8Array(bytes.buffer || bytes);
    let s = ''; for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
    return btoa(s);
  }, [model, mode]);
  return Uint8Array.from(Buffer.from(b64, 'base64'));
}

(async () => {
  const html = fs.readFileSync(HTML, 'utf8');
  const b = await chromium.launch();
  const p = await b.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(String(e)));
  await p.goto('data:text/html;charset=utf-8,' + encodeURIComponent(html), { waitUntil: 'load' });
  await p.waitForTimeout(500);

  const rows = [{ kind: 'item', node: '', name: '地面石材', expr: '1+1', unit: '㎡', qty: 2, note: '' }];

  /* ---- WPS 版：应带 customXml 水印 ---- */
  const wpsBuf = await build(p, { sheetName: 'Sheet1', rows }, 'wps');
  const names = zipNames(wpsBuf);
  assert.ok(names.includes('customXml/item1.xml'), 'customXml/item1.xml 必须在包内（WPS 水印声明本体）');
  assert.ok(names.includes('customXml/itemProps1.xml'), 'customXml/itemProps1.xml 必须在包内');
  assert.ok(names.includes('customXml/_rels/item1.xml.rels'), 'customXml/_rels/item1.xml.rels 必须在包内');

  const item = readText(wpsBuf, 'customXml/item1.xml');
  assert.ok(item.includes('http://www.wps.cn/officeDocument/2017/etCustomData'), '声明必须用 WPS etCustomData 命名空间');
  assert.ok(/<watermark type="0">/.test(item), '必须是文字水印（type=0）');
  assert.ok(/angle="-45"/.test(item), '默认角度 -45°');
  assert.ok(/fontSize="36\.000000"/.test(item), '默认字号 36');
  assert.ok(/opacity="0\.240000"/.test(item), '默认透明度 0.24（对齐 WPS 自定义水印默认观感）');
  assert.ok(item.includes('方衡 Forhan'), '水印文字须含品牌名');
  assert.ok(item.includes('fhzjsj.cn'), '水印文字须含域名');
  assert.ok(item.includes('&#13;'), '双行文字用 &#13; 换行（照抄 WPS 结构）');

  const wbRels = readText(wpsBuf, 'xl/_rels/workbook.xml.rels');
  assert.ok(wbRels.includes('customXml') && wbRels.includes('../customXml/item1.xml'), 'workbook.xml.rels 必须挂 customXml 关系');
  const ct = readText(wpsBuf, '[Content_Types].xml');
  assert.ok(ct.includes('/customXml/itemProps1.xml'), 'Content_Types 必须注册 itemProps1.xml');

  assert.ok(!names.some(n => /watermark/.test(n)), '不得再有 xl/media/watermark.png 之类图片（防「大图盖上去」）');
  assert.ok(!names.includes('xl/drawings/drawing1.xml'), '无图时不应生成 drawing1.xml');

  /* ---- 关水印：三件套应全部消失 ---- */
  const offBuf = await build(p, { sheetName: 'Sheet1', rows, watermark: false }, 'wps');
  const offNames = zipNames(offBuf);
  assert.ok(!offNames.some(n => n.indexOf('customXml/') === 0), 'watermark:false 时不得写 customXml 水印部件');
  assert.ok(!readText(offBuf, 'xl/_rels/workbook.xml.rels').includes('customXml'), 'watermark:false 时 workbook 关系不得含 customXml');

  /* ---- 兼容版：老 Excel 不认这份声明，故完全不挂 ---- */
  const compatBuf = await build(p, { sheetName: 'Sheet1', rows }, 'compat');
  assert.ok(!zipNames(compatBuf).some(n => n.indexOf('customXml/') === 0), '兼容版不得写 customXml 水印部件');

  await b.close();
  if (errs.length) { console.log('[FAIL] 页面报错 ' + errs.join('; ')); process.exit(1); }

  console.log('[OK] WPS 原生自定义水印机制正确：' + names.length + ' 个部件，含 customXml 三件套（文字/角度/字号/透明度齐全），含水印开关与兼容版隔离断言');
})().catch(e => { console.error('[FAIL] ' + (e && e.message || e)); process.exit(1); });
