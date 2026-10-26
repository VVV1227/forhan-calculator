# 方衡造价计算器 · 社区版（Forhan Calculator Community Edition）

> 方寸有数，权衡定价 —— 面向工程造价人的多列计算稿工具。
> 官网与完整版：**[fhzjsj.cn](https://fhzjsj.cn)**

## 这是什么

造价工程师日常算量的「电子计算稿」：项目名称 + 计算式 + 单位 + 工程量，
边算边沉淀，支持部位分组、自定义公式、附图、撤销/重做，
**一键导出 Excel（WPS/微软 Excel 双兼容，含品牌水印与计算式溯源）**，
也可以把导出的表原样导入回来继续算。

社区版是方衡造价计算器 V1.0（JS 版）的**冻结快照**：

- ✅ **纯本地运行**：不联网、无账号、零埋点——你的项目名称、计算式、工程量只存在你自己的电脑上
- ✅ **开箱即用**：双击 `calculator.html` 就能算（浏览器即开即用）
- ✅ **完整导出**：自研零依赖 xlsx 生成器（forhan-xlsx），不用 SheetJS，样式齐全
- ✅ 每表 50 行免费额度，导出不受限
- ❄️ 功能冻结：社区版不接新需求，只作方法论分享与体验引流

## 快速开始

**方式一：浏览器直接用**

双击 `calculator.html`（推荐 Chrome / Edge / 360 极速等现代浏览器）。

**方式二：桌面壳（Windows）**

`shell.py` 是 pywebview 桌面壳（独立窗口、Excel 导入增强——导入解析同样是自研零依赖，zipfile + 标准库 XML）：

```bash
pip install pywebview
python shell.py
```

打包单 exe（可选）：`pyinstaller 方衡造价计算器_单文件版.spec`，
安装包制作：`installer.nsi`（NSIS）。

## 跑测试

```bash
# 纯 node 的 vm 测试（无依赖）
node row-insert.test.cjs   # 行插入
node triangle.test.cjs     # 三角计算
node watermark.test.cjs    # 水印导出结构

# 浏览器冒烟（需要 playwright）
node smoke.cjs
```

> 说明：`row-insert` / `row-delete` / `undo-xz` 三个测试的断言锚点已随版本演进过时
> （测的是旧行为，非功能损坏），保留作历史参考。

## 社区版 vs 完整版

| | 社区版（本仓库） | 完整版（[fhzjsj.cn](https://fhzjsj.cn)） |
|---|---|---|
| 核心计算 / 导出 / 导入 | ✅ | ✅ |
| 每表行数 | 50 行 | 更多 |
| 账号与云存档 | — | ✅ |
| 清单匹配 / 投标报价等专业能力 | — | ✅ |
| 数据不出本机 | ✅ | 云端加密存储 |

## 代码说明

- `calculator.html`：全部前端逻辑（约 1 万行单文件，无构建步骤，ES5 兼容老内核）
- `shell.py`：pywebview 桌面壳 + Excel 导入解析（标准库自研，零第三方依赖）
- `*.test.cjs` / `smoke.cjs`：回归测试
- `*.spec` / `installer.nsi`：PyInstaller / NSIS 打包脚本

许可证：MIT（见 [LICENSE](LICENSE)）。

**商标声明**：「方衡」「Forhan」及 fhzjsj.cn 品牌标识不在 MIT 授权范围内。
你可以自由使用、修改、分发本代码，但不得以「方衡」名义或仿冒官网形式发布改名版本。

## 为什么开源

造价工具大多闭源且沉重。我们把打磨了两年的计算器 V1.0 完整源码开放出来：
表格交互、公式引擎、Excel 样式导出的实现细节都在里面，希望能帮到同行与开发者。
觉得有用，来 [fhzjsj.cn](https://fhzjsj.cn) 看看完整版。
