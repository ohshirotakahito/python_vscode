"""Apply the Analyzer icon to the native Windows taskbar window after Qt shows it."""
import ctypes
import os
from ctypes import wintypes


def apply_taskbar_icon(window, path):
    """Keep native icon handles alive for the lifetime of the Qt window."""
    if os.name != 'nt':
        return
    user32 = ctypes.WinDLL('user32', use_last_error=True)
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
                                  ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, ctypes.c_size_t, ctypes.c_ssize_t]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    handles = []
    hwnd = int(window.winId())
    # WM_SETICON: ICON_SMALL is used in the caption; ICON_BIG in the taskbar/Alt-Tab.
    for kind, width_metric, height_metric in [(0, 49, 50), (1, 11, 12)]:
        handle = user32.LoadImageW(None, str(path), 1, user32.GetSystemMetrics(width_metric),
                                    user32.GetSystemMetrics(height_metric), 0x10)
        if handle:
            user32.SendMessageW(hwnd, 0x80, kind, handle)
            handles.append(handle)
    if handles:
        # LoadImageW creates owned HICONs; Qt does not own the handles sent via WM_SETICON.
        def release_icons(*_args):
            for handle in handles:
                user32.DestroyIcon(handle)
        window.destroyed.connect(release_icons)
