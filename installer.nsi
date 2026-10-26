; 方衡造价计算器 V2.1 安装包（NSIS）
; 产物：单 exe 安装程序，双击 → 一键装到 LocalAppData → 桌面/开始菜单快捷方式
; 构建：makensis installer.nsi

!define APPNAME      "方衡造价计算器"
!define APPVERSION   "2.1.3.0"
!define COMPANY      "方衡Forhan"
!define EXENAME      "方衡造价计算器.exe"
!define UNINST_KEY   "Software\Microsoft\Windows\CurrentVersion\Uninstall\方衡造价计算器"

; ── 常规设置 ──────────────────────────────────────────
Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "WinVer.nsh"   ; ${AtMostWin7} —— Win7 的 WebView2 提示与其它系统分开写

; 普通用户装到自己目录（免管理员、免 UAC 弹窗）
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\方衡造价计算器"
InstallDirRegKey HKCU "Software\方衡造价计算器" "InstallDir"

!define MUI_ICON   "icon.ico"
!define MUI_UNICON "icon.ico"

; 安装向导界面（简体中文；2026-10-22 起一键安装，不提供选目录页——
; 用户手选 Program Files 类受保护目录会导致 WebView2 数据目录写不进，程序起不来）
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

Name "${APPNAME} ${APPVERSION}"
OutFile "安装包\方衡造价计算器V2.1.exe"
; 用 exe 自带图标元数据
VIProductVersion "${APPVERSION}"
VIAddVersionKey /LANG=2052 "ProductName" "${APPNAME}"
VIAddVersionKey /LANG=2052 "CompanyName" "${COMPANY}"
VIAddVersionKey /LANG=2052 "FileDescription" "${APPNAME} 安装程序"
VIAddVersionKey /LANG=2052 "FileVersion" "${APPVERSION}"
VIAddVersionKey /LANG=2052 "ProductVersion" "${APPVERSION}"
VIAddVersionKey /LANG=2052 "LegalCopyright" "Copyright (C) 2026 ${COMPANY}"

; ── 辅助函数 ──────────────────────────────────────────
; 检测 WebView2 Runtime 是否已安装 → 结果写入 $R2（"" = 未装，否则为版本号）
; 口径与 shell.py::_webview2_installed 一致：三处注册表任一有 pv 即视为已装。
; 为什么三处都要查：per-machine 装在 64 位系统上落 WOW6432Node，32 位系统落
; 常规 SOFTWARE；而本安装包是 RequestExecutionLevel user，非管理员时安装器
; 会 per-user 装到 HKCU。漏掉任何一处都会误判成「没装」重复安装。
Function CheckWebView2
  StrCpy $R2 ""
  ReadRegStr $R3 HKLM "SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
  ${If} $R3 != ""
    StrCpy $R2 $R3
  ${Else}
    ReadRegStr $R3 HKLM "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
    ${If} $R3 != ""
      StrCpy $R2 $R3
    ${Else}
      ReadRegStr $R3 HKCU "SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}" "pv"
      ${If} $R3 != ""
        StrCpy $R2 $R3
      ${EndIf}
    ${EndIf}
  ${EndIf}
FunctionEnd

; ── 初始化：目录安检（2026-10-22 一键安装配套）──────────────────
; 升级场景：注册表可能记住一个普通权限写不进去的老位置（典型：手动装进
; Program Files，WebView2 数据目录建不起来 → 启动弹「Microsoft Edge 无法
; 读取和写入其数据目录」，2026-10-22 用户实机反馈）。这里做一次可写探针：
; 写不进就无视记忆位置，回默认 $LOCALAPPDATA\方衡造价计算器。
Function .onInit
  ClearErrors
  CreateDirectory "$INSTDIR"
  FileOpen $R8 "$INSTDIR\.fh-install-probe.tmp" w
  ${If} ${Errors}
    StrCpy $INSTDIR "$LOCALAPPDATA\方衡造价计算器"
  ${Else}
    FileClose $R8
    Delete "$INSTDIR\.fh-install-probe.tmp"
  ${EndIf}
  ClearErrors
FunctionEnd

