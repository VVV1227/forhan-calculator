"""
方衡造价计算器 —— pywebview 桌面壳（WebView2 加载 calculator.html）

铁律：
- js_api 实例属性禁止持有非平凡对象，必须 _ 前缀（pywebview 暴露 js_api 时会
  递归遍历公开属性，爬进 WinForms 控件树会刷爆 UI 线程 → 窗口未响应）。
- 窗口控制走 Win32 API（hwnd = _win.native.Handle.ToInt32()），不依赖 pywebview
  原生方法（easy_drag 未实现）。
"""
import os
import re          # ★ 2026-10-22 补：模块级 _DISPIMG_RE/_IMGPLACE_RE 要用 re.compile，
                  #   原来只有函数内 import re as _re2，启动到这里直接 NameError 崩（用户 10-22 实报）
import sys
import time

_T0 = time.perf_counter()


def _t(msg):
    """导入/启动阶段性能打点（%TEMP%/forhan_import.log，perf_counter 毫秒）。"""
    try:
        with open(os.path.join(os.environ.get('TEMP', '.'), 'forhan_import.log'),
                  'a', encoding='utf-8') as f:
            f.write('%6dms  %s\n' % ((time.perf_counter() - _T0) * 1000, msg))
    except Exception:
        pass


_t('python 运行时已起, 开始导入')
import ctypes
_t('ctypes')
import webbrowser
import subprocess
_t('webbrowser')
import threading
_t('threading')
import functools
_t('functools')
import webview
_t('webview')

user32 = ctypes.windll.user32


class PT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class RC(ctypes.Structure):
    _fields_ = [('l', ctypes.c_long), ('t', ctypes.c_long),
                ('r', ctypes.c_long), ('b', ctypes.c_long)]


def _work_area():
    """屏幕工作区宽高（已扣掉任务栏）。

    别用 GetSystemMetrics(SM_CYSCREEN)：那是含任务栏的全屏高度，按它算出来的
    窗口底部会被任务栏压住、或者被迫收掉一大截（表格就只剩三四行高）。
    """
    u = ctypes.windll.user32
    r = RC()
    try:
        u.SystemParametersInfoW.argtypes = [ctypes.c_uint, ctypes.c_uint,
                                            ctypes.c_void_p, ctypes.c_uint]
        if u.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
            w, h = r.r - r.l, r.b - r.t
            if w > 200 and h > 200:
                return w, h
    except Exception as e:
        _log('取工作区失败，退回全屏尺寸: %s' % e)
    return u.GetSystemMetrics(SM_CXSCREEN), u.GetSystemMetrics(SM_CYSCREEN)


SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOCOPYBITS = 0x0100        # 掰子窗口时丢弃旧像素，强制整块重绘
SWP_ASYNCWINDOWPOS = 0x4000    # 跨线程挪窗口用异步投递，避免卡在 WebView2 忙线程上
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SM_CXSCREEN = 0
SM_CYSCREEN = 1
GWL_EXSTYLE = -20         # SetWindowLongW 取/设扩展样式
WS_EX_LAYERED = 0x00080000   # 分层窗口——SetLayeredWindowAttributes 的前提
LWA_ALPHA = 0x00000002    # 按 alpha 混合整窗
SPI_GETWORKAREA = 0x0030   # SystemParametersInfo 取「工作区」（已扣任务栏）
SW_MAXIMIZE = 3
SW_MINIMIZE = 6
SW_RESTORE = 9

# 启动/调用诊断日志（%TEMP%/forhan_boot.log）——真机拖动失灵时看此文件定位断点。
_LOG_PATH = os.path.join(os.environ.get('TEMP', '.'), 'forhan_boot.log')
TITLE = '方衡造价计算器 V2.1'

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010


def _load_icon(path, size):
    """从 .ico 文件加载指定尺寸的 HICON（失败返回 0）。"""
    return user32.LoadImageW(
        0, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)


def _apply_window_icon(hwnd):
    """给窗口设置任务栏/标题栏图标。

    pywebview create_window 无 icon 参数，frameless 窗口运行时任务栏
    显示的是 WinForms 默认图标——「图标没了」的根因。exe 里嵌的图标
    只覆盖 exe 文件本身，窗口类图标要运行时用 WM_SETICON 设。
    """
    if not hwnd:
        return
    ico_path = resource_path('icon.ico')
    if not os.path.exists(ico_path):
        _log('icon.ico 不存在, 跳过设置窗口图标: %s' % ico_path)
        return
    big = _load_icon(ico_path, 32)    # 任务栏/Alt+Tab
    small = _load_icon(ico_path, 16)  # 标题栏（frameless 不可见但保留）
    user32.SendMessageW(hwnd, 0x0080, 1, big)    # WM_SETICON ICON_BIG
    user32.SendMessageW(hwnd, 0x0080, 0, small)  # WM_SETICON ICON_SMALL
    _log('窗口图标已设置, big=%s small=%s' % (big, small))


def _find_hwnd_by_pid(pid, title):
    """按「窗口标题包含 title + 所属进程=pid」查顶层窗口句柄（线程安全）。"""
    result = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def _cb(h, _):
        ln = user32.GetWindowTextLengthW(h)
        if not ln:
            return True
        buf = ctypes.create_unicode_buffer(ln + 1)
        user32.GetWindowTextW(h, buf, ln + 1)
        if title not in buf.value:
            return True
        wpid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(h, ctypes.byref(wpid))
        if wpid.value != pid:
            return True
        # 同进程内还有 GDI+ Hook / IME 等隐藏辅助窗，标题同样含关键字且排在
        # EnumWindows 最前面，只认「可见 + 有正常尺寸」的窗口，取面积最大者。
        if not user32.IsWindowVisible(h):
            return True
        rc = RC()
        user32.GetWindowRect(h, ctypes.byref(rc))
        area = (rc.r - rc.l) * (rc.b - rc.t)
        if area >= 300 * 200:
            result.append((area, h))
        return True

    user32.EnumWindows(_cb, 0)
    return max(result)[1] if result else 0


_LOG_T0 = time.time()


_DISPIMG_RE = re.compile(r'^\[?\s*=?\s*DISPIMG\s*\(', re.I)
_IMGPLACE_RE = re.compile(r'^\[\s*\]$|^\[图片\]$')


def _is_img_placeholder_change(new_v, old_v):
    """WPS/Excel 打开带图的导出件时，会把浮动图片写成 =DISPIMG("ID_…") 存回单元格
    （方衡导出时那格本来是空的或 [] 占位）。这**不是用户改的内容**，不能报「文件被改过」。

    判据（保守）：新值必须是 DISPIMG 公式，且旧值是空 / [] / [图片] 这类占位。
    旧值有真实文字（如「按图示」）就不算——那说明用户把内容换成了图，是真改动。
    """
    nv = str(new_v or '').strip()
    ov = str(old_v or '').strip()
    if not _DISPIMG_RE.match(nv):
        return False
    return ov == '' or bool(_IMGPLACE_RE.match(ov))


def _log(msg):
    try:
        with open(_LOG_PATH, 'a', encoding='utf-8') as f:
            f.write('%s [+%5dms] %s\n' % (time.strftime('[%H:%M:%S]'),
                                          (time.time() - _LOG_T0) * 1000, msg))
    except Exception:
        pass


def resource_path(relative):
    """定位资源：onefile 解压到 sys._MEIPASS；onedir 用 exe 绝对路径目录；
    开发模式（python shell.py）用 __file__ 所在目录。
    """
    if getattr(sys, '_MEIPASS', None):
        base = sys._MEIPASS
    elif getattr(sys, 'frozen', False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        # 开发态：优先 exe 同级目录，兜底 __file__ 所在目录
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(exe_dir, relative)):
            base = exe_dir
        else:
            base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, relative)


def _read_cell_images(zf, max_each=500 * 1024, budget=3 * 1024 * 1024, max_n=20):
    """读 WPS「嵌入单元格图片」（=DISPIMG）→ {ID_xxx: {'mime':..., 'b64':...}}（2026-10-19 用户截图）。

    原理：WPS 把单元格图片存 xl/cellimages.xml（每个 xdr:pic 的 cNvPr@name = 单元格公式里的
    ID_xxx），图片本体在 xl/media/*，由 xl/_rels/cellimages.xml.rels 关联。
    安全闸门：单张 ≤500KB、总量 ≤3MB、最多 20 张（超出计数丢弃，前端明说）。
    找不到/解析失败一律返回空 dict —— 图片不能拖累表格导入主流程。
    """
    import base64
    out = {}
    skipped = 0
    try:
        names = set(zf.namelist())
        if 'xl/cellimages.xml' not in names:
            return out, skipped
        from xml.etree import ElementTree as ET
        rid_target = {}
        rels_path = 'xl/_rels/cellimages.xml.rels'
        if rels_path in names:
            rroot = ET.fromstring(zf.read(rels_path))
            for rel in rroot:
                rid = rel.get('Id')
                tgt = (rel.get('Target') or '').replace('\\', '/')
                if rid and tgt:
                    if tgt.startswith('/'):
                        tgt = tgt.lstrip('/')
                    elif not tgt.startswith('xl/'):
                        tgt = 'xl/' + tgt
                    rid_target[rid] = tgt
        ci_root = ET.fromstring(zf.read('xl/cellimages.xml'))
        for pic in ci_root.iter():
            if pic.tag.split('}')[-1] != 'pic':
                continue
            pid = None
            embed = None
            for sub in pic.iter():
                ln = sub.tag.split('}')[-1]
                if ln == 'cNvPr' and sub.get('name'):
                    pid = sub.get('name')
                elif ln == 'blip':
                    for k, v in sub.attrib.items():
                        if k.split('}')[-1] == 'embed':
                            embed = v
            if not pid or not embed:
                continue
            tgt = rid_target.get(embed)
            if not tgt or tgt not in names:
                skipped += 1
                continue
            if len(out) >= max_n:
                skipped += 1
                continue
            try:
                raw = zf.read(tgt)
            except Exception:
                skipped += 1
                continue
            if len(raw) > max_each or len(raw) > budget:
                skipped += 1
                continue
            lo = tgt.lower()
            mime = 'image/png' if lo.endswith('.png') else (
                'image/gif' if lo.endswith('.gif') else (
                    'image/bmp' if lo.endswith('.bmp') else 'image/jpeg'))
            out[pid] = {'mime': mime, 'b64': base64.b64encode(raw).decode('ascii')}
            budget -= len(raw)
    except Exception as e:
        try:
            _log('_read_cell_images 异常: %r' % e)
        except Exception:
            pass
    return out, skipped


