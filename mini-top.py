# -*- coding: utf-8 -*-
"""待办悬浮窗 —— 把「开发台板」的迷你模式开成一个始终置顶的小窗。

用法：双击桌面上的「待办悬浮窗」。
  · 二选一：开这个小窗时，主面板会被关掉；反过来主面板一开，小窗自己让位。
  · 没在用（别的程序在前台、鼠标也不在窗上）时整窗半透明、并切成深色底亮字，
    能看见背后的代码，也能一眼看清待办。
  · 鼠标移上去或点到它，立刻恢复不透明；移开约半秒后重新淡下去。
  · 贴边收起：点小窗右上角的 ⇤，窗口会吸到屏幕边缘（默认右边），
    鼠标一移开就滑出屏幕、只留一条窄边；鼠标划到那条边上，它又滑回来。
    再点一下 ⇤ 关掉这个行为，窗口留在边上、照常整窗显示。
  · 已经在开着的，再双击不会开第二个，只会把它拎到最前。
  · 关掉小窗后守护会留在后台等一会儿（主面板还开着就一直等）：从主面板点「悬浮窗 ↗」
    回来时，它会把这个浏览器弹窗换成真正的桌面小窗，置顶和贴边继续生效。
    两个窗口都关掉、且过了宽限期，守护才自己退出，不留残余。
  · 数据还是同一份本机存储，和主界面双向同步。
"""
import ctypes
import hashlib
import os
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
# 下面几个允许用环境变量覆盖：一是方便换机器/换目录，二是自动化测试能用独立 profile 跑真脚本
PAGE = os.environ.get("DEVBOARD_PAGE", "file:///E:/code/me/todo/index.html#mini").strip()
TITLE = os.environ.get("DEVBOARD_TITLE", "待办 · 悬浮窗").strip()
MAIN_TITLE = os.environ.get("DEVBOARD_MAIN_TITLE", "开发台板").strip()  # 主面板是 --app 窗口，标题恰好等于文档标题
PROFILE = os.environ.get("DEVBOARD_PROFILE", "").strip()                # 空 = 用系统默认配置

WIN_W, WIN_H = 360, 360          # 近似正方形：宽度够放下一行待办，高度只留一屏能扫完的量
LEGACY_SIZES = {(360, 640)}      # 旧版默认尺寸；没改过尺寸的老窗口启动时自动迁到新默认
MARGIN_X, MARGIN_Y = 16, 40      # 贴屏幕右侧

ALPHA_ON = 255                   # 在用：完全不透明
ALPHA_AWAY = int(os.environ.get("DEVBOARD_ALPHA_AWAY", "190"))  # 没在用：约 75%，深底亮字，桌面仍透得出来
ALPHA_STEP = int(os.environ.get("DEVBOARD_ALPHA_STEP", "26"))   # 每次渐变步长，约 3 帧（0.45 秒）走完

# ---- 贴边收起 ----
# 页面管不了窗口位置，所以用标题尾巴当信号：标题以 ⇥ 结尾 = 用户点了「贴边收起」
EDGE_MARK = os.environ.get("DEVBOARD_EDGE_MARK", "⇥").strip() or "⇥"
EDGE_ENABLED = os.environ.get("DEVBOARD_EDGE_HIDE", "1") != "0"   # 总开关（环境变量可整体禁用）
EDGE_PEEK = int(os.environ.get("DEVBOARD_EDGE_PEEK", "6"))        # 收起来后露在屏幕上的窄边宽度
EDGE_SNAP = int(os.environ.get("DEVBOARD_EDGE_SNAP", "32"))       # 离边多近算「贴边」，会顺手吸过去
EDGE_DELAY = float(os.environ.get("DEVBOARD_EDGE_DELAY", "0.45"))  # 鼠标离开多久后收起
EDGE_SLIDE_MS = int(os.environ.get("DEVBOARD_EDGE_SLIDE", "130"))  # 滑出/滑回时长