; ── 安装段 ──────────────────────────────────────────
Section "Install"
  SetShellVarContext current

  ; ★ 2026-10-20 用户反馈「安装不了 / 会卡住」：开始就关掉旧进程，不弹框。
  ;   安装要动它占着的数据目录；静默安装下 MessageBox 会永久挂住。
  DetailPrint "关闭正在运行的计算器（若在运行）..."
  nsExec::ExecToLog 'taskkill /IM "${EXENAME}" /F'
  Sleep 1000

  ; ── 卸载旧版（若有）─────────────────────────────────────
  ReadRegStr $R0 HKCU "${UNINST_KEY}" "UninstallString"
  StrCmp $R0 "" done

  ; ⚠️⚠️ 用户的离线计算稿存在 $INSTDIR\webview2_data\ 里（WebView2 用户数据目录，
  ; localStorage 落在其中的 Local Storage\leveldb）。旧版卸载器的收尾是
  ; RMDir /r "$INSTDIR" —— 整个安装目录连数据一起删，且不可逆。
  ; 所以卸载旧版【之前】必须先把数据挪到安装目录之外，装完再移回来。
  ; 2026-09-23 实测：本机该目录里确实有用户 9-23 当天写入的计算稿数据。
  StrCpy $R4 ""
  IfFileExists "$INSTDIR\webview2_data\*.*" 0 wv2_move_done
    ; 取一个唯一的临时路径（NSIS 没有 $RANDOM；GetTempFileName 会顺手建个空文件）
    GetTempFileName $R4
    Delete "$R4"
    ClearErrors
    Rename "$INSTDIR\webview2_data" "$R4"
    IfErrors 0 wv2_move_done
      ; 挪不动基本只有一个原因：程序还在运行、数据文件被占用
      IfSilent wv2_locked_silent
      MessageBox MB_ICONEXCLAMATION|MB_OK "「方衡造价计算器」正在运行，安装目录里有你的计算数据，现在升级会把它弄丢。$\r$\n$\r$\n请先退出程序，再重新运行本安装包。"
      Abort
    wv2_locked_silent:
      Abort
    ClearErrors
  wv2_move_done:

  DetailPrint "检测到旧版本，正在卸载..."
  ExecWait '"$R0" /S _?=$INSTDIR'
done:
  ; 干净安装：先清目录（防旧文件残留，但保留用户数据目录）
  RMDir /r "$INSTDIR\_internal"

  SetOutPath "$INSTDIR"
  File /r "dist\方衡造价计算器\*.*"

  ; 把升级前挪走的用户数据放回原位（若旧版卸载器已把它删了，这里就是恢复的唯一机会）
  StrCmp $R4 "" wv2_restore_done
  IfFileExists "$INSTDIR\webview2_data\*.*" wv2_restore_done
  Rename "$R4" "$INSTDIR\webview2_data"
wv2_restore_done:

  ; 上次卸载留下的数据备份（卸载时挪到安装目录外保命的那个）→ 搬回来
  IfFileExists "$LOCALAPPDATA\方衡造价计算器-计算数据\*.*" 0 wv2_restore_end
  IfFileExists "$INSTDIR\webview2_data\*.*" wv2_restore_end
    Rename "$LOCALAPPDATA\方衡造价计算器-计算数据" "$INSTDIR\webview2_data"
wv2_restore_end:

  ; ── WebView2 Runtime：先查已装，缺则静默安装；装不上必须如实告知 ──────────
  ; 随包携带的是 Evergreen 引导安装器（仅 1.7MB），它自身【不含】运行时，需联网
  ; 下载约 180MB。⚠️ 无网络、或 Windows 7（微软 2023-01 起停止支持）时必然装不上
  ; —— 必须给出可读提示，不能静默跳过：否则用户只看到「程序打不开」，会以为
  ; 安装包是坏的（2026-09-23 就是这么被反馈的）。
  ; 检测口径与程序启动时一致（shell.py::_webview2_installed）：三处注册表任一有 pv。
  Call CheckWebView2
  StrCmp $R2 "" 0 wv2_report_ok

  IfFileExists "$INSTDIR\MicrosoftEdgeWebview2Setup.exe" wv2_have_setup wv2_no_setup

wv2_have_setup:
  DetailPrint "正在安装 WebView2 运行库（首次需联网下载，约 180MB，请稍候）..."
  ; ExecToLog 拿不到返回码、失败也全静默 → 必须用 ExecToStack + Pop
  nsExec::ExecToStack '"$INSTDIR\MicrosoftEdgeWebview2Setup.exe" /silent /install'
  Pop $0
  Pop $1
  DetailPrint "WebView2 安装器返回码：$0"
  Call CheckWebView2          ; 不信返回码，复查注册表为准
  StrCmp $R2 "" 0 wv2_report_ok
  IfSilent wv2_end
  ${If} ${AtMostWin7}
    MessageBox MB_ICONEXCLAMATION|MB_OK "程序已装好，但「WebView2 运行库」在 Windows 7 上装不了。$\r$\n$\r$\n原因：微软自 2023 年起停止为 Windows 7 提供 WebView2 更新，联网自动安装必然失败。$\r$\n$\r$\n办法：需要单独安装 Windows 7 专用的 109 版运行库（约 130MB，离线包），装完再打开程序。请联系方衡获取，或访问官网：$\r$\n$\r$\nfhzjsj.cn"
  ${Else}
    MessageBox MB_ICONEXCLAMATION|MB_OK "程序已装好，但「WebView2 运行库」没能自动装上。$\r$\n$\r$\n最常见的原因：这台电脑当前上不了网（该运行库需联网下载约 180MB）。$\r$\n$\r$\n请连上网络后重新运行本安装包；也可以手动运行安装目录里的：$\r$\nMicrosoftEdgeWebview2Setup.exe$\r$\n$\r$\n官网：fhzjsj.cn"
  ${EndIf}
  Goto wv2_end

