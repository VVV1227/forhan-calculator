# -*- mode: python ; coding: utf-8 -*-
# 单文件版（onefile）：整个程序封成一个 exe，双击即用，无需解压
# 构建：py38\python.exe -m PyInstaller 方衡造价计算器_单文件版.spec
# 说明：首次启动会把运行时解压到 %TEMP%\_MEIxxxx（约 2-4 秒），之后秒开由系统缓存决定。
#       calculator.html 一并封入，改页面需重新打包；改样式调试仍用通用版（onedir）更方便。


a = Analysis(
    ['shell.py'],
    pathex=[],
    binaries=[],
    # ⚠️ 免安装版同样要带 WebView2 引导安装器：换机器运行时若目标机没装过 WebView2，
    # 程序启动才能自愈（否则直接弹「需要 WebView2 运行库」）。别删。
    datas=[('calculator.html', '.'), ('icon.ico', '.'), ('MicrosoftEdgeWebview2Setup.exe', '.')],
    hiddenimports=['webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium', 'pythonnet', 'clr', 'ctypes.wintypes'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 计算器全程本地 HTTP + 系统浏览器外跳，不建 TLS 连接，可安全剔除 OpenSSL（约 2.6MB）
    # → onefile 每次启动要解压全部内容，体积直接等于启动耗时。
    excludes=['_ssl', 'ssl', '_hashlib', 'hashlib', 'unicodedata'],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='方衡造价计算器_单文件版_v1.10内测版',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon.ico'],
    version='version_info.txt',   # 正式版本信息：SmartScreen/属性详情可见，降低可疑度评级
)