class ShellAPI:
    """暴露给页面的窗口控制接口。公开方法=可调用；数据一律 _ 前缀。"""

    def __init__(self):
        self._win = None
        self._hwnd = None
        self._max = False
        self._shown = False
        self._set_show = None
        # 拖拽状态字段（init 时初始化，ready() 时也可重置）
        self._dragging = False
        self._resizing = False
        # ensure_height / ensure_size 之前的原尺寸（0=无待还原）
        self._saved_h = 0
        self._saved_t = 0
        self._saved_l = 0
        self._saved_w = 0
        # ★ 2026-09-29 分档：卡片档（上面的 _saved_*）与视图档（下面这组）各记各的，
        #   避免「卡开着切视图」时两边的还原互相吞掉，留下窗口顶部一截空白。
        self._saved_h_view = 0
        self._saved_t_view = 0
        # 计算稿态的偏好高度 + 「本次加高是切视图发起的」标记（2026-09-21）
        self._sheet_h = 0
        self._sheet_remember = False
        # ★ 2026-09-29 几何档位锁：js_api 是并发执行（boot 日志实锤两次 restore 同毫秒
        #   跑、还出现过「还原为 0」——读到被另一线程清掉的档位值，SetWindowPos 高度=0），
        #   所有档位读改写必须串行。
        self._geo_lock = threading.Lock()
        # 一键截图的状态（2026-09-21：非阻塞轮询用；None = 没有进行中的截图）
        self._snap_state = None

    def ready(self):
        """页面首屏渲染完成回调：此时再显示窗口，用户第一帧就是完整界面。"""
        if self._shown:
            return
        self._shown = True
        _t('页面ready 首屏完成'); _log('收到页面 ready 通知, 首屏完成')
        if self._set_show:
            self._set_show()
        # 窗口此时已存在，设置任务栏/标题栏图标（pywebview 无 icon 参数）
        hwnd = self._ensure_hwnd()
        _apply_window_icon(hwnd)
        # 默认置顶：启动后保持最上层，不被其他窗口盖住
        if hwnd:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
            _log('窗口已设为 TOPMOST, hwnd=%s' % hwnd)
        self._dragging = False
        self._resizing = False
        self._drag_cx = 0
        self._drag_cy = 0
        self._drag_l = 0
        self._drag_t = 0

    def _ensure_hwnd(self):
        """懒初始化 hwnd（窗口句柄在 create 后才可用）。

        2026-09-08 事故根因：WinForms 的 Control.Handle 禁止跨线程访问，而
        js_api 回调跑在 WebView2 的非 UI 线程 → 每次取句柄都抛异常 → hwnd=0
        → 拖动/缩放/最小化全是空操作。改用 Win32 EnumWindows 按「标题+本进程
        PID」查句柄，线程安全，不碰 WinForms 控件。
        """
        if self._hwnd:
            return self._hwnd
        if self._win is not None:
            try:
                self._hwnd = self._win.native.Handle.ToInt32()
            except Exception as e:
                _log('native.Handle 取句柄失败(预期,跨线程): %s' % e)
        if not self._hwnd:
            self._hwnd = _find_hwnd_by_pid(os.getpid(), TITLE)
            _log('EnumWindows 查句柄 = %s' % self._hwnd)
        return self._hwnd

    def _align_webview_child(self, hwnd):
        """★ 2026-09-29 治「关卡后窗口顶部露一截白」（用户实报，探针复现实锤）：
        壳在 js_api 后台线程直接 SetWindowPos 改父窗口，WinForms 给 WebView2 子窗口
        的同步是异步竞态——子窗口偶发不跟（实测停在父窗口下方 44px，多次操作后累计
        到 137px），顶部露白、页脚被裁，且不会自愈。
        这里把 Chrome_WidgetWin 子窗口强制掰回父窗口矩形（frameless 无边框：
        客户区=窗口区，子窗口坐标相对父窗口客户区，即 (0,0,w,h)）。
        返回掰正的子窗口数。"""
        prc = RC()
        user32.GetWindowRect(hwnd, ctypes.byref(prc))
        w, h = prc.r - prc.l, prc.b - prc.t
        if w <= 0 or h <= 0:
            return 0
        fixed = [0]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(ch, _):
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(ch, buf, 64)
            if buf.value.startswith('Chrome_WidgetWin'):
                crc = RC()
                user32.GetWindowRect(ch, ctypes.byref(crc))
                if (crc.l, crc.t, crc.r - crc.l, crc.b - crc.t) != (prc.l, prc.t, w, h):
                    user32.SetWindowPos(ch, 0, 0, 0, w, h,
                                        SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOCOPYBITS
                                        | SWP_ASYNCWINDOWPOS)
                    fixed[0] += 1
            return True

        user32.EnumChildWindows(hwnd, cb, 0)
        return fixed[0]

    def _align_soon(self, hwnd):
        """立即掰一次 + 延迟再掰两次：子窗口掉队是异步竞态，可能晚一拍才发生，
        150ms / 600ms 各补一枪，保证最终对齐。"""
        self._align_webview_child(hwnd)
        for d in (0.15, 0.6):
            threading.Timer(d, self._align_webview_child, args=(hwnd,)).start()

    def _animate_to(self, hwnd, L, T, W, H, duration_ms=240):
        """窗口直接落位到目标矩形（单次 SetWindowPos，瞬跳）。

        2026-10-08 用户反馈分段缓出动画「太慢太怪、像虫子爬」——根因是每帧一次
        SetWindowPos + sleep，WebView2 内容异步重绘跟不上窗口框，肉眼能数出每一帧。
        改为单次 SetWindowPos 直接到位，干净利落。duration_ms 保留参数仅为兼容调用方。
        """
        if not hwnd or (L, T, W, H) == (0, 0, 0, 0):
            return
        user32.SetWindowPos(hwnd, 0, int(L), int(T), int(W), int(H),
                            SWP_NOZORDER | SWP_NOACTIVATE)

    def ensure_height(self, want, remember=False):
        """临时把窗口加高到 want 像素（型钢卡片 / 切计算稿用），记住原位置/高度供还原。

        原位加高；若底部会超出屏幕，则上移贴住屏幕下沿（不遮挡卡片底部）。

        remember=True（切到计算稿用，2026-09-21）：**只要记过计算稿高度就完全按它来**
        （无论比 want 高还是矮 —— 用户拖矮了也得算数），没记过才用 want。
        配合 restore_height 里的回记逻辑，修「调完计算稿大小、切到简洁再切回来尺寸又变」。
        型钢/日期卡片用默认 False，不带这个语义（否则关卡片时会把卡片高度误记成计算稿高度）。

        ★ 2026-09-29 修「点柱墩卡片后窗口顶部多一截白」（用户实报）：
        根因是 _saved_h 只有**一份**，而卡片开合与视图切换会交叉调用 ——
        ① 在简洁视图开卡片：_saved_h 记下「简洁高度」；
        ② 卡片还开着就切到计算稿：viewWinSync 调 ensure_height(remember=True)，
           它看到 _saved_h 已非 0 就不再覆盖（对），但**关卡片时 restore_height
           会把窗口还原成「简洁高度」并清空 _saved_h**；
        ③ 此时视图已经是计算稿，却没了待还原记录 → 计算稿要的高度没还回去，
           窗口就多留了一截空白。
        修法：把「卡片档」与「视图档」分成两份记录，各自记、各自还，互不吞对方。
        """
        try:
            want = int(want)
        except (TypeError, ValueError):
            return False
        with self._geo_lock:
            self._sheet_remember = bool(remember)
            if remember and self._sheet_h:
                want = self._sheet_h
            hwnd = self._ensure_hwnd()
            if not hwnd:
                return False
            rc = RC()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            cur_h = rc.b - rc.t
            if cur_h >= want:
                return True
            # ★ 分档：切视图（remember=True）记在「视图档」，卡片（False）记在「卡片档」
            if remember:
                if not self._saved_h_view:
                    self._saved_h_view = cur_h
                    self._saved_t_view = rc.t
            else:
                if not self._saved_h:
                    self._saved_t = rc.t
                    self._saved_h = cur_h
            top = rc.t
            scr_h = user32.GetSystemMetrics(SM_CYSCREEN)
            # ★ 2026-10-08 轻量兜底（用户「矮窗口够不到柱墩卡」）：目标高度先钳进屏幕 ——
            #   矮屏幕 / 高 DPI 缩放下 want=780 可能比屏幕还高，原逻辑只挪 top 不缩高度，
            #   窗口底部整体顶出屏幕外，卡片下半截（填数表/按钮）就真的够不到了。
            #   钳矮后卡片靠内部滚动兜底（max-height:96vh + overflow-y:auto，页面自带）。
            if want > scr_h - 16:
                want = max(260, scr_h - 16)
            if top + want > scr_h - 8:
                top = max(8, scr_h - 8 - want)
            _log('窗口加高 %d→%d (top=%d, 档=%s)'
                 % (cur_h, want, top, '视图' if remember else '卡片'))
            # ★ 2026-10-08 平滑过渡（替代瞬跳）
            self._animate_to(hwnd, rc.l, top, rc.r - rc.l, want, 240)
        # ★ 子窗口对齐兜底（锁外）：加高同样会碰子窗口掉队，白边出现在底部同理要治
        if self._hwnd:
            self._align_soon(self._hwnd)
        return True

    def ensure_size(self, want_w, want_h):
        """加宽/加高窗口（大卡片要空间）——**只放大不缩小**，并记住原尺寸供还原。"""
        return self._set_size(want_w, want_h, False)

    def fit_size(self, want_w, want_h):
        """切视图用：**按 want 精确设置**窗口（可放大也可缩小）。

        和 ensure_size 分开是为了不破坏老行为：老壳只认 ensure_size（只放大不缩小），
        新前端优先调 fit_size，于是「切到计算稿 = 就是这个尺寸」能落地，
        而用户手动拖出来的尺寸在下次切视图时会被重新按视图尺寸摆正（符合「切视图=重排」直觉）。
        """
        return self._set_size(want_w, want_h, True)

    def set_alpha(self, pct):
        """窗口整体透明度（%）：100 = 不透明。贴图纸算量时调低能看穿窗口。

        整窗生效（含文字）：WS_EX_LAYERED + SetLayeredWindowAttributes(LWA_ALPHA)。
        下限夹到 30%，太透会看不清自己的字。
        """
        try:
            pct = int(float(pct))
        except (TypeError, ValueError):
            return False
        pct = max(30, min(100, pct))
        hwnd = self._ensure_hwnd()
        if not hwnd:
            return False
        try:
            user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
            user32.GetWindowLongW.restype = ctypes.c_long
            user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
            user32.SetWindowLongW.restype = ctypes.c_long
            user32.SetLayeredWindowAttributes.argtypes = [ctypes.c_void_p, ctypes.c_uint,
                                                          ctypes.c_ubyte, ctypes.c_uint]
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if not (ex & WS_EX_LAYERED):
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)
            user32.SetLayeredWindowAttributes(hwnd, 0, int(round(255 * pct / 100.0)), LWA_ALPHA)
            _log('窗口透明度设为 %d%%' % pct)
            return True
        except Exception as e:
            _log('设置透明度失败: %s' % e)
            return False

    def _set_size(self, want_w, want_h, force):
        try:
            want_w = int(want_w); want_h = int(want_h)
        except (TypeError, ValueError):
            return False
        with self._geo_lock:
            hwnd = self._ensure_hwnd()
            if not hwnd:
                return False
            rc = RC()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            cur_w, cur_h = rc.r - rc.l, rc.b - rc.t
            if not force and cur_w >= want_w and cur_h >= want_h:
                return True
            if force and cur_w == want_w and cur_h == want_h:
                return True
            if not self._saved_h:
                self._saved_t = rc.t
                self._saved_h = cur_h
                self._saved_l = rc.l
                self._saved_w = cur_w
            scr_w, scr_h = _work_area()   # 工作区（扣任务栏），窗口才能真正铺满
            want_w = min(want_w, scr_w - 16)
            want_h = min(want_h, scr_h - 16)
            left = min(rc.l, max(8, scr_w - 8 - want_w))
            top = min(rc.t, max(8, scr_h - 8 - want_h))
            _log('窗口改尺寸%s %dx%d -> %dx%d (left=%d top=%d) [平滑过渡]'
                 % ('(强制)' if force else '', cur_w, cur_h, want_w, want_h, left, top))
            # ★ 2026-10-08 平滑过渡（替代瞬跳）
            self._animate_to(hwnd, left, top, want_w, want_h, 240)
        if self._hwnd:
            self._align_soon(self._hwnd)
        return True

    def restore_height(self):
        """还原 ensure_height 之前的高度（无待还原则空操作）。

        ★ 2026-09-21 两处修正（用户反馈「调完计算稿大小、切到简洁再切回来尺寸又变」）：
        ① 若本次加高是「切计算稿」发起的（ensure_height 传了 remember=True），
           还原前先把**当前高度**记成计算稿态的偏好高度 —— 用户在计算稿里拖出来的
           尺寸不会再被"还原"吞掉，下次切回计算稿就是他拖的那个高度。
        ② **不再把宽度改回旧值** —— 视图切换根本不碰宽度，还原时也就不该动它，
           否则用户在任一视图里改过的宽度会被悄悄改回去。宽度现在是"全局"的。

        ★ 2026-09-29 改成「按档还原」：本次加高走的哪个档（视图 / 卡片），
           就还哪个档记下的高度。这样「卡开着切视图，再关卡」不会把窗口
           还原成错误的档，也不会留下顶部那截空白。
           若本次是**视图档**发起（_sheet_remember=True），顺手把当前高度记成
           计算稿态偏好高度（用户拖过的尺寸要算数）。
        """
        hwnd = self._ensure_hwnd()
        if not hwnd:
            return False
        if self._sheet_remember:
            return self._restore_view_height(hwnd)
        return self._restore_card_height(hwnd)

    def _restore_view_height(self, hwnd):
        """视图档还原：回到列表/简洁视图前的高度（若视图档没记过，则空操作）。"""
        with self._geo_lock:
            if not self._saved_h_view:
                return False
            rc = RC()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            cur_h, cur_w = rc.b - rc.t, rc.r - rc.l
            self._sheet_h = cur_h          # 记住用户拖出来的计算稿高度
            _log('记住计算稿态高度 = %d' % cur_h)
            _log('窗口高度还原(视图档)为 %d (宽 %d 保持不变) [平滑过渡]' % (self._saved_h_view, cur_w))
            # ★ 2026-10-08 平滑过渡（替代瞬跳）
            self._animate_to(hwnd, rc.l, self._saved_t_view, cur_w, self._saved_h_view, 240)
            self._saved_h_view = 0
            self._saved_t_view = 0
            # 视图切回简洁时，卡片档若有残留也一并清掉（不留孤档）
            if self._saved_h:
                _log('同时清掉残留的卡片档 %d' % self._saved_h)
                self._saved_h = 0
                self._saved_t = 0
                self._saved_w = 0
                self._saved_l = 0
        if self._hwnd:
            self._align_soon(self._hwnd)
        return True

    def _restore_card_height(self, hwnd):
        """卡片档还原：回到开卡前的高度。"""
        with self._geo_lock:
            if not self._saved_h:
                return False
            rc = RC()
            user32.GetWindowRect(hwnd, ctypes.byref(rc))
            cur_h, cur_w = rc.b - rc.t, rc.r - rc.l
            _log('窗口高度还原(卡片档)为 %d (宽 %d 保持不变) [平滑过渡]' % (self._saved_h, cur_w))
            # ★ 2026-10-08 平滑过渡（替代瞬跳）
            self._animate_to(hwnd, rc.l, self._saved_t, cur_w, self._saved_h, 240)
            self._saved_h = 0
            self._saved_w = 0
            self._saved_l = 0
        if self._hwnd:
            self._align_soon(self._hwnd)
        return True

    def minimize(self):
        _log('minimize 被调用, hwnd=%s' % self._ensure_hwnd())
        hwnd = self._ensure_hwnd()
        if hwnd:
            user32.ShowWindow(hwnd, SW_MINIMIZE)

    def close(self):
        # js_api 跑在 WebView2 的 JS 线程，PostQuitMessage 会投错线程队列关不掉；
        # 用 WM_CLOSE，SendMessageW 会把消息 marshal 到窗口 UI 线程的 WndProc。
        hwnd = self._ensure_hwnd()
        if hwnd:
            user32.SendMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

    def _ensure_drag_state(self):
        """确保拖拽相关字段已初始化（兼容 ready() 未触发的边缘情况）。

        2026-09-09 真机事故：JS 端未调用 api.ready() → _drag_l 等字段没设 →
        begin_drag 内 SetWindowPos 计算失败 → Promise reject → 顶部红色 JS 错误条。
        防御写法：所有字段按需懒初始化，绝不依赖 ready() 抢先触发。
        """
        if not hasattr(self, '_dragging'):
            self._dragging = False
        if not hasattr(self, '_resizing'):
            self._resizing = False
        if not hasattr(self, '_drag_cx'):
            self._drag_cx = self._drag_cy = 0
            self._drag_l = self._drag_t = 0
        if not hasattr(self, '_rz_cx'):
            self._rz_dir = ''
            self._rz_cx = self._rz_cy = 0
            self._rz_l = self._rz_t = self._rz_r = self._rz_b = 0

    def begin_drag(self):
        """标题栏按下：记录光标-窗口偏移进入拖拽态，移动由 JS mousemove 驱动。

        历史教训：Python 侧自建循环（SC_MOVE 模态移动、GetAsyncKeyState 轮询）
        在真机都拖不动——WebView2 持鼠标捕获时主窗口收不到消息。唯一可靠路径 =
        JS mousemove 上报屏幕坐标，Python 只做无状态 SetWindowPos。
        """
        self._ensure_drag_state()
        hwnd = self._ensure_hwnd()
        if not hwnd or self._max:
            return

        _log('begin_drag 调用, hwnd=%s max=%s' % (hwnd, self._max))

        pt, rc = PT(), RC()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        # 起点用 Win32 物理坐标记录；JS 的 e.screenX 属 CSS 坐标系（DPI 虚拟化时会
        # 有约 3% 偏差），因此拖动位移一律由 Python 侧 GetCursorPos 现读，不用 JS 坐标。
        self._drag_cx, self._drag_cy = pt.x, pt.y
        self._drag_l, self._drag_t = rc.l, rc.t
        self._dragging = True
        # 位移由 Python 侧 60Hz 轮询光标驱动：页面 mousemove 事件投递不稳定
        # （页面卡顿/失焦时会丢事件，实测出现拖不动或过冲），轮询不依赖页面。
        self._start_polling('_drag_thread', '_dragging', self._apply_drag)

    def _apply_drag(self):
        hwnd = self._ensure_hwnd()
        if not hwnd:
            return
        pt = PT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetWindowPos(hwnd, 0,
                            self._drag_l + (pt.x - self._drag_cx),
                            self._drag_t + (pt.y - self._drag_cy),
                            0, 0, SWP_NOSIZE | SWP_NOZORDER)

    def _start_polling(self, thread_attr, flag_attr, apply_fn):
        setattr(self, thread_attr, threading.Thread(
            target=self._poll_loop, args=(flag_attr, apply_fn), daemon=True))
        getattr(self, thread_attr).start()

    def _poll_loop(self, flag_attr, apply_fn):
        while getattr(self, flag_attr):
            try:
                apply_fn()
            except Exception as e:
                _log('轮询异常: %s' % e)
                return
            time.sleep(0.012)      # ~80Hz，比屏幕刷新快，拖动跟手不卡顿
        try:                      # 松手时补最后一帧，避免停在半帧位置
            apply_fn()
        except Exception:
            pass

    def drag_to(self, x, y):
        # 兼容旧前端：位移已由 Python 轮询接管，此处仅做保底同步
        if self._dragging:
            self._apply_drag()

    def end_drag(self):
        self._dragging = False
        t = getattr(self, '_drag_thread', None)
        if t and t.is_alive():
            t.join(timeout=0.2)

    # ══ 一键截图 ═══════════════════════════════════════════════════════════════
    #  2026-09-18 首版：ShellExecuteW('ms-screenclip:') 唤起系统截图 → 轮询剪贴板
    #  → GetClipboardData(CF_DIB) 取位图 → 补 14 字节 BMP 头 → base64 交回页面。
    #
    #  2026-09-21 改成「非阻塞 + 可取消」（用户真机反馈：取消截图后界面卡住）：
    #   旧版 snap_screen() 在**壳里**干等 45 秒 —— 用户按 Esc 取消后剪贴板永远不变，
    #   前端 await 也一直不返回，截图按钮灰着、看着就是卡死。
    #   现在拆成三个都能立刻返回的接口：
    #     snap_begin()  唤起系统截图，记下剪贴板序列号，马上返回
    #     snap_poll()   页面每 250ms 问一次；没截到回 {pending:True}，**永不阻塞**
    #     snap_cancel() 用户点底部「取消截图」时调，下次 poll 立刻收工
    #   老的 snap_screen() 保留（旧页面还在用它），内部改成 begin + 循环 poll。

    def _snap_api(self):
        """截图要用的 Win32 绑定（每次现建，开销可忽略）。

        为什么 ctypes 手写：嵌入式 Python 里没有 PIL / pywin32。
        自带类型别名，**不要** from ctypes import wintypes：PyInstaller 不自动收集
        这个子模块，打包后 import 直接失败（2026-09-18 真机踩到，截图一进来就炸）。
        """

        class wintypes(object):
            DWORD = ctypes.c_ulong
            HANDLE = ctypes.c_void_p
            HWND = ctypes.c_void_p
            LPCWSTR = ctypes.c_wchar_p

        u = ctypes.windll.user32
        k = ctypes.windll.kernel32
        sh = ctypes.windll.shell32

        u.GetClipboardSequenceNumber.restype = wintypes.DWORD
        u.GetClipboardData.restype = wintypes.HANDLE
        u.OpenClipboard.argtypes = [wintypes.HWND]
        k.GlobalLock.restype = ctypes.c_void_p
        k.GlobalLock.argtypes = [wintypes.HANDLE]
        k.GlobalUnlock.argtypes = [wintypes.HANDLE]
        k.GlobalSize.restype = ctypes.c_size_t
        k.GlobalSize.argtypes = [wintypes.HANDLE]
        sh.ShellExecuteW.restype = ctypes.c_void_p
        sh.ShellExecuteW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                     wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_int]

        CF_DIB = 8

        def read_dib():
            """剪贴板里若有位图，取出 DIB（含 BITMAPINFOHEADER），否则 None。"""
            if not u.OpenClipboard(None):
                return None
            try:
                h = u.GetClipboardData(CF_DIB)
                if not h:
                    return None
                p = k.GlobalLock(h)
                if not p:
                    return None
                try:
                    size = int(k.GlobalSize(h) or 0)
                    if size < 40:
                        return None
                    buf = ctypes.create_string_buffer(size)
                    ctypes.memmove(buf, p, size)
                    return buf.raw
                finally:
                    k.GlobalUnlock(h)
            finally:
                u.CloseClipboard()

        def to_bmp(dib):
            """DIB → (BMP 字节, 宽, 高)。结构不认就抛异常，让上层报错。"""
            bi_size = int.from_bytes(dib[0:4], 'little')
            w = int.from_bytes(dib[4:8], 'little', signed=True)
            h = int.from_bytes(dib[8:12], 'little', signed=True)
            bpp = int.from_bytes(dib[14:16], 'little')
            palette = 0 if bpp > 8 else (1 << bpp) * 4
            stride = ((abs(w) * bpp + 31) // 32) * 4
            real = bi_size + palette + stride * abs(h)
            if real <= len(dib):
                dib = dib[:real]            # GlobalSize 可能比实际大，按结构裁准
            offset = 14 + bi_size + palette
            head = b'BM' + (14 + len(dib)).to_bytes(4, 'little') + b'\x00\x00\x00\x00' \
                   + offset.to_bytes(4, 'little')
            return head + dib, abs(w), abs(h)

        return {'u': u, 'sh': sh, 'read_dib': read_dib, 'to_bmp': to_bmp}

    def _snap_fire(self, shell32):
        """唤起系统截图工具。返回 (rc, err)；err 非空表示没唤起来。

        nShowCmd 必须给 SW_SHOWNORMAL(1)：给 0(SW_HIDE) 时某些系统下截图工具不显形，
        用户就以为"按钮点了没反应"。<=32 都是失败码（2=找不到文件 / 31=无关联程序 …）。
        """
        rc = None
        try:
            rc = shell32.ShellExecuteW(None, 'open', 'ms-screenclip:', None, None, 1)
        except Exception as e:
            _log('snap: ShellExecuteW 抛异常 %s' % e)
        if not rc or int(rc) <= 32:
            _log('snap: ms-screenclip 唤起失败 rc=%s，改用 explorer 兜底' % rc)
            try:
                rc2 = shell32.ShellExecuteW(None, 'open', 'explorer.exe', 'ms-screenclip:', None, 1)
            except Exception as e:
                rc2 = None
                _log('snap: explorer 兜底抛异常 %s' % e)
            if not rc2 or int(rc2) <= 32:
                _log('snap: explorer 兜底也失败 rc=%s，放弃' % rc2)
                return rc, ('系统截图工具唤不起来（rc=%s），可能被安全软件拦了；'
                            '可直接 Win+Shift+S 后 Ctrl+V' % rc)
            return rc2, None
        return rc, None

    def snap_begin(self):
        """唤起系统截图并**立刻返回**（不阻塞）。页面随后用 snap_poll 拿结果。"""
        self._snap_state = None
        try:
            api = self._snap_api()
            seq = int(api['u'].GetClipboardSequenceNumber())
        except Exception as e:
            return {'ok': False, 'done': True, 'err': '截图模块初始化失败（%s）' % e}
        rc, err = self._snap_fire(api['sh'])
        if err:
            return {'ok': False, 'done': True, 'err': err}
        self._snap_state = {'api': api, 'seq': seq, 't0': time.time(), 'cancelled': False}
        _log('snap_begin: 已唤起系统截图（rc=%s），剪贴板序列号=%s' % (rc, seq))
        return {'ok': True, 'done': False}

    def snap_poll(self):
        """页面每 250ms 问一次：截到了回图；没截到回 {pending:True}（**永不阻塞**）。"""
        st = self._snap_state
        if not st:
            return {'ok': False, 'done': True, 'err': '没有正在进行的截图'}
        if st.get('cancelled'):
            self._snap_state = None
            _log('snap_poll: 用户已取消，收工')
            return {'ok': False, 'done': True, 'cancelled': True, 'err': '已取消截图'}
        try:
            cur = int(st['api']['u'].GetClipboardSequenceNumber())
        except Exception:
            cur = st['seq']
        if cur == st['seq']:                      # 剪贴板没动 → 还在等用户框选
            return {'ok': False, 'done': False, 'pending': True}
        st['seq'] = cur                           # 剪贴板变了：可能是图，也可能不是，取了再说
        dib = st['api']['read_dib']()
        if not dib or len(dib) < 40:
            return {'ok': False, 'done': False, 'pending': True}
        try:
            bmp, w, h = st['api']['to_bmp'](dib)
        except Exception as e:
            self._snap_state = None
            return {'ok': False, 'done': True, 'err': '读剪贴板位图出错（%s）' % e}
        self._snap_state = None
        _log('snap_poll: 取到 %dx%d，BMP %d 字节' % (w, h, len(bmp)))
        import base64
        return {'ok': True, 'done': True, 'b64': base64.b64encode(bmp).decode('ascii'),
                'mime': 'image/bmp', 'w': w, 'h': h}

    def snap_cancel(self):
        """用户点底部「取消截图」→ 下次 poll 立刻收工，不再干等。"""
        if self._snap_state:
            self._snap_state['cancelled'] = True
            _log('snap_cancel: 收到取消')
            return True
        return False

    def snap_screen(self, timeout=45):
        """【老接口 · 保留兼容】阻塞版：唤起后在本方法里干等，旧页面仍在用。

        新页面一律走 snap_begin / snap_poll / snap_cancel（不阻塞、可取消）。
        返回形状与老版一致：{'ok':True,'b64','mime',...} 或 {'ok':False,'err'}。
        """
        r = self.snap_begin()
        if not r.get('done'):
            t0 = time.time()
            while time.time() - t0 < timeout:
                time.sleep(0.25)
                r = self.snap_poll()
                if r.get('done'):
                    break
            else:                                  # 循环跑满 = 超时（没 break）
                self.snap_cancel()
                _log('snap_screen: 等待 %ds 超时，剪贴板始终没有新位图' % timeout)
                r = {'ok': False,
                     'err': '等了 %d 秒没等到截图 —— 多半是截图工具的「自动复制到剪贴板」没开'
                            '（截图工具 → 设置 → 自动复制到剪贴板）' % timeout}
        r.pop('done', None)
        return r

    def open_site(self):
        """左下角链接：用系统默认浏览器打开官网，避免 WebView2 内跳转顶掉计算器页。"""
        webbrowser.open('https://fhzjsj.cn')

    def begin_resize(self, direction):
        """边缘热区按下：记录起始光标/窗口矩形与方向，移动由 JS mousemove 驱动。"""
        hwnd = self._ensure_hwnd()
        if not hwnd:
            return

        pt, rc = PT(), RC()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.GetWindowRect(hwnd, ctypes.byref(rc))
        self._rz_dir = direction or ''
        self._rz_cx, self._rz_cy = pt.x, pt.y
        self._rz_l, self._rz_t, self._rz_r, self._rz_b = rc.l, rc.t, rc.r, rc.b
        self._resizing = True
        self._start_polling('_rz_thread', '_resizing', self._apply_resize)

    def _apply_resize(self):
        hwnd = self._ensure_hwnd()
        if not hwnd:
            return
        MIN_W, MIN_H = 560, 300
        pt = PT()
        user32.GetCursorPos(ctypes.byref(pt))
        dx, dy = pt.x - self._rz_cx, pt.y - self._rz_cy
        l, t, r, b = self._rz_l, self._rz_t, self._rz_r, self._rz_b
        d = self._rz_dir
        if 'e' in d:
            r = max(self._rz_r + dx, self._rz_l + MIN_W)
        if 's' in d:
            b = max(self._rz_b + dy, self._rz_t + MIN_H)
        if 'w' in d:
            l = min(self._rz_l + dx, self._rz_r - MIN_W)
        if 'n' in d:
            t = min(self._rz_t + dy, self._rz_b - MIN_H)
        user32.SetWindowPos(hwnd, 0, l, t, r - l, b - t, SWP_NOZORDER)

    def drag_resize(self, x, y):
        if self._resizing:
            self._apply_resize()

    def end_resize(self):
        self._resizing = False
        t = getattr(self, '_rz_thread', None)
        if t and t.is_alive():
            t.join(timeout=0.2)

    def maximize(self):
        hwnd = self._ensure_hwnd()
        if not hwnd:
            return
        _log('maximize 被调用, hwnd=%s 当前max=%s' % (hwnd, self._max))
        if self._max:
            user32.ShowWindow(hwnd, SW_RESTORE)
        else:
            user32.ShowWindow(hwnd, SW_MAXIMIZE)
        self._max = not self._max
        if self._hwnd:
            self._align_soon(self._hwnd)   # 最大化/还原同样会碰子窗口掉队

    # ══ 压缩包安全闸门（2026-10-19 糯米审核点⑤）
    # 「文件 ≤ 5MB/20MB」只约束**压缩后**大小 —— 一个 3MB 的 zip 可以解出几 GB（zip bomb）。
    # 这里补三条：解压后总体积、条目数、单条体积；另外加密文件直接拒（读不了的别硬解）。
    def _zip_guard(self, zf, max_uncompressed=64 * 1024 * 1024,
                   max_entries=4000, max_single=32 * 1024 * 1024):
        try:
            infos = zf.infolist()
        except Exception:
            return '压缩包读不了（可能损坏）'
        if len(infos) > max_entries:
            return '压缩包内文件数过多（%d 个），超出安全上限 %d' % (len(infos), max_entries)
        total = 0
        for i in infos:
            if getattr(i, 'flag_bits', 0) & 0x1:
                return '文件已加密（设了打开密码），请先在 Excel/WPS 里去掉密码另存'
            if i.file_size > max_single:
                return '压缩包内单个文件过大（%.1fMB），超出安全上限' % (i.file_size / 1048576.0)
            total += i.file_size
            if total > max_uncompressed:
                return '解压后体积超过 %.0fMB，超出安全上限（防止压缩炸弹）' % (max_uncompressed / 1048576.0)
        return None

    # ══ 可见工作表取值（只用于「元数据 vs 可见表」一致性核对）══
    # 糯米 P1-01：导出后用户在 Excel 里改了名字/公式，再导入时原来只读 customXml，
    # 等于把旧内容当新内容恢复。这里把可见表读出来对一遍，不一致就报给用户确认。
    def _sheet_cells(self, zf, sheet_path, shared):
        from xml.etree import ElementTree as _ET3
        import re as _re3
        NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        root = _ET3.fromstring(zf.read(sheet_path))
        cells = {}
        for row in root.iter(NS + 'row'):
            rn = row.get('r')
            if not rn:
                continue
            for c in row.findall(NS + 'c'):
                ref = c.get('r') or ''
                m = _re3.match(r'^([A-Z]+)(\d+)$', ref)
                if not m:
                    continue
                t = c.get('t')
                v = c.find(NS + 'v')
                is_el = c.find(NS + 'is')
                if t == 's' and v is not None and v.text is not None:
                    try:
                        val = shared[int(v.text)]
                    except Exception:
                        val = ''
                elif t == 'inlineStr' and is_el is not None:
                    val = ''.join(x.text or '' for x in is_el.iter(NS + 't'))
                else:
                    val = (v.text if v is not None else '') or ''
                cells[(m.group(1), int(m.group(2)))] = val
        return cells

    # ══ P0-B 方衡计算稿可逆导入（2026-10-19 需求基线）
    # 读导出的 .xlsm 里 customXml/item2.xml 的版本化恢复元数据（JSON in CDATA）。
    # 有元数据 → 前端走可逆恢复（差异预览/冲突/原子替换）；无 → 明确告知不是方衡格式，不猜。
    def import_calc_sheet(self, path):
        try:
            import zipfile
            import json as _json
            if not path or not str(path).lower().endswith(('.xlsm', '.xlsx')):
                return {'ok': False, 'error': '只支持 .xlsm / .xlsx 文件'}
            try:
                st = os.stat(path)
            except OSError as e:
                return {'ok': False, 'error': '读不到文件：%s' % e}
            if st.st_size > 20 * 1024 * 1024:
                return {'ok': False, 'error': '文件超过 20MB，超出安全上限'}
            try:
                zf = zipfile.ZipFile(path)
            except Exception:
                return {'ok': False, 'error': '不是有效的 Excel 文件（可能损坏或改了后缀）'}
            g = self._zip_guard(zf)
            if g:
                return {'ok': False, 'error': g}
            names = zf.namelist()
            raw = None
            # 正常路径：item2.xml；兼容：遍历 customXml/*.xml 找 forhanCalc 标记
            for cand in ['customXml/item2.xml'] + [n for n in names if n.startswith('customXml/') and n.endswith('.xml')]:
                if cand not in names:
                    continue
                try:
                    data = zf.read(cand).decode('utf-8', 'replace')
                except Exception:
                    continue
                if 'forhanCalc' in data:
                    raw = data
                    break
            if raw is None:
                # ★ 2026-10-19：第二份元数据在 veryHidden 工作表里（WPS 另存会冲掉 customXml，
                # 但不会删工作表内容）。按行号拼回 JSON。
                try:
                    import re as _re5
                    import html as _html5
                    for n in sorted(names):
                        if not (n.startswith('xl/worksheets/') and n.endswith('.xml')):
                            continue
                        try:
                            t = zf.read(n).decode('utf-8', 'replace')
                        except Exception:
                            continue
                        if 'forhan-calc' not in t:
                            continue
                        parts = []
                        for cm in _re5.finditer(r'<c\b[^>]*t="inlineStr"[^>]*>(.*?)</c>', t, _re5.S):
                            mref = _re5.search(r'r="([A-Z]+)(\d+)"', cm.group(0))
                            txt = ''.join(_re5.findall(r'<t[^>]*>(.*?)</t>', cm.group(1), _re5.S))
                            parts.append((int(mref.group(2)) if mref else 0, _html5.unescape(txt)))
                        parts.sort()
                        merged = ''.join(p[1] for p in parts)
                        if 'forhan-calc' in merged:
                            raw = '<forhanCalc><![CDATA[' + merged.replace(']]>', ']]]]><![CDATA[>') + ']]></forhanCalc>'
                            break
                except Exception:
                    raw = None
            if raw is None:
                return {'ok': False, 'noMeta': True,
                        'error': '这个文件不是方衡计算器导出的（或者是很早的旧版本），里面没有恢复用的信息。'
                                 '要取数的话，可以把它当普通表格，把行追加到末尾。'}
            # 取 CDATA 里的 JSON（可能存在多段 CDATA 拼接）
            import re as _re
            segs = _re.findall(r'<!\[CDATA\[(.*?)\]\]>', raw, _re.S)
            if not segs:
                return {'ok': False, 'error': '恢复信息损坏（元数据为空）'}
            js = ''.join(segs).replace(']]]]><![CDATA[>', ']]>')
            try:
                meta = _json.loads(js)
            except Exception as e:
                return {'ok': False, 'error': '恢复信息解析失败（%s）——文件可能被工具改动过' % e.__class__.__name__}
            if meta.get('app') != 'forhan-calc' or not isinstance(meta.get('rows'), list):
                return {'ok': False, 'error': '恢复信息不是计算稿格式（app 标记不符）'}
            ver = meta.get('metaVersion')
            if not isinstance(ver, int) or ver < 1:
                return {'ok': False, 'error': '恢复信息版本不认识（metaVersion=%r）' % ver}
            if ver > 3:
                return {'ok': False, 'error': '这份文件来自更新版本的计算器（元数据 v%s），当前版本读不了，请先升级' % ver}
            # ★ 2026-10-20 用户：「方衡自己导出的稿必须可以原样回来」——附图随行恢复。
            # 导出时 meta.imgIds = {行ID: DISPIMG 图 ID}；这里按 ID 从 cellimages 取回图，
            # 随 rows 一起回前端（前端存成附图、挂 shotId）。读不到就如实报，不假装有图。
            img_ids_meta = meta.get('imgIds') if isinstance(meta.get('imgIds'), dict) else {}
            images = {}
            img_unread = 0
            if img_ids_meta:
                try:
                    pool, _imgskip = _read_cell_images(zf, max_each=900 * 1024,
                                                       budget=8 * 1024 * 1024, max_n=40)
                    for _rid, _iid in img_ids_meta.items():
                        if not isinstance(_iid, str):
                            continue
                        if _iid in pool:
                            images[_iid] = pool[_iid]
                        else:
                            img_unread += 1
                except Exception:
                    images, img_unread = {}, 0

            rows = []
            seen_ids = set()
            for idx, r in enumerate(meta['rows']):
                # ★ 2026-10-19 糯米 P1-05：坏行不再静默跳过 —— 只要有一条认不出，整份不给恢复。
                if not isinstance(r, dict):
                    return {'ok': False, 'error': '恢复信息第 %d 行不是有效结构（文件可能被改过），不能恢复' % (idx + 1)}
                rid = r.get('id')
                if isinstance(rid, bool) or not isinstance(rid, (int, str)) or str(rid).strip() == '':
                    return {'ok': False, 'error': '恢复信息第 %d 行缺少有效行号（id），不能恢复' % (idx + 1)}
                key = str(rid)
                if key in seen_ids:
                    return {'ok': False, 'error': '恢复信息里有重复行号（id=%s），可能导致行错乱，已停止恢复' % key}
                seen_ids.add(key)
                kind = r.get('kind') or 'item'
                if kind not in ('item', 'section', 'blank'):
                    return {'ok': False, 'error': '恢复信息第 %d 行类型不认识（kind=%r）' % (idx + 1, kind)}
                qty = r.get('qtyRaw')
                if qty is not None:
                    if isinstance(qty, bool) or not isinstance(qty, (int, float)):
                        return {'ok': False, 'error': '恢复信息第 %d 行工程量不是数字，不能恢复' % (idx + 1)}
                    if qty != qty or qty in (float('inf'), float('-inf')):
                        return {'ok': False, 'error': '恢复信息第 %d 行工程量不是有限数值，不能恢复' % (idx + 1)}
                texts = {k: str(r.get(k) or '') for k in ('node', 'name', 'unit', 'expr', 'note')}
                if len(texts['name']) > 500 or len(texts['expr']) > 4000 or len(texts['note']) > 2000:
                    return {'ok': False, 'error': '恢复信息第 %d 行内容长度异常（可能不是计算稿），不能恢复' % (idx + 1)}
                rows.append({
                    'id': rid,
                    'kind': 'section' if kind == 'section' else 'item',
                    'node': texts['node'], 'name': texts['name'], 'unit': texts['unit'],
                    'expr': texts['expr'], 'note': texts['note'],
                    'qtyRaw': qty if qty is None else (float(qty) if isinstance(qty, float) else qty),
                })
            # 行上挂 imgId（只挂真能取到图的，避免前端空转）
            if images:
                for _row in rows:
                    _iid = img_ids_meta.get(str(_row.get('id')))
                    if isinstance(_iid, str) and _iid in images:
                        _row['imgId'] = _iid

            # ★ 摘要校验（糯米 P1-05）：digest 与行内容对不上 → 文件被改过或损坏，fail-closed。
            # 算法与前端一致：FNV-1a(32) over JSON(rows) —— 只做「结构一致性」核验，不做安全签名。
            try:
                import json as _json2
                def _js_num(v):
                    if isinstance(v, float) and v.is_integer():
                        return int(v)
                    return v
                norm = [{k: (_js_num(r.get(k)) if k == 'qtyRaw' else r.get(k))
                         for k in ('id', 'kind', 'node', 'name', 'unit', 'expr', 'note', 'qtyRaw')}
                        for r in meta['rows']]

                def _fnv(s):
                    hh = 5381
                    for ch in s:
                        hh = ((hh * 33) ^ ord(ch)) & 0xFFFFFFFF
                    return 'fnv1a-' + format(hh, 'x')

                # 摘要只是「内容没被意外改动」的一致性检查，不是签名。JS 与 Python 的
                # 序列化细节（空格/键序/浮点写法）可能不同 → 多算几种等价写法，任一匹配即放行。
                variants = set()
                for kw in ({'separators': (',', ':'), 'ensure_ascii': False},
                           {'separators': (',', ':'), 'ensure_ascii': True},
                           {'ensure_ascii': False},
                           {'sort_keys': True, 'ensure_ascii': False}):
                    variants.add(_fnv(_json2.dumps(norm, **kw)))
                want = meta.get('digest')
                if isinstance(want, str) and want.startswith('fnv1a-') and want not in variants:
                    return {'ok': False, 'error': '恢复信息校验不通过（内容与摘要对不上，文件可能被改过），为了不覆盖你的稿子，已停止恢复'}
            except Exception:
                pass
            # ★ 2026-10-19 糯米 P1-01/02：可见工作表整体读出来 ——
            # ① 与元数据比对报「改了几处」（和导出那一刻比）；
            # ② 把可见内容组装成 rowsEdited，前端给用户真实的选择：
            #    「用 Excel 新内容恢复」/「用导出时的内容恢复」。不再只有"自己补"一条路。
            # 结构变了（插行/删行/换行类型）→ structure_changed 给原因，fail-closed 拦截。
            visible_diff = {'count': 0, 'samples': []}
            edited_count = 0
            samples = []
            rows_edited = []
            structure_changed = ''
            legacy_pos = False
            qty_only_count = 0
            qty_only_samples = []
            has_appliable = False
            prechecks = []   # ★ 先初始化：可见分析块里的旧件警告要往里写（糯米 245e7ed-③）
            try:
                shared = []
                if 'xl/sharedStrings.xml' in names:
                    from xml.etree import ElementTree as _ET4
                    sroot = _ET4.fromstring(zf.read('xl/sharedStrings.xml'))
                    NSm = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
                    for si in sroot.findall(NSm + 'si'):
                        shared.append(''.join(t.text or '' for t in si.iter(NSm + 't')))
                sheet_paths = [n for n in names if n.startswith('xl/worksheets/sheet') and n.endswith('.xml')]
                sheet_paths.sort()
                if sheet_paths:
                    def _fmtq(v):
                        try:
                            return ('%.4f' % float(v)).rstrip('0').rstrip('.')
                        except Exception:
                            return str(v)

                    def _norm_expr(s):
                        # 导出会把 ×÷ 等归一成 *÷；比较两侧都归一到同一形状，避免拿显示写法当「被改过」
                        t = str(s or '')
                        for a, b in (('×', '*'), ('÷', '/'), ('－', '-'), ('—', '-'), ('–', '-'),
                                     ('（', '('), ('）', ')'), ('，', ','), ('　', ''), (' ', '')):
                            t = t.replace(a, b)
                        return t

                    cells = self._sheet_cells(zf, sheet_paths[0], shared)
                    # ★ 行 ID 锚点（H 隐藏列，糯米 3cca5af-01）：每行核对导出时写的 ID，
                    #   插行/删行/移动行立刻暴露，不会把 A 行的内容安到 B 行头上。
                    hid = {}
                    for (col, rn), val in cells.items():
                        if col == 'H' and str(val or '').strip():
                            try:
                                hid[rn] = str(int(float(str(val).strip())))
                            except (TypeError, ValueError):
                                hid[rn] = str(val).strip()
                    legacy_pos = (not hid)   # 旧版导出没有 H 列 → 只能按行位置对齐，要提示
                    if not legacy_pos:
                        title_off0 = 1 if meta.get('title') else 0
                        for j0, mr0 in enumerate(meta['rows']):
                            r0 = 3 + j0 + title_off0
                            want = str(mr0.get('id'))
                            if hid.get(r0) != want:
                                structure_changed = ('第 %d 行和导出时的行号记录对不上 —— 文件里可能插了行/删了行/移动了行。'
                                                     '请撤销这些结构改动后重新保存，或改用「当普通表格导入」' % r0)
                                break
                    title_off = 1 if meta.get('title') else 0
                    n_meta = len(meta['rows'])
                    last_rnum = 3 + n_meta - 1 + title_off
                    # 结构核对 ①：末尾多行
                    for (col, rn), val in cells.items():
                        if rn > last_rnum and str(val or '').strip():
                            structure_changed = '文件末尾多了内容（第 %d 行）—— 可能插了行' % rn
                            break
                    # 结构核对 ②：逐行按导出时的形状核对，同时把可见内容读成「新内容行」
                    if not structure_changed:
                        for j, mr in enumerate(meta['rows']):
                            if not isinstance(mr, dict):
                                continue
                            rnum = 3 + j + title_off
                            mkind = mr.get('kind') or 'item'
                            if mkind == 'blank':
                                rowvals = [str(cells.get((c, rnum), '') or '') for c in ('A', 'B', 'C', 'D', 'E', 'F', 'G')]
                                if any(v.strip() for v in rowvals):
                                    structure_changed = '第 %d 行原来是空行，现在有内容 —— 可能插了行' % rnum
                                    break
                                rows_edited.append({'id': mr.get('id'), 'kind': 'blank', 'node': '', 'name': '',
                                                    'expr': '', 'unit': '', 'note': '', 'qtyRaw': None})
                                continue
                            if mkind == 'section':
                                vname = str(cells.get(('A', rnum), '') or '')
                                vexpr = str(cells.get(('D', rnum), '') or '')
                                if vexpr.strip():
                                    structure_changed = '第 %d 行原来是部位行，现在有计算式 —— 结构变了' % rnum
                                    break
                                rows_edited.append({'id': mr.get('id'), 'kind': 'section', 'node': '', 'name': vname,
                                                    'expr': '', 'unit': '', 'note': '', 'qtyRaw': None})
                                if vname != str(mr.get('name') or ''):
                                    edited_count += 1
                                    if len(samples) < 12:
                                        samples.append('第 %d 行 部位名「%s」→「%s」' % (rnum, str(mr.get('name') or '')[:20], vname[:20]))
                                continue
                            # 明细行：B 部位 / C 名称 / D 计算式 / E 单位 / G 备注 / F 工程量
                            vnode = str(cells.get(('B', rnum), '') or '')
                            vname = str(cells.get(('C', rnum), '') or '')
                            vexpr = str(cells.get(('D', rnum), '') or '')
                            vunit = str(cells.get(('E', rnum), '') or '')
                            vnote = str(cells.get(('G', rnum), '') or '')
                            vqty_raw = cells.get(('F', rnum))
                            vqty = None
                            if vqty_raw not in (None, ''):
                                try:
                                    vqty = float(vqty_raw)
                                except (TypeError, ValueError):
                                    vqty = None   # F 里写了文字（如「待确认」）→ 工程量按空处理，但算「改过」
                            mname = str(mr.get('name') or '')
                            munit = str(mr.get('unit') or '')
                            mexpr = str(mr.get('expr') or '')
                            mnote = str(mr.get('note') or '')
                            mnode = str(mr.get('node') or '')
                            mqty = mr.get('qtyRaw')
                            diffs = []
                            if vname != mname:
                                diffs.append('名称「%s」→「%s」' % (mname[:20], vname[:20]))
                            if vunit != munit:
                                diffs.append('单位「%s」→「%s」' % (munit[:10], vunit[:10]))
                            if vexpr != mexpr and _norm_expr(vexpr) != _norm_expr(mexpr):
                                diffs.append('计算式「%s」→「%s」' % (mexpr[:24], vexpr[:24]))
                            if vnote != mnote and not _is_img_placeholder_change(vnote, mnote):
                                # 排除 WPS 自动把图片写成 =DISPIMG(...)（用户 2026-10-21 实报：
                                # 只是打开看了一眼文件，就被报「改过 4 处」）
                                diffs.append('备注「%s」→「%s」' % (mnote[:16], vnote[:16]))
                            if vnode != mnode:
                                diffs.append('部位「%s」→「%s」' % (mnode[:10], vnode[:10]))
                            if isinstance(mqty, (int, float)):
                                if vqty is None:
                                    if mqty != 0:
                                        diffs.append('工程量 %s → 空' % _fmtq(mqty))
                                elif abs(vqty - float(mqty)) > 5e-5:   # 半个显示位（导出按 4 位小数取整）
                                    diffs.append('工程量 %s → %s' % (_fmtq(mqty), _fmtq(vqty)))
                            elif mqty is None and vqty is not None:
                                diffs.append('工程量 空 → %s' % _fmtq(vqty))
                            if diffs:
                                edited_count += 1
                                if len(samples) < 12:
                                    samples.append('第 %d 行：%s' % (rnum, '；'.join(diffs)))
                                qty_diffs = [d for d in diffs if d.startswith('工程量')]
                                other_diffs = [d for d in diffs if not d.startswith('工程量')]
                                if other_diffs:
                                    # 有可应用字段（名称/单位/计算式/备注/部位）
                                    has_appliable = True
                                # ★ 糯米 245e7ed-①：计算器不存「手改工程量」，F 里改的数恢复后会按
                                #   计算式重算 —— 这类改动单独列出来明说，不许混进"保住新改动"的承诺。
                                if qty_diffs:
                                    qty_only_count += 1
                                    if len(qty_only_samples) < 12:
                                        qty_only_samples.append('第 %d 行：%s' % (rnum, '；'.join(qty_diffs)))
                            # ★ 糯米 d13222d-②：rowsEdited 必须是**整份行集**（没改的行用元数据基线），
                            #   否则前端拿它整稿替换就缺行。任何明细行都进，改了用新值、没改用原值。
                            rows_edited.append({'id': mr.get('id'), 'kind': 'item', 'node': vnode, 'name': vname,
                                                'expr': vexpr, 'unit': vunit, 'note': vnote, 'qtyRaw': vqty})
                if legacy_pos and not structure_changed:
                    prechecks.append('这是旧版导出（没有行号记录），按行位置对齐 —— 如果在 Excel 里插过/删过行，恢复会错位，请确认')
            except Exception as e:
                # ★ 糯米 245e7ed-③：可见内容读不出来不能装没事 —— fail-closed 给拦截原因
                structure_changed = '文件可见内容读不出来（%s），不能安全恢复。可改用「当普通表格导入」取数' % e.__class__.__name__
                visible_diff = {'count': 0, 'samples': [], 'unreadable': True}

            # ★ 2026-10-19 糯米审核点②：可应用闭环必须同时给「哪些东西没恢复」的预检，
            # 不能让用户点完「恢复」才发现附图/额外表没了。只报事实、只提示，不拦（预览里可取消）。
            # （prechecks 已在上面初始化 —— 旧件警告在可见分析块里就要写进来）
            try:
                import re as _re2
                media = [n for n in names if n.startswith('xl/media/')]
                if media:
                    _n_img = len(images)
                    if _n_img:
                        prechecks.append('源文件含 %d 张图片：其中 %d 张附图会随行恢复' % (len(media), _n_img))
                    else:
                        prechecks.append('源文件含 %d 张图片（水印不算；附图没找到对应记录，恢复后不会回到计算稿）' % len(media))
                sheets = [n for n in names if _re2.match(r'xl/worksheets/sheet\d+\.xml$', n)]
                # 我们自己写的那张 veryHidden 元数据表不算「额外工作表」
                user_sheets = []
                for n in sheets:
                    try:
                        if 'forhan-calc' in zf.read(n).decode('utf-8', 'replace'):
                            continue
                    except Exception:
                        pass
                    user_sheets.append(n)
                if len(user_sheets) > 1:
                    prechecks.append('源文件有 %d 个工作表，只会恢复当前这张计算稿' % len(user_sheets))
                sheet_xml = ''
                for n in sheets:
                    try:
                        sheet_xml = zf.read(n).decode('utf-8', 'replace')
                        break
                    except Exception:
                        continue
                # 工作表 XML 必须能解析 —— 损坏的表不能当「干净」通过（糯米 P1-05）
                try:
                    from xml.etree import ElementTree as _ET2
                    _ET2.fromstring(sheet_xml.encode('utf-8')) if sheet_xml else None
                except Exception:
                    return {'ok': False, 'error': '工作表结构损坏（XML 解析失败），不能按计算稿安全恢复。若只要取数，请用「当普通表格导入」'}
                nmerge = len(_re2.findall(r'<mergeCell\s', sheet_xml))
                # 第 1 行品牌行 + 标题行 + 每个部位行本来就是我们自己合并的（A:G），
                # 不算「额外格式丢失」，只有多出来的才提示。
                expected_merge = 1 + (1 if meta.get('title') else 0) + sum(
                    1 for mr in meta.get('rows') or [] if isinstance(mr, dict) and mr.get('kind') == 'section')
                if nmerge > expected_merge:
                    prechecks.append('源文件有 %d 处额外合并单元格，恢复后按内容重建，不保留合并' % (nmerge - expected_merge))
                nhidden = len(_re2.findall(r'<row\b[^>]*\bhidden="1"', sheet_xml))
                if nhidden:
                    prechecks.append('源文件有 %d 行被隐藏，恢复后会照常显示' % nhidden)
                ncolhidden = len(_re2.findall(r'<col\b[^>]*\bhidden="1"', sheet_xml))
                if ncolhidden > 1:   # 我们自己的隐藏 H 列（行 ID）不算
                    prechecks.append('源文件有 %d 列被隐藏，恢复后会照常显示' % ncolhidden)
                if _re2.search(r'<hyperlink\s', sheet_xml):
                    prechecks.append('源文件含超链接，恢复后不会保留')
                if any(n.startswith('xl/externalLinks/') for n in names):
                    prechecks.append('源文件含外部链接，恢复后不会保留（数值按元数据回填）')
                if any(n.startswith('xl/comments') or n.startswith('xl/threadedComments') for n in names):
                    prechecks.append('源文件含单元格批注，恢复后不会保留')
                if any(n.startswith('xl/embeddings/') or n.startswith('xl/ctrlProps/') for n in names):
                    prechecks.append('源文件含嵌入对象/控件，恢复后不会保留（也不会执行）')
                if any(n.lower().endswith('vbaproject.bin') for n in names):
                    prechecks.append('源文件含宏（VBA），恢复不执行也不保留宏')
                # 隐藏工作表：workbook.xml 里 state="hidden"
                try:
                    wb = zf.read('xl/workbook.xml').decode('utf-8', 'replace')
                    nsheets_hidden = len(_re2.findall(r'<sheet\b[^>]*state="hidden"', wb))
                    if nsheets_hidden:
                        prechecks.append('源文件有 %d 个隐藏工作表，不在恢复范围内' % nsheets_hidden)
                except Exception:
                    pass
            except Exception:
                pass
            return {'ok': True, 'meta': {k: meta.get(k) for k in ('app', 'metaVersion', 'exportedAt', 'title', 'digest')},
                    'rows': rows, 'total': len(rows), 'prechecks': prechecks,
                    'visibleDiff': visible_diff,
                    'rowsEdited': rows_edited, 'editedCount': edited_count, 'editedSamples': samples,
                    'qtyOnlyCount': qty_only_count, 'qtyOnlySamples': qty_only_samples,
                    'hasAppliableEdits': has_appliable,
                    'images': images, 'imgUnread': img_unread,
                    'structureChanged': structure_changed}
        except Exception as e:
            try:
                _log('import_calc_sheet 异常: %r' % e)
            except Exception:
                pass
            return {'ok': False, 'error': '读取失败（%s）' % e.__class__.__name__}

    # ══ Excel 安全导入（2026-10-19 用户指令：登录 + Excel 安全导入）══
    # 文件选择：win32 共对话框（comdlg32），路径不经过前端 JS——前端只拿到解析结果，
    # 「用户选了哪个盘哪个文件」这类本机信息不出壳。
    def pick_xlsx(self):
        try:
            import ctypes
            from ctypes import wintypes
            # OPENFILENAMEW 布局（x64）
            class OPENFILENAMEW(ctypes.Structure):
                _fields_ = [
                    ('lStructSize', wintypes.DWORD), ('hwndOwner', wintypes.HWND),
                    ('hInstance', wintypes.HINSTANCE), ('lpstrFilter', wintypes.LPCWSTR),
                    ('lpstrCustomFilter', wintypes.LPWSTR), ('nMaxCustFilter', wintypes.DWORD),
                    ('nFilterIndex', wintypes.DWORD), ('lpstrFile', wintypes.LPWSTR),
                    ('nMaxFile', wintypes.DWORD), ('lpstrFileTitle', wintypes.LPWSTR),
                    ('nMaxFileTitle', wintypes.DWORD), ('lpstrInitialDir', wintypes.LPCWSTR),
                    ('lpstrTitle', wintypes.LPCWSTR), ('Flags', wintypes.DWORD),
                    ('nFileOffset', wintypes.WORD), ('nFileExtension', wintypes.WORD),
                    ('lpstrDefExt', wintypes.LPCWSTR), ('lCustData', wintypes.LPARAM),
                    ('lpfnHook', ctypes.c_void_p), ('lpTemplateName', wintypes.LPCWSTR),
                    ('pvReserved', ctypes.c_void_p), ('dwReserved', wintypes.DWORD),
                    ('FlagsEx', wintypes.DWORD),
                ]
            buf = ctypes.create_unicode_buffer(1024)
            ofn = OPENFILENAMEW()
            ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
            ofn.hwndOwner = self._hwnd or 0
            # ★ 2026-10-19：我们自己的导出产物是 .xlsm（F4 名称方案），
            # 过滤器和默认扩展名必须带上 .xlsm，否则用户在选择框里根本看不到刚导出的计算稿。
            ofn.lpstrFilter = ('方衡计算稿 / Excel (*.xlsm;*.xlsx)\0*.xlsm;*.xlsx\0'
                               'Excel 工作簿 (*.xlsx)\0*.xlsx\0'
                               '所有文件 (*.*)\0*.*\0')
            ofn.nFilterIndex = 1
            ofn.lpstrFile = ctypes.cast(buf, ctypes.c_wchar_p)   # ★ 必须 cast：结构体字段是 LPWSTR，
                                                               # 直接赋 create_unicode_buffer 会抛
                                                               # TypeError(c_wchar_Array vs c_wchar_p)，
                                                               # 表现为「点导入没反应」（异常被吞成返回空串）
            ofn.nMaxFile = 1024
            ofn.lpstrTitle = '选择要导入的 Excel 文件（.xlsm / .xlsx）'
            ofn.lpstrDefExt = 'xlsm'
            ofn.Flags = 0x00080000 | 0x00000400 | 0x00001000   # OFN_EXPLORER|OFN_FILEMUSTEXIST|OFN_PATHMUSTEXIST
            ok = ctypes.windll.comdlg32.GetOpenFileNameW(ctypes.byref(ofn))
            return buf.value if ok else ''
        except Exception as e:
            try:
                _log('pick_xlsx 异常: %r' % e)
            except Exception:
                pass
            # ★ 2026-10-19：异常不再静默返回空串（前端会当成「用户取消」，表现成「点导入没反应」）。
            # 回一个带 #ERR 前缀的串，前端据此弹出真实原因。
            return '#ERR' + (str(e) or e.__class__.__name__)

    # 设计取舍：用 Python 标准库（zipfile + xml.etree）直接解析 .xlsx（本质是 zip+XML），
    # **不引入 openpyxl**（py38 环境无此库；零新依赖 = 零供应链风险，代码可控可审）。
    # 「安全」闸门（全部硬编码，超限立即拒绝，不静默截断大文件）：
    #   文件 ≤ 5MB · 行 ≤ 2000 · 列 ≤ 20 · 单元格 ≤ 200 字符 · 解析异常一律结构化返回
    # 只读第一个工作表的数据（数值/字符串/内联字符串），公式取缓存值，样式全忽略。
    def import_excel(self, path):
        try:
            import zipfile
            from xml.etree import ElementTree as ET
            NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
            RELNS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

            if not path or not str(path).lower().endswith(('.xlsx', '.xlsm')):
                return {'ok': False, 'error': '只支持 .xlsx / .xlsm 文件（旧版 .xls 请先在 Excel/WPS 里另存为 .xlsx）'}
            try:
                st = os.stat(path)
            except OSError as e:
                return {'ok': False, 'error': '读不到文件：%s' % e}
            if st.st_size > 5 * 1024 * 1024:
                return {'ok': False, 'error': '文件超过 5MB（%.1fMB）。安全导入上限 5MB，请先删减再导' % (st.st_size / 1048576.0)}
            try:
                zf = zipfile.ZipFile(path)
            except Exception:
                return {'ok': False, 'error': '不是有效的 .xlsx / .xlsm 文件（可能是改了后缀的其他格式）'}
            g = self._zip_guard(zf)
            if g:
                return {'ok': False, 'error': g}

            # 1) 共享字符串表（xlsxt 的文本多半存这里）
            shared = []
            if 'xl/sharedStrings.xml' in zf.namelist():
                try:
                    root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
                    for si in root.findall(NS + 'si'):
                        shared.append(''.join(t.text or '' for t in si.iter(NS + 't')))
                except Exception:
                    shared = []

            # 2) 定位第一个工作表（workbook.xml 第一行 sheet → rels → 真实文件名）
            # 2026-10-19 修：workbook.xml 缺失/异常时 first 从未赋值 → 结尾 UnboundLocalError，
            # 整单被兜成「解析失败」；先给默认值。
            first = None
            sheet_target = 'xl/worksheets/sheet1.xml'
            try:
                wb = ET.fromstring(zf.read('xl/workbook.xml'))
                first = wb.find(NS + 'sheets/' + NS + 'sheet')
                rid = first.get(RELNS + 'id') if first is not None else None
                if rid:
                    rels = ET.fromstring(zf.read('xl/_rels/workbook.xml.rels'))
                    for rel in rels:
                        if rel.get('Id') == rid:
                            t = rel.get('Target') or ''
                            t = t.lstrip('/')
                            if not t.startswith('xl/'):
                                t = 'xl/' + t
                            sheet_target = t
                            break
            except Exception:
                pass  # 定位失败就退回默认名
            if sheet_target not in zf.namelist():
                return {'ok': False, 'error': '工作簿里找不到可读的工作表'}

            # 3) 解析行：★ 按 r="C5" 列号对齐（红队 P0 实锤：xlsx 空单元格默认不写进 XML，
            #    只按出现顺序 append 会让「中间空格」的行整体左移错位）；公式单元格取缓存值
            root = ET.fromstring(zf.read(sheet_target))
            rows_out = []
            warn_cell = False      # 有单元格被截到 200 字
            warn_col = False       # 有行的列数超 20 被砍
            import re as _re
            for row in root.iter(NS + 'row'):
                # 列号折算：'A'→0, 'Z'→25, 'AA'→26（行号后缀去掉）
                def _col_idx(ref):
                    m = _re.match(r'([A-Z]+)', ref or '')
                    if not m:
                        return None
                    n = 0
                    for ch in m.group(1):
                        n = n * 26 + (ord(ch) - 64)
                    return n - 1
                vals = {}
                max_ci = -1
                for c in row.iter(NS + 'c'):
                    ci = _col_idx(c.get('r'))
                    if ci is None:
                        ci = max_ci + 1   # 无 r 属性的畸形文件退回顺序填充
                    if ci > 19:
                        warn_col = True
                        continue          # 超 20 列：丢弃但记录，前端明说
                    t_attr = c.get('t')
                    text = ''
                    if t_attr == 's':
                        v = c.find(NS + 'v')
                        if v is not None and v.text is not None:
                            try:
                                idx = int(v.text)
                                text = shared[idx] if 0 <= idx < len(shared) else ''
                            except Exception:
                                text = ''
                    elif t_attr == 'inlineStr':
                        is_el = c.find(NS + 'is')
                        if is_el is not None:
                            text = ''.join(x.text or '' for x in is_el.iter(NS + 't'))
                    else:
                        v = c.find(NS + 'v')
                        if v is not None and v.text is not None:
                            text = v.text
                        # WPS 嵌入单元格图片（2026-10-19 糯米 5351544 P1-03）：
                        # 标准写法是 <f>_xlfn.DISPIMG("ID_x",1)</f><v>0</v> —— 只读缓存值只会拿到 0，
                        # 图片 ID 就丢了。凡公式里带 DISPIMG 的，一律把公式串交给前端提 ID。
                        f_el = c.find(NS + 'f')
                        if f_el is not None and f_el.text and 'DISPIMG' in f_el.text.upper():
                            text = '=' + f_el.text.lstrip('=')
                    if len(text) > 200:
                        text = text[:200]
                        warn_cell = True
                    vals[ci] = text
                    if ci > max_ci:
                        max_ci = ci
                if max_ci < 0:
                    continue
                # 定长行（按本行最末列补空），再去行尾连续空列
                vals_list = [vals.get(i, '') for i in range(max_ci + 1)]
                while vals_list and vals_list[-1] == '':
                    vals_list.pop()
                if not vals_list:
                    continue
                rows_out.append(vals_list)
                if len(rows_out) >= 2000:
                    break

            total_rows = len(rows_out)
            if total_rows == 0:
                return {'ok': False, 'error': '表里没有可读的数据行（全空？）'}
            # WPS 嵌入单元格图片（=DISPIMG）：能取到就随行带上，取不到也不挡导入
            cell_images, img_skipped = _read_cell_images(zf)
            return {'ok': True, 'rows': rows_out, 'total': total_rows,
                    'sheetName': (first.get('name') if first is not None else '') or '',
                    'warnCell': warn_cell, 'warnCol': warn_col,
                    'images': cell_images, 'imgSkipped': img_skipped,
                    'atCap': (total_rows >= 2000)}
        except Exception as e:
            try:
                _log('import_excel 异常: %r' % e)
            except Exception:
                pass
            return {'ok': False, 'error': '解析失败（%s）。文件可能损坏或加密，请先在 Excel/WPS 里能正常打开' % e.__class__.__name__}

    # ══ 一键升级（2026-10-19 用户拍板）══
    # 下载安装包到临时目录 → SHA256 校验 → 前端再调 upgrade_run 拉起安装器。
    # ★ 糯米 3cca5af-06：缺 sha256 / 非 https / 非官方域名 → 一律拒绝自动升级，
    #   回落人工下载；upgrade_run 只认「本轮刚校验过的那个文件」，不接任意路径。
    _upgrade_verified = set()   # 类级：本进程内「已通过校验的安装包路径」

    def upgrade_download(self, url, sha256, name):
        # 社区版：不做自动升级（前端已桩化，此入口双保险）
        return {'ok': False, 'error': '社区版不含自动升级。新版本请关注官网 fhzjsj.cn'}
        try:
            import urllib.request
            import hashlib
            import tempfile
            from urllib.parse import urlparse
            url = str(url or '').strip()
            want = str(sha256 or '').strip().lower()
            if not url or not url.startswith('https://'):
                return {'ok': False, 'error': '下载地址不是官方加密链接，不能自动升级。请去官网 fhzjsj.cn 手动下载'}
            host = (urlparse(url).hostname or '').lower()
            if host not in ('fhzjsj.cn', 'www.fhzjsj.cn'):
                return {'ok': False, 'error': '下载地址不在官方域名内，不能自动升级。请去官网 fhzjsj.cn 手动下载'}
            if len(want) != 64 or any(c not in '0123456789abcdef' for c in want):
                return {'ok': False, 'error': '新版本没带校验信息，不能自动升级。请去官网 fhzjsj.cn 手动下载'}
            fn = 'fh_setup_' + str(name or 'setup.exe')
            fn = ''.join(ch for ch in fn if ch.isalnum() or ch in '._-') or 'fh_setup.exe'
            dst = os.path.join(tempfile.gettempdir(), fn)
            req = urllib.request.Request(url, headers={'User-Agent': 'forhan-calc-updater'})
            with urllib.request.urlopen(req, timeout=120) as resp, open(dst, 'wb') as f:
                h = hashlib.sha256()
                while True:
                    b = resp.read(65536)
                    if not b:
                        break
                    f.write(b)
                    h.update(b)
            got = h.hexdigest().lower()
            if got != want:
                try:
                    os.remove(dst)
                except Exception:
                    pass
                self._upgrade_verified.discard(dst)
                return {'ok': False, 'error': '下载的安装包校验不对（可能没下完整），已删掉，请稍后再试'}
            self._upgrade_verified.add(dst)
            return {'ok': True, 'path': dst, 'size': os.path.getsize(dst)}
        except Exception as e:
            try:
                _log('upgrade_download 异常: %r' % e)
            except Exception:
                pass
            return {'ok': False, 'error': '下载失败（%s），请检查网络后重试' % e.__class__.__name__}

    def upgrade_run(self, path):
        # 社区版：不做自动升级
        return {'ok': False, 'error': '社区版不含自动升级。新版本请关注官网 fhzjsj.cn'}
        try:
            path = str(path or '')
            # 只允许执行「本进程刚校验通过」的那个文件 —— 别的路径一律不碰（糯米 06）
            if path not in self._upgrade_verified:
                return {'ok': False, 'error': '这个文件没有通过校验，不能当作安装包运行'}
            if not os.path.exists(path):
                return {'ok': False, 'error': '安装包不见了，请重新下载'}
            os.startfile(path)  # noqa: 拉起 NSIS 安装器，装完用户自己重开
            self._upgrade_verified.discard(path)   # 一次性：运行过就作废
            return {'ok': True}
        except Exception as e:
            try:
                _log('upgrade_run 异常: %r' % e)
            except Exception:
                pass
            return {'ok': False, 'error': '启动安装器失败（%s）' % e.__class__.__name__}


def _webview2_installed():
    """检测系统是否已安装 WebView2 Runtime（注册表 + 目录兜底）。"""
    try:
        import winreg
        keys = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"),
        ]
        for hk, path in keys:
            try:
                with winreg.OpenKey(hk, path) as k:
                    if winreg.QueryValueEx(k, "pv")[0]:
                        return True
            except OSError:
                pass
    except Exception:
        pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"), os.environ.get("LOCALAPPDATA")):
        if base and os.path.isdir(os.path.join(base, "Microsoft", "EdgeWebView", "Application")):
            return True
    return False


