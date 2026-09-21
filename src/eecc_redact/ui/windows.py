"""Windows: Qt plumbing that needs Win32."""


def flush_clipboard(ole32=None) -> bool:
    """Hand the clipboard's contents to Windows so they outlive eecc-redact.

    Qt places clipboard data lazily with OleSetClipboard; OleFlushClipboard
    renders every format into the system clipboard. False if another
    application holds the clipboard, in which case the caller waits instead.
    """
    try:
        if ole32 is None:
            import ctypes

            ole32 = ctypes.windll.ole32
        return ole32.OleFlushClipboard() == 0  # S_OK
    except (AttributeError, OSError):
        return False
