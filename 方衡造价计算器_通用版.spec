# -*- mode: python ; coding: utf-8 -*-
# 通用版（onedir 免解压，秒开）：py38 链构建，32 位；目标系统 Windows 10 / 11（Win7 已放弃，2026-09-23）
# 用 py38\python.exe -m PyInstaller 方衡造价计算器_通用版.spec（不要加 --specpath）


a = Analysis(
    ['shell.py'],
    pathex=[],
    binaries=[],
    # ⚠️ 必须带上 MicrosoftEdgeWebview2Setup.exe：安装包（installer.nsi 的 File /r）就是
    # 封的这个目录，2026-09-23 就是因为这里漏了它 → 安装时 IfFileExists 命中跳过分支
    # → 收件人首次运行弹「需要 WebView2 运行库」。别删。
    datas=[('calculator.html', '.'), ('MicrosoftEdgeWebview2Setup.exe', '.')],
    hiddenimports=['webview', 'webview.platforms.winforms', 'webview.platforms.edgechromium', 'pythonnet', 'clr'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    version='version_info.txt',   # 2026-10-23 补：exe 属性版本（对齐单文件版；此前安装包内 exe 属性一直是空的）
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='方衡造价计算器通用版',
)