def _msgbox(title, text):
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x40)  # MB_ICONINFORMATION | MB_OK
    except Exception:
        pass


def _ensure_webview2():
    """启动前确保 WebView2 可用；缺失则尽量自动安装，否则给明确指引（杜绝静默退化 IE11 白屏）。"""
    if _webview2_installed():
        return
    _log('未检测到 WebView2 Runtime，尝试安装')
    # 1) 优先用随包内置的引导安装器（联网下载运行时）
    setup = resource_path('MicrosoftEdgeWebview2Setup.exe')
    if os.path.exists(setup):
        try:
            _log('运行内置 WebView2 安装器: %s' % setup)
            rc = subprocess.run([setup, '/silent', '/install'], check=False, timeout=600).returncode
            _log('WebView2 安装器返回码: %d' % rc)
            if _webview2_installed():
                return
        except Exception as e:
            _log('内置安装器异常: %r' % (e,))
    # 2) 自动装失败/无内置器：弹窗指引手动装
    url = 'https://developer.microsoft.com/zh-cn/microsoft-edge/webview2/'
    _msgbox('方衡造价计算器',
            '本程序需要 Microsoft WebView2 运行库才能运行。\n\n'
            '将为你打开下载页面，请安装「WebView2 Runtime」后重新启动本程序。')
    try:
        webbrowser.open(url)
    except Exception:
        pass
    raise SystemExit('缺少 WebView2 Runtime，已引导安装，请重启动程序')