# ---- 守护的生命周期 ----
# 小窗关掉后不马上退出：用户可能只是回主面板看一眼，再把小窗叫回来。
# 主面板还开着就一直留着；两个窗口都没了，再等 IDLE_GRACE 秒才走。
IDLE_GRACE = float(os.environ.get("DEVBOARD_IDLE_GRACE", "120"))
EVENT_MODIFY_STATE = 0x0002
ERROR_ALREADY_EXISTS = 183

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x0002
WM_CLOSE = 0x0010
GA_ROOT = 2

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.GetWindowLongPtrW.restype = ctypes.c_void_p
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SetWindowLongPtrW.restype = ctypes.c_void_p
user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetAncestor.restype = wintypes.HWND
user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.DWORD,
                                              ctypes.c_ubyte, wintypes.DWORD]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM]
user32.PostMessageW.restype = wintypes.BOOL
# 贴边要看「工作区」（去掉任务栏的那块），而且窗口在哪块屏就算哪块屏，不能死认主屏
user32.MonitorFromWindow.restype = wintypes.HMONITOR
user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.OpenEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL

EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

MONITOR_DEFAULTTONEAREST = 2


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def hval(v):
    """HWND 在不同签名下可能是 int 或 None，统一成 int 好比较。"""
    return int(v) if v else 0


def setup_log():
    """桌面快捷方式用的是 pythonw，没有控制台（sys.stdout 是 None），print 全被丢掉，
    出错也无声无息。这种情况下把输出落到一个日志文件里；手动跑/自动化测试有控制台，
    就照常打到终端，不改变原有行为。DEVBOARD_LOG 可以强制指定日志路径。"""
    forced = os.environ.get("DEVBOARD_LOG", "").strip()
    if not forced and sys.stdout is not None and sys.stderr is not None:
        return
    try:
        path = forced or os.path.join(tempfile.gettempdir(), "devboard-mini.log")
        try:
            if os.path.getsize(path) > 200 * 1024:
                os.replace(path, path + ".1")
        except OSError:
            pass
        f = open(path, "a", encoding="utf-8", buffering=1)
        f.write("\n==== %s ====\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        sys.stdout = f
        sys.stderr = f
    except Exception:
        pass


def dpi_aware():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except Exception:
        try:
            ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
        except Exception:
            pass


def visible_windows():
    """可见的顶层窗口（hwnd, 标题）。"""
    out = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        t = buf.value.strip()
        if t:
            out.append((hval(hwnd), t))
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


CHROME_SUFFIX = " - Google Chrome"   # 浏览器标签/弹窗比页面标题多出来的一截

# ---- 单实例（同一套页面+profile 只留一个守护）----
_mutex = None
_wake = None


def instance_names():
    """按「profile + 页面地址」区分实例：正常用是一份，自动化测试各自独立，互不抢占。"""
    key = ("%s|%s|%s" % (PROFILE or "default", PAGE, TITLE)).encode("utf-8")
    tag = hashlib.md5(key).hexdigest()[:12]
    return ("Local\\DevBoardMiniTop_" + tag, "Local\\DevBoardMiniTopWake_" + tag)


def claim_single_instance():
    """拿单实例锁。拿不到说明已经有一个守护在跑：只给它发个唤醒信号，自己退出。

    这样「已经在开着的，再双击不会开第二个，只会把它拎到最前」依然成立，
    也不会出现两个守护抢同一个窗口（位置、透明度互相打架）。
    """
    global _mutex, _wake
    mutex_name, wake_name = instance_names()
    _mutex = kernel32.CreateMutexW(None, False, mutex_name)
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        ev = kernel32.OpenEventW(EVENT_MODIFY_STATE, False, wake_name)
        if ev:
            kernel32.SetEvent(ev)
            kernel32.CloseHandle(ev)
        return False
    _wake = kernel32.CreateEventW(None, False, False, wake_name)   # 自动复位事件
    return True


def take_wake():
    """有没有人（又双击了一次快捷方式）在叫我们。取一次就清掉。"""
    if not _wake:
        return False
    return kernel32.WaitForSingleObject(_wake, 0) == 0



def find_mini():
    """真正的桌面悬浮窗优先于浏览器弹窗版。
    桌面版（--app）标题就是页面标题（可能带 ⇥ 后缀）；浏览器弹窗会多出 ' - Google Chrome'。
    两者同名同源，必须先认桌面版，否则会把弹窗当成自己的窗口去置顶、去淡入淡出。"""
    exact, loose = [], []
    for h, t in visible_windows():
        if not t.startswith(TITLE):
            continue
        if t.endswith(CHROME_SUFFIX):
            loose.append(h)
        else:
            exact.append(h)
    return exact + loose


def find_main():
    """主面板：标题恰好等于「开发台板」。
    普通浏览器标签的标题会多出 ' - Google Chrome'，所以不会误伤。
    最小化也算占用——二选一就是二选一，不留一个藏在任务栏里的副本。"""
    return [h for h, t in visible_windows() if t == MAIN_TITLE]


def main_exists():
    """「开发台板」还在用吗——只用来决定守护要不要继续等，绝不据此关窗口。

    从悬浮窗点「台板 ↗」打开的是浏览器窗口，标题是「开发台板 - Google Chrome」，
    比桌面版多一截；所以这里两种都算。"""
    for _, t in visible_windows():
        if t == MAIN_TITLE or t == MAIN_TITLE + CHROME_SUFFIX:
            return True
    return False


def rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def workarea(hwnd):
    """窗口所在那块屏的工作区（去掉任务栏）。贴边要贴的是它，不是整块屏幕。"""
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) if hwnd else None
    if mon and user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        wr = mi.rcWork
        return wr.left, wr.top, wr.right, wr.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def on_screen(hwnd):
    """至少要有 80×80 落在屏幕里，否则算跑丢了。
    贴边收起时窗口大半在屏外，也会落到「跑丢了」——启动时正好把它请回来。"""
    x, y, w, h = rect(hwnd)
    l, t, r, b = workarea(hwnd)
    return (x + w > l + 60) and (y + h > t + 40) and (x < r - 60) and (y < b - 40)