wv2_no_setup:
  IfSilent wv2_end
  MessageBox MB_ICONEXCLAMATION|MB_OK "程序已装好，但安装包内缺少 WebView2 安装器，无法自动补齐运行库。$\r$\n$\r$\n请到官网按提示手动安装「WebView2 Runtime」后再打开程序：$\r$\n$\r$\nfhzjsj.cn"
  Goto wv2_end

wv2_report_ok:
  DetailPrint "已检测到 WebView2 运行库（版本 $R2），跳过安装。"

wv2_end:

  ; 开始菜单 + 桌面快捷方式（名字无后缀）
  CreateDirectory "$SMPROGRAMS\${COMPANY}"
  CreateShortCut  "$SMPROGRAMS\${COMPANY}\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0
  ; 桌面快捷方式：$DESKTOP 读的是系统默认路径，桌面被重定向（如 D:\桌面）时
  ; 会写错位置。优先用 SHGetKnownFolderPath 解析的真实桌面；失败再退回 $DESKTOP。
  System::Call 'shell32::SHGetKnownFolderPath(g "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", i 0, p 0, *p .r0) i .r1'
  ${If} $1 == 0
    System::Call '*$0(&w128 .r2)'
    System::Call 'ole32::CoTaskMemFree(p r0)'
    CreateShortCut  "$2\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0
  ${Else}
    CreateShortCut  "$DESKTOP\${APPNAME}.lnk" "$INSTDIR\${EXENAME}" "" "$INSTDIR\${EXENAME}" 0
  ${EndIf}

  ; 卸载器
  WriteUninstaller "$INSTDIR\卸载方衡造价计算器.exe"
  WriteRegStr HKCU "Software\方衡造价计算器" "InstallDir" $INSTDIR
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayName"     "${APPNAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayVersion"  "${APPVERSION}"
  WriteRegStr HKCU "${UNINST_KEY}" "Publisher"       "${COMPANY}"
  WriteRegStr HKCU "${UNINST_KEY}" "DisplayIcon"     "$INSTDIR\${EXENAME}"
  WriteRegStr HKCU "${UNINST_KEY}" "UninstallString" "$INSTDIR\卸载方衡造价计算器.exe"
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINST_KEY}" "NoRepair" 1

  ; 可选：装完直接启动
  ; Exec "$INSTDIR\${EXENAME}"
SectionEnd

; ── 卸载段 ──────────────────────────────────────────
Section "Uninstall"
  SetShellVarContext current
  ; 杀进程防文件占用
  nsExec::ExecToLog 'taskkill /IM "${EXENAME}" /F'

  ; ⚠️ 用户的离线计算稿在 $INSTDIR\webview2_data\，不能跟着程序一起删（不可逆）。
  ; 这里【逐个删程序文件】而不是 RMDir /r 整个目录：webview2_data 原地留着，
  ; 以后重装本程序就能接着用旧数据（路径相同，程序自动认）。
  Delete "$INSTDIR\*.exe"
  Delete "$INSTDIR\*.dll"
  Delete "$INSTDIR\*.pyd"
  Delete "$INSTDIR\*.zip"
  Delete "$INSTDIR\*.html"
  Delete "$INSTDIR\*.ico"
  RMDir /r "$INSTDIR\webview"
  RMDir /r "$INSTDIR\_internal"
  RMDir "$INSTDIR"          ; 非递归：数据目录还在就删不掉，这是预期行为，不是失败
  IfFileExists "$INSTDIR\webview2_data\*.*" 0 un_nodata
    DetailPrint "程序文件已删除；你的计算数据保留在 $INSTDIR\webview2_data"
    Goto un_done
  un_nodata:
    DetailPrint "程序文件已删除。"
  un_done:
  Delete "$SMPROGRAMS\${COMPANY}\${APPNAME}.lnk"
  RMDir  "$SMPROGRAMS\${COMPANY}"
  ; 桌面快捷方式（与安装段同路径逻辑）
  System::Call 'shell32::SHGetKnownFolderPath(g "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", i 0, p 0, *p .r0) i .r1'
  ${If} $1 == 0
    System::Call '*$0(&w128 .r2)'
    System::Call 'ole32::CoTaskMemFree(p r0)'
    Delete "$2\${APPNAME}.lnk"
  ${Else}
    Delete "$DESKTOP\${APPNAME}.lnk"
  ${EndIf}
  DeleteRegKey HKCU "${UNINST_KEY}"
  DeleteRegKey HKCU "Software\方衡造价计算器"
SectionEnd