# 后台 HTTP 服务相关（2026-10-08 起，导入放进线程以重叠 WebView2 启动）
_HTTP_SERVERS = []


def _pick_free_port():
    """裸 socket 占一个 8787–8796 之间的空闲端口（不引 http.server 导入）。

    先占住端口、再把同一端口交给后台线程的 TCPServer，避免两次选端口不一致、
    也避免在主线程提前 import http.server（那 ~200ms 现在躲进后台线程）。"""
    import socket
    for p in range(8787, 8797):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', p))
            return p
        except OSError:
            pass
        finally:
            s.close()
    return 8787


def _serve(port, web_dir, ready):
    """后台线程：import http.server（~200ms）+ 起本地服务；监听后置 ready 事件。

    导入放进本函数体内，是为了让这 ~200ms 的 stdlib 导入与 WebView2 的
    ~1.1s 拉起在**不同线程**并行，冷启动省掉约 200ms（详见 main() 注释）。"""
    import http.server
    import socketserver

    class _LoggedHandler(http.server.SimpleHTTPRequestHandler):
        """访问日志路由到自建 _log。

        2026-09-08 真机事故根因：noconsole 打包后 sys.stderr=None，
        默认 log_message 写 stderr 抛 AttributeError → 每个请求都被掐死 →
        WebView2 ERR_EMPTY_RESPONSE → js_api 未注入 → 拖动/按钮全瘫。
        """

        def log_message(self, fmt, *args):
            _log('HTTP ' + (fmt % args))

        def end_headers(self):
            # 【必须】禁缓存。WebView2 的 HTTP 缓存按 URL 复用（端口固定 8787 +
            # 同一用户数据目录 = 同一缓存键）：改了内嵌 calculator.html 重新打包，
            # 启动时浏览器直接命中缓存、连请求都不发 → 界面/角标仍是旧版。
            # 2026-09-10 实锤：boot 日志里只有 favicon 请求、无 calculator.html 请求。
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            super().end_headers()

        def do_GET(self):
            # favicon.ico 直接返回空：省一次 404 往返，且避免日志里刷 404
            if self.path == '/favicon.ico':
                self.send_response(204)
                self.end_headers()
                return
            super().do_GET()

    handler = functools.partial(_LoggedHandler, directory=web_dir)

    class _Server(socketserver.TCPServer):
        allow_reuse_address = True

    try:
        httpd = _Server(('127.0.0.1', port), handler)
    except OSError as e:
        _log('HTTP 服务器绑定端口 %d 失败: %s' % (port, e))
        return
    _HTTP_SERVERS.append(httpd)
    _log('HTTP 服务器已起, port=%d' % port)
    ready.set()                       # 监听就绪，main 可放心起窗口
    httpd.serve_forever()


