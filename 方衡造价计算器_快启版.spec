# -*- mode: python ; coding: utf-8 -*-
# onedir 版（快启版）：启动 1-2s，无 _MEIPASS 解压，适合慢电脑日常用
# 构建：python -m PyInstaller 方衡造价计算器_快启版.spec
# 产物：dist/方衡造价计算器/  文件夹（exe + 依赖 ~30MB）
# 慢电脑 vs onefile：
#   - onefile 每次启动要解压全部到 %TEMP%/_MEIxxxx，慢电脑 5-8s
#   - onedir 直接 load .pyd/.dll，慢电脑 1-2s
# 配合 NSIS 安装包分发：用户拿到的还是单 exe（安装器），装到硬盘上是 onedir 文件夹

a = Analysis(
    ['shell.py'],
    pathex=[],
    binaries=[],
    datas=[('calculator.html', '.'), ('icon.ico', '.'), ('MicrosoftEdgeWebview2Setup.exe', '.')],
    hiddenimports=['webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium', 'pythonnet', 'clr', 'ctypes.wintypes'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 不能剔 ssl/hashlib/unicodedata：webview.http → bottle 都用
    # 本版本不剔任何 stdlib，安全优先
    excludes=[],
    noarchive=True,  # 纯 Python 模块展开成文件，减少启动时归档解压
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],          # onedir: 不打进 exe，独立成 _internal/
    [],
    exclude_binaries=True,
    name='方衡造价计算器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
    version='version_info.txt',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='方衡造价计算器',
)
