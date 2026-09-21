"""Windows: a real global hotkey, registered by eecc-redact itself.

RegisterHotKey delivers WM_HOTKEY to the thread that registered it, so the
listener runs a Win32 message loop on its own thread and reports a press
through a callback, on that thread. Wrap the callback with eecc_redact.ui.main_thread
before it touches Qt.
"""

import threading
from collections.abc import Callable

from eecc_redact import APP_NAME
from eecc_redact.errors import AppError
from eecc_redact.hotkeys import Hotkey

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000  # holding the keys down must not fire a stream of captures
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 0xB107


def _user32():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
    ]
    user32.GetMessageW.restype = ctypes.c_int  # -1 on error, so not BOOL
    user32.PostThreadMessageW.argtypes = [
        wintypes.DWORD,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    return user32


def explain(code: int, hotkey: Hotkey) -> str:
    if code == ERROR_HOTKEY_ALREADY_REGISTERED:
        return f"{hotkey.text} is already taken by another program."
    return f"Windows refused {hotkey.text} as a hotkey (error {code})."


class HotkeyListener:
    def __init__(self, hotkey: Hotkey, on_press: Callable[[], None]) -> None:
        self.hotkey = hotkey
        self._on_press = on_press
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._error = ""

    def start(self, timeout: float = 5.0) -> None:
        ready = threading.Event()
        self._error = ""
        self._thread = threading.Thread(
            target=self._run, args=(ready,), daemon=True, name=f"{APP_NAME}-hotkey"
        )
        self._thread.start()
        if not ready.wait(timeout):
            raise AppError("The hotkey listener did not start.")
        if self._error:
            raise AppError(self._error)

    def _run(self, ready: threading.Event) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = _user32()
        self._thread_id = ctypes.WinDLL("kernel32").GetCurrentThreadId()
        if not user32.RegisterHotKey(
            None, HOTKEY_ID, self.hotkey.win_modifiers | MOD_NOREPEAT, self.hotkey.win_vk
        ):
            self._error = explain(ctypes.get_last_error(), self.hotkey)
            ready.set()
            return
        ready.set()
        message = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    self._on_press()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)

    def stop(self, timeout: float = 2.0) -> None:
        """Stop and wait until the key is really released: the unregister runs on
        the listener thread, and registering the same key before it finishes
        would fail as "already taken"."""
        if self._thread_id:
            _user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout)
        self._thread, self._thread_id = None, 0