def _selftest_frozen():
    """冻结环境自检（FORHAN_SELFTEST=1 触发，只写日志不弹窗）。
       为什么要有：打包版和源码跑的是两套 import 环境 —— 2026-10-19 实测
       onedir 里 ctypes 多带了一份包，`from ctypes import wintypes` 直接 ImportError，
       源码跑得好好的，装完就「打不开文件选择框」。这条把它变成可复现的一步。"""
    ok = True
    try:
        from ctypes import wintypes  # noqa: F401
        _log('SELFTEST ctypes.wintypes: OK')
    except Exception as e:
        ok = False
        _log('SELFTEST ctypes.wintypes: FAIL %r' % e)
    try:
        import ctypes as _c
        buf = _c.create_unicode_buffer(1024)
        p = _c.cast(buf, _c.c_wchar_p)
        _log('SELFTEST 路径缓冲区 cast: OK addr_equal=%s' % (
            _c.addressof(buf) == _c.cast(p, _c.c_void_p).value))
    except Exception as e:
        ok = False
        _log('SELFTEST 路径缓冲区 cast: FAIL %r' % e)
    try:
        api = ShellAPI()
        _log('SELFTEST ShellAPI 实例化: OK pick_xlsx=%s import_calc_sheet=%s import_excel=%s upgrade=%s' % (
            hasattr(api, 'pick_xlsx'), hasattr(api, 'import_calc_sheet'), hasattr(api, 'import_excel'),
            hasattr(api, 'upgrade_download') and hasattr(api, 'upgrade_run')))
    except Exception as e:
        ok = False
        _log('SELFTEST ShellAPI: FAIL %r' % e)
    _log('SELFTEST 结论: %s' % ('PASS' if ok else 'FAIL'))
    return ok