def place(hwnd, resize):
    l, t, r, b = workarea(hwnd)
    x = max(l, r - WIN_W - MARGIN_X)
    y = t + MARGIN_Y
    if resize:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, WIN_W, WIN_H,
                            SWP_SHOWWINDOW | SWP_NOACTIVATE)
        # Chrome 有最小窗口尺寸，被撑大了就按实际宽度重新贴右边
        _, _, aw, _ = rect(hwnd)
        if aw > WIN_W:
            user32.SetWindowPos(hwnd, HWND_TOPMOST, max(l, r - aw - MARGIN_X), y,
                                0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
    else:
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                            SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE)


def place_home(hwnd):
    """只把窗口挪回默认位置，不动用户自己调过的尺寸。
    贴边收起会把窗口推到屏幕外，下次启动靠它回家，但不能顺手把尺寸改回 360×360。"""
    l, t, r, b = workarea(hwnd)
    _, _, w, _ = rect(hwnd)
    user32.SetWindowPos(hwnd, HWND_TOPMOST, max(l, r - w - MARGIN_X), t + MARGIN_Y, 0, 0,
                        SWP_NOSIZE | SWP_SHOWWINDOW | SWP_NOACTIVATE)


def keep_topmost(hwnd):
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)


def is_topmost(hwnd):
    return bool(hval(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)) & WS_EX_TOPMOST)


def is_layered(hwnd):
    return bool(hval(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)) & WS_EX_LAYERED)


def ensure_layered(hwnd):
    """加分层位，否则 SetLayeredWindowAttributes 不生效。"""
    ex = hval(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))
    if not (ex & WS_EX_LAYERED):
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ctypes.c_void_p(ex | WS_EX_LAYERED))


def set_alpha(hwnd, a):
    return bool(user32.SetLayeredWindowAttributes(hwnd, 0, max(0, min(255, a)), LWA_ALPHA))


def focused(hwnd):
    fg = hval(user32.GetForegroundWindow())
    if not fg:
        return False
    root = hval(user32.GetAncestor(fg, GA_ROOT)) or fg
    return root == hval(hwnd)


