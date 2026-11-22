"""Cửa sổ không thanh tiêu đề của Windows (như app Claude / VS Code): thanh trên cùng của giao diện web làm thanh
tiêu đề, 3 nút ─ ☐ ✕ vẽ bằng HTML (base.html, gọi `window.pywebview.api.win(...)`).

Cách làm (pywebview `frameless=True` = WinForms FormBorderStyle.None):
- Kéo cửa sổ: CSS `app-region: drag` + WebView2 `IsNonClientRegionSupportEnabled` -> Windows tự xử lý kéo,
  nhấp đúp phóng to, kéo lên mép trên màn hình để phóng to (như thanh tiêu đề thật).
- Thêm lại kiểu WS_THICKFRAME | WS_CAPTION… nhưng trả WM_NCCALCSIZE = 0 (không chừa chỗ cho khung) -> không thấy
  khung, vẫn có Aero Snap, Win+mũi tên, hiệu ứng thu nhỏ, bấm nút taskbar để thu nhỏ; phóng to không đè taskbar.
- Đổi kích thước: WebView2 phủ kín cửa sổ nên mép cửa sổ không nhận chuột -> chừa viền `BORDER` px (Form.Padding,
  cùng màu nền trang) và trả mã HTLEFT / HTTOP… cho WM_NCHITTEST ở viền đó. Phóng to thì bỏ viền.
Chỉ chạy trên Windows; lỗi gì cũng chỉ ghi log (cửa sổ vẫn dùng được, chỉ thiếu phần đổi kích thước / snap).
"""

import ctypes
from ctypes import wintypes

BORDER = 5          # px thật (không theo DPI) viền đổi kích thước quanh nội dung

WM_NCCALCSIZE, WM_NCHITTEST, WM_NCACTIVATE, WM_NCDESTROY = 0x83, 0x84, 0x86, 0x82
GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME, WS_SYSMENU = 0x00C00000, 0x00040000, 0x00080000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX = 0x00020000, 0x00010000
SWP_FLAGS = 0x0020 | 0x0002 | 0x0001 | 0x0004 | 0x0010   # FRAMECHANGED | NOMOVE | NOSIZE | NOZORDER | NOACTIVATE
HTCLIENT = 1
HT = {(True, False, False, False): 10, (False, True, False, False): 11,      # trái, phải
      (False, False, True, False): 12, (False, False, False, True): 15,      # trên, dưới
      (True, False, True, False): 13, (False, True, True, False): 14,        # trên-trái, trên-phải
      (True, False, False, True): 16, (False, True, False, True): 17}        # dưới-trái, dưới-phải

LRESULT = ctypes.c_ssize_t
SUBCLASSPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
                                  ctypes.c_size_t, ctypes.c_size_t)


class NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


class MARGINS(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


_keep = []          # giữ callback sống (bị thu gom rác thì Windows gọi vào vùng nhớ đã giải phóng -> sập)


def _api():
    u, c = ctypes.windll.user32, ctypes.windll.comctl32
    c.SetWindowSubclass.argtypes = [wintypes.HWND, SUBCLASSPROC, ctypes.c_size_t, ctypes.c_size_t]
    c.DefSubclassProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    c.DefSubclassProc.restype = LRESULT
    u.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    u.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    u.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                               wintypes.UINT]
    u.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
    u.MonitorFromWindow.restype = wintypes.HANDLE
    u.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    u.IsZoomed.argtypes = [wintypes.HWND]
    u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    return u, c


def install(form) -> None:
    """Gọi trên luồng giao diện (form.Invoke) sau khi cửa sổ đã hiện."""
    from System.Windows.Forms import Padding
    u, c = _api()
    hwnd = int(form.Handle.ToInt64())

    def proc(h, msg, wp, lp, _id, _data):
        try:
            if msg == WM_NCCALCSIZE and wp:
                if u.IsZoomed(h):    # phóng to: Windows đặt cửa sổ lố ra ngoài màn hình 1 khung -> co về vùng làm việc
                    mi = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
                    if u.GetMonitorInfoW(u.MonitorFromWindow(h, 2), ctypes.byref(mi)):
                        NCCALCSIZE_PARAMS.from_address(lp).rgrc[0] = mi.rcWork
                return 0             # không chừa chỗ cho khung / thanh tiêu đề
            if msg == WM_NCACTIVATE:
                return c.DefSubclassProc(h, msg, wp, -1)     # -1: không vẽ lại khung khi đổi cửa sổ đang chọn
            if msg == WM_NCHITTEST and not u.IsZoomed(h):
                x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
                r = wintypes.RECT()
                u.GetWindowRect(h, ctypes.byref(r))
                b = BORDER + 2       # vùng bắt chuột rộng hơn viền 1 chút cho dễ trúng góc
                key = (x < r.left + b, x >= r.right - b, y < r.top + b, y >= r.bottom - b)
                return HT.get(key, HTCLIENT)
        except Exception as e:      # ngoại lệ trong callback Windows làm sập tiến trình -> nuốt, để Windows xử lý
            print("winchrome:", e, flush=True)
        return c.DefSubclassProc(h, msg, wp, lp)

    cb = SUBCLASSPROC(proc)
    _keep.append(cb)
    style = u.GetWindowLongPtrW(hwnd, GWL_STYLE)
    u.SetWindowLongPtrW(hwnd, GWL_STYLE, style | WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX)
    if not c.SetWindowSubclass(hwnd, cb, 0xD7, 0):
        raise OSError("SetWindowSubclass lỗi")
    # Windows 10 vẽ 1 đường viền trắng 1px quanh cửa sổ có khung (WS_THICKFRAME) -> lề khung DWM = 0 thì hết
    # (đo: lề 1 hoặc -1 vẫn còn viền trắng)
    ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(wintypes.HWND(hwnd), ctypes.byref(MARGINS(0, 0, 0, 0)))
    u.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_FLAGS)

    def pad(*_):
        want = 0 if u.IsZoomed(hwnd) else BORDER
        if form.Padding.All != want:
            form.Padding = Padding(want)
    form.Resize += pad
    pad()


def enable_drag_regions(form) -> bool:
    """Bật `app-region: drag` của WebView2. Đổi cài đặt này có hiệu lực từ lần tải trang kế -> trả True nếu cần tải lại."""
    cw = form.browser.webview.CoreWebView2
    if cw is None or cw.Settings.IsNonClientRegionSupportEnabled:
        return False
    cw.Settings.IsNonClientRegionSupportEnabled = True
    return True


def is_maximized(form) -> bool:
    return bool(ctypes.windll.user32.IsZoomed(wintypes.HWND(int(form.Handle.ToInt64()))))