def main():
    if os.environ.get('FORHAN_SELFTEST') == '1':
        _selftest_frozen()
        raise SystemExit(0)
    _ensure_webview2()
    # ★ 启动加速：固定 WebView2 用户数据目录
    # 默认 WebView2 每次用「临时目录」存 profile → 每次启动都重建 + 被杀软重新扫描
    # → 冷启动凭空多 ~700ms。固定到安装目录下复用，二次启动明显加快。
    # 注意：HTML 换版立即生效靠的是「URL 版本号 + 响应禁缓存」（见 _LoggedHandler），
    # 与此目录无关；localStorage 落固定目录反而更稳（用户设置不再被临时目录清理丢）。
    _udf = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), 'webview2_data')
    try:
        os.makedirs(_udf, exist_ok=True)
        # 可写探针：受保护目录下 makedirs 可能假成功，真正写文件才露馅。
        # 2026-10-22 用户把安装目录手动选到 D:\Program Files\ 下，普通权限写不进
        # → WebView2 起不来，弹「Microsoft Edge 无法读取和写入其数据目录」。
        _probe = os.path.join(_udf, '.write-probe.tmp')
        with open(_probe, 'w') as _pf:
            _pf.write('ok')
        os.remove(_probe)
    except Exception:
        # 兜底：安装目录不可写时退到用户目录，保证程序能起来。
        # 目录名与 installer.nsi 卸载保命用的「方衡造价计算器-计算数据」一致。
        _udf = os.path.join(
            os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'),
            '方衡造价计算器-计算数据', 'webview2_data')
        os.makedirs(_udf, exist_ok=True)
    os.environ['WEBVIEW2_USER_DATA_FOLDER'] = _udf
    _log('WebView2 用户数据目录固定: %s' % _udf)
    html_path = resource_path('calculator.html')
    if not os.path.exists(html_path):
        raise SystemExit(f'找不到计算器页面: {html_path}')

    _log('main 开始, html=%s' % html_path)
    # ★ 2026-10-19 构建标记：验证「跑的是哪版壳」只看日志，别猜（旧 exe / 新 exe 外观一样）
    _log('build: calc-login-excel-import 2026-10-19 (pick_xlsx v2 / import_calc_sheet / import_excel xlsm)')

    # 本地 HTTP 服务器加载页面，绕开 pywebview 3.6.3 的 file:// URL bug
    # （resolve_url 对 'file:///盘符:' 会把盘符当 netloc → dirname 得空 → 报错，
    #  而中文目录下的纯路径 WebView2 加载不可靠）。HTTP 的 origin 稳定，localStorage 持久化正常。
    #
    # ★ 2026-10-08 启动加速：http.server / socketserver 的导入（~200ms）从模块顶层
    #   挪进后台线程（_serve 内 import），与 WebView2 的 ~1.1s 拉起重叠，冷启动省约 200ms。
    #   端口用裸 socket 先占住（不引 http.server），再把同一端口交给后台线程起服务。
    web_dir = os.path.dirname(html_path)
    _port = _pick_free_port()
    _server_ready = threading.Event()
    threading.Thread(target=_serve, args=(_port, web_dir, _server_ready),
                    daemon=True, name='httpd').start()

    # 版本指纹（修改时间+体积）拼进 URL：与响应头禁缓存双保险，确保每次启动
    # 都真实回源，换掉内嵌页面后界面立即生效（详见 _serve 内 _LoggedHandler.end_headers）。
    try:
        _st = os.stat(html_path)
        _build_tag = '%x%x' % (int(_st.st_mtime), _st.st_size)
    except OSError:
        _build_tag = '0'

    # ★ 2026-10-19 糯米 R-02：只有**开发态**（源码直跑）才带 dev=1 允许探测本机 3001；
    #   打包后的生产包永远不带 → 前端永不自动切基址，防本机进程劫持登录凭据。
    _dev_q = '' if getattr(sys, 'frozen', False) else '&dev=1'
    file_url = 'http://127.0.0.1:%d/calculator.html?v=%s%s' % (_port, _build_tag, _dev_q)
    _log('HTTP 服务器起线程, port=%d, url=%s, html_path=%s' % (_port, file_url, html_path))

    # 起窗口前确保 HTTP 已就绪：后台线程正常早已监听，0.5s 只是兜底（异常慢才等得到）
    if not _server_ready.wait(0.5):
        _log('警告：HTTP 服务器 0.5s 内未就绪，页面首屏可能短暂空白')

    _t('create_window 开始'); _log('create_window 开始')
    api = ShellAPI()
    api._win = webview.create_window(
        TITLE,
        url=file_url,
        js_api=api,
        # 默认尺寸（2026-09-21 用户调）：750 × 360。
        # 宽度 750（比 900 窄，用户要更窄的窗口）；高度 360 即「简洁态」高度。
        # 计算稿态由页面自己再加高到 SHEET_WIN_H = 520（见 calculator.html），只加高不动宽；
        #   关掉（切回简洁）由 restore_height 回到这份默认 360。
        # min_size=(560, 300)：最小宽度 560 —— **放开横向拖窄**（之前锁 740 导致拖不动）；
        #   最小高度 300：表头三层（43+55+40=138）+ 一行高，再压计算稿首行会被粘性表头压住。
        width=750,
        height=360,
        min_size=(560, 300),
        frameless=True,
        easy_drag=False,
        on_top=True,
        text_select=True,
        background_color='#ffffff',
    )
    _log('create_window 完成')  # 勿在此取 native.Handle：start 前窗体未建，跨线程访问会抛异常打断启动

    # 【已弃用方案·勿重试】hidden=True + 延迟 show 消除白屏：
    # ① Window.show() 被 @_shown_call 装饰，会等 shown 事件 20s 超时（hidden 窗口永不触发）
    # ② 绕过装饰器调 gui.show() 也不行——hidden 窗口下 pywebview 主循环会提前结束、
    #   js_api 根本注入不进去，进程 3 秒后自己退出。3.6.3 无解。

    def _on_loaded():
        _log('页面 loaded 事件触发（NavigationCompleted）')

    api._win.events.loaded += _on_loaded
    _t('webview.start 调用'); _log('webview.start 调用')
    webview.start()
    _log('webview.start 结束')
    # HTTP 服务器在后台守护线程里，进程退出自动随父进程消亡；这里主动关掉释放端口
    for _s in _HTTP_SERVERS:
        try:
            _s.shutdown()
        except Exception:
            pass


if __name__ == '__main__':
    main()