def cursor_inside(hwnd):
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False
    x, y, w, h = rect(hwnd)
    return x <= pt.x < x + w and y <= pt.y < y + h


# ---------------- 贴边收起 ----------------
# 思路：整窗没法缩到 6 像素（Chrome 有最小尺寸），所以反过来——把窗口推出屏幕，
# 只留 EDGE_PEEK 那么宽的一条在外面当把手。鼠标压上把手就滑回来，移开再滑出去。

def mouse_down():
    """左键是否按着。拖动窗口标题栏时按着，这时候不能抢位置、也不能收起。"""
    return bool(user32.GetAsyncKeyState(0x01) & 0x8000)


def nearest_side(hwnd, snap=None):
    """窗口贴着哪条边：L/R/T/B；离得都不够近就 None。"""
    if snap is None:
        snap = EDGE_SNAP
    x, y, w, h = rect(hwnd)
    l, t, r, b = workarea(hwnd)
    dist = {"L": x - l, "R": r - (x + w), "T": y - t, "B": b - (y + h)}
    side = min(dist, key=lambda k: abs(dist[k]))
    return side if abs(dist[side]) <= snap else None


def docked_xy(hwnd, side):
    """贴边展开时该待的坐标：齐着那条边推过去。"""
    x, y, w, h = rect(hwnd)
    l, t, r, b = workarea(hwnd)
    if side == "L":
        x = l
    elif side == "R":
        x = r - w
    elif side == "T":
        y = t
    elif side == "B":
        y = b - h
    return x, y


def hidden_xy(hwnd, side, dx, dy):
    """收起来：整窗推出屏幕，只把 EDGE_PEEK 宽的一条留在屏幕上。"""
    _, _, w, h = rect(hwnd)
    l, t, r, b = workarea(hwnd)
    if side == "L":
        return l - w + EDGE_PEEK, dy
    if side == "R":
        return r - EDGE_PEEK, dy
    if side == "T":
        return dx, t - h + EDGE_PEEK
    if side == "B":
        return dx, b - EDGE_PEEK
    return dx, dy


def cursor_on_peek(hwnd, side, dx, dy, w, h, slack=2):
    """鼠标压到把手上了吗。纵向/横向也要对得上，
    否则鼠标擦过屏幕右边缘任意高度都会把窗口勾出来。"""
    l, t, r, b = workarea(hwnd)
    pt = POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False
    if side == "R":
        return pt.x >= r - EDGE_PEEK - slack and dy <= pt.y < dy + h
    if side == "L":
        return pt.x <= l + EDGE_PEEK + slack and dy <= pt.y < dy + h
    if side == "T":
        return pt.y <= t + EDGE_PEEK + slack and dx <= pt.x < dx + w
    if side == "B":
        return pt.y >= b - EDGE_PEEK - slack and dx <= pt.x < dx + w
    return False


def slide(hwnd, x0, y0, x1, y1, ms=None):
    """一步蹦过去太生硬，分几帧走完（先快后慢），一百多毫秒。"""
    if ms is None:
        ms = EDGE_SLIDE_MS
    frames = max(1, int(round(ms / 16.0)))
    for k in range(1, frames + 1):
        e = 1 - (1 - k / float(frames)) ** 3
        user32.SetWindowPos(hwnd, HWND_TOPMOST,
                            int(round(x0 + (x1 - x0) * e)),
                            int(round(y0 + (y1 - y0) * e)),
                            0, 0, SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW)
        if k < frames:
            time.sleep(ms / 1000.0 / frames)


def launch():
    args = [CHROME, "--app=" + PAGE, "--window-size=%d,%d" % (WIN_W, WIN_H)]
    if PROFILE:
        args.insert(1, "--user-data-dir=" + PROFILE)
    subprocess.Popen(
        args,
        cwd=os.path.dirname(CHROME),
        creationflags=0x08000000,   # CREATE_NO_WINDOW，不闪黑框
        close_fds=True,
    )


def title_of(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def is_app_window(hwnd):
    """桌面版（--app）的标题就是页面标题本身；浏览器弹窗/标签会带 ' - Google Chrome'。"""
    t = title_of(hwnd)
    return t.startswith(TITLE) and not t.endswith(CHROME_SUFFIX)


def wants_edge(hwnd):
    """页面点「贴边收起」后会在标题尾巴上加 ⇥ —— 这是页面唯一能传给脚本的信号。"""
    if not EDGE_ENABLED:
        return False
    t = title_of(hwnd)
    if t.endswith(CHROME_SUFFIX):
        return False          # 浏览器弹窗版不受脚本管，别跟着起哄
    return t.endswith(EDGE_MARK)


def close_windows(handles, wait=2.0):
    for h in handles:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    t0 = time.time()
    while time.time() - t0 < wait:
        if not [h for h in handles if user32.IsWindow(h)]:
            return True
        time.sleep(0.1)
    return False


def close_main_panel():
    """二选一：开悬浮窗之前，把主面板请走。只认标题完全一致的 app 窗口，不碰浏览器标签。"""
    mains = find_main()
    if not mains:
        return 0
    close_windows(mains, wait=3.0)
    # 主面板刚被收走就立刻开小窗，Chrome 会把新窗口建在一个「置顶设不上去」的过渡状态里
    # （SetWindowPos 返回成功，WS_EX_TOPMOST 却始终不生效，窗口就不再浮在最前面）。
    # 等 Chrome 把主面板那套收尾做完再开，就正常了
    time.sleep(0.8)
    return len(mains) if not find_main() else len(mains)


def adopt_window(hwnd, reused):
    """把窗口摆好、套上分层位。reused=True 时尽量不动用户自己调过的位置与尺寸。"""
    _, _, cw, ch = rect(hwnd)
    if not reused:
        place(hwnd, resize=True)
    elif (cw, ch) in LEGACY_SIZES:
        # 还是旧版的 360×640：换成新默认尺寸。用户拖过的尺寸不在表里，不会被重置
        place(hwnd, resize=True)
    elif not on_screen(hwnd):
        # 跑丢了（常见于上次是贴边收起、位置被 Chrome 记在屏幕外）：请回家，但保留尺寸
        place_home(hwnd)
    else:
        place(hwnd, resize=False)
    ensure_layered(hwnd)


def launch_and_wait(timeout=20.0):
    """开一个真正的桌面小窗（--app），等它出现。Chrome 冷启动可能慢，最多等 20 秒。"""
    launch()
    t0 = time.time()
    while time.time() - t0 < timeout:
        time.sleep(0.2)
        hits = [h for h in find_mini() if is_app_window(h)]
        if hits:
            return hits[0]
    return None


def acquire_window(first):
    """找一个能守护的窗口。返回 (hwnd, reused)；没有可用的就 (None, False)。

    除了第一次启动，还有两种「小窗又回来了」要接住：
      · 主面板点「悬浮窗 ↗」→ 页面 window.open 出来的是浏览器弹窗版（不能置顶、不能贴边），
        这里把它换成真正的桌面小窗；
      · 又双击了一次桌面快捷方式 → 第二个进程只发来一个唤醒信号（见 claim_single_instance）。
    """
    woke = take_wake()
    apps = [h for h in find_mini() if is_app_window(h)]
    if apps:
        if woke:
            print("wake -> raise existing")
        return apps[0], True

    pops = [h for h in find_mini() if not is_app_window(h)]
    if not (pops or first or woke):
        return None, False

    if pops:
        print("browser-popup-found=%d -> replace" % len(pops))
    if first or woke:
        # 双开快捷方式/从主面板回来：按二选一，先把主面板请走
        close_main_panel()
    hwnd = launch_and_wait()
    if hwnd:
        # 新的桌面小窗起来了再收走弹窗版：先立后破，中间不空
        close_windows(pops)
        return hwnd, False
    if pops:
        # 兜底：--app 被 Chrome 交给已在运行的浏览器时，开出来还是弹窗。
        # 宁可退一步接管弹窗，也不能让用户点了什么都没有
        print("fallback-popup hwnd=%s" % pops[0])
        return pops[0], False
    return None, False


def guard(hwnd, loose, reused):
    """守住一个窗口，直到它消失（返回 "closed"）或主面板接管（返回 "step-aside"）。"""
    cur = ALPHA_ON if focused(hwnd) else ALPHA_AWAY
    set_alpha(hwnd, cur)
    print("alpha=%d" % cur)

    # 守护：置顶掉了补回来；分层位被 Chrome 重置了补回来；按「有没有在用」淡入淡出；
    # 开了贴边收起的，顺带管「吸到边上 / 鼠标移开滑出去 / 压到把手滑回来」
    # Chrome 在窗口可见后会再套一次自己记的位置/尺寸，所以开头两秒内重新摆几次，之后不再抢用户拖动
    settle_until = time.time() + (1.0 if reused else 2.2)
    edge_on = wants_edge(hwnd)
    side = None          # 贴在哪条边：'R' / 'L' / 'T' / 'B'
    docked = None        # 贴边展开时该待的坐标
    collapsed = False    # 现在是不是藏起来了
    idle_since = time.time()   # 鼠标最后一次在窗上/窗激活的时刻，用来算收起延迟
    i = 0
    while True:
        time.sleep(0.15)
        i += 1
        if not user32.IsWindow(hwnd):
            # Chrome 有时会把 app 窗口销毁后重建（新窗口可能复用到同一个句柄号），
            # 这里重新认领一次；真的关掉了才算关掉，否则会出现「窗口还在、守护却退出」
            again = find_mini() if loose else [h for h in find_mini() if is_app_window(h)]
            if not again:
                print("window-closed")
                return "closed"
            hwnd = again[0]
            place(hwnd, resize=True)
            ensure_layered(hwnd)
            cur = ALPHA_ON if focused(hwnd) else ALPHA_AWAY
            set_alpha(hwnd, cur)
            settle_until = time.time() + 1.2
            side, docked, collapsed = None, None, False
            print("re-adopted hwnd=%s rect=%s" % (hwnd, rect(hwnd)))
            continue

        # 开关写在页面标题上，隔一会儿对一遍（点一下按钮半秒内生效）
        if i % 3 == 0:
            want = wants_edge(hwnd)
            if want != edge_on:
                edge_on = want
                print("edge-hide=%s" % ("on" if want else "off"))
                if not want:
                    if collapsed and docked:
                        # 关掉了，还藏着就先请回来
                        hx, hy, _, _ = rect(hwnd)
                        slide(hwnd, hx, hy, docked[0], docked[1])
                        collapsed = False
                        cur = ALPHA_ON
                        set_alpha(hwnd, cur)
                    side, docked = None, None

        if edge_on:
            now = time.time()
            _, _, w, h = rect(hwnd)
            if collapsed:
                # 藏起来时只认「鼠标压到把手」和「被叫到前台」，别的一概不动它
                if side and docked and (focused(hwnd) or
                                        cursor_on_peek(hwnd, side, docked[0], docked[1], w, h)):
                    hx, hy, _, _ = rect(hwnd)
                    slide(hwnd, hx, hy, docked[0], docked[1])
                    collapsed = False
                    idle_since = now
                    cur = ALPHA_ON      # 滑出来的同时就亮起来，别让人看半透明的
                    set_alpha(hwnd, cur)
            else:
                # 用户可能又把窗口拖离了边缘，那就取消贴边
                if side and nearest_side(hwnd, EDGE_SNAP + 12) != side:
                    side, docked = None, None
                if side is None:
                    s = nearest_side(hwnd)
                    if s:
                        side = s
                        docked = docked_xy(hwnd, s)
                x, y, _, _ = rect(hwnd)
                if side and docked and (x, y) != (docked[0], docked[1]) and not mouse_down():
                    # 差几个像素没贴严实，顺手吸过去；正在拖动时绝不动手，免得从手里抢走窗口
                    slide(hwnd, x, y, docked[0], docked[1])
                elif cursor_inside(hwnd) or focused(hwnd):
                    idle_since = now
                elif side and docked and not mouse_down() and now - idle_since >= EDGE_DELAY:
                    hx, hy = hidden_xy(hwnd, side, docked[0], docked[1])
                    slide(hwnd, x, y, hx, hy)
                    collapsed = True
                    print("edge-collapse side=%s" % side)

        if time.time() < settle_until and i % 4 == 0 and not edge_on and not collapsed:
            place(hwnd, resize=True)

        # 主面板一出现，小窗让位（二选一）
        if i % 3 == 0 and find_main():
            print("main-panel-opened -> step-aside")
            if collapsed and docked:
                # 别把屏幕外的坐标留给 Chrome 记住，否则下次启动窗口会开在看不见的地方
                user32.SetWindowPos(hwnd, HWND_TOPMOST, docked[0], docked[1], 0, 0,
                                    SWP_NOSIZE | SWP_NOACTIVATE)
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            for _ in range(25):
                if not user32.IsWindow(hwnd):
                    break
                time.sleep(0.1)
            return "step-aside"

        # 又双击了一次桌面快捷方式：把它拎到最前，而不是再开一个
        if i % 3 == 0 and take_wake():
            print("wake -> raise")
            keep_topmost(hwnd)
            user32.SetForegroundWindow(hwnd)

        if i % 5 == 0:
            if not is_topmost(hwnd):
                keep_topmost(hwnd)
            if not is_layered(hwnd):
                ensure_layered(hwnd)
                set_alpha(hwnd, cur)

        if collapsed:
            continue          # 藏在屏幕外，淡不淡的没人看得见

        target = ALPHA_ON if (focused(hwnd) or cursor_inside(hwnd)) else ALPHA_AWAY
        if cur != target:
            if abs(target - cur) <= ALPHA_STEP:
                cur = target
            else:
                cur += ALPHA_STEP if target > cur else -ALPHA_STEP
            set_alpha(hwnd, cur)


def main():
    setup_log()
    dpi_aware()

    try:
        if not claim_single_instance():
            print("already-running -> wake-signal")
            return 0

        closed = close_main_panel()
        if closed:
            print("main-panel-closed=%d" % closed)

        hwnd, reused = None, False
        first = True
        idle_since = 0.0
        errors = 0

        # 外层是「找一个窗口 → 守到它消失 → 再找」，而不是「守到它消失就退出」。
        # 小窗让位给主面板后守护必须留着：用户从主面板点「悬浮窗 ↗」回来时，
        # 页面 window.open 出来的是浏览器弹窗版，只有后台还有人在，才能把它换成
        # 真正的桌面小窗，并继续管置顶和贴边。以前这里直接 return，回来就没人管了。
        while True:
            if not hwnd or not user32.IsWindow(hwnd):
                hwnd, reused = acquire_window(first)
                if not hwnd:
                    if first:
                        print("window-not-found")
                        return 2
                    # 没窗口可守：主面板还开着就继续等；两个窗口都没了才按宽限期退出
                    if main_exists() or time.time() - idle_since < IDLE_GRACE:
                        time.sleep(0.35)
                        continue
                    print("idle-exit")
                    return 0
                first = False
                adopt_window(hwnd, reused)
                if reused:
                    user32.SetForegroundWindow(hwnd)
                    print("already-open hwnd=%s" % hwnd)
                else:
                    print("opened hwnd=%s rect=%s topmost=%s"
                          % (hwnd, rect(hwnd), is_topmost(hwnd)))

            try:
                reason = guard(hwnd, not is_app_window(hwnd), reused)
                errors = 0
            except Exception:
                # 以前守护循环里一抛异常，进程就静默死了：窗口还在，却没人再管它，
                # 表现就是「贴边/透明度突然不生效」。现在把栈打出来，并重新接管窗口
                traceback.print_exc()
                errors += 1
                reason = "error"
                time.sleep(0.4)
                if errors >= 20:
                    print("too-many-errors")
                    return 1
            print("guard-exit=%s" % reason)
            hwnd = None
            idle_since = time.time()
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
