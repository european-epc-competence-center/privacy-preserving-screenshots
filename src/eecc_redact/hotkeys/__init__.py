"""Global hotkeys: one syntax everywhere, translated for each platform.

`ctrl+shift+print`, `alt+f9` and `ctrl+alt+k` mean the same on every OS. A
parsed Hotkey carries what Windows needs (modifier flags and a virtual-key
code) and what the Linux shortcuts portal needs (a trigger like CTRL+SHIFT+Print).

eecc-redact registers a hotkey rather than watching the keyboard: a key listener needs
Input Monitoring on macOS and looks like a keylogger to antivirus tools.

* windows - eecc-redact registers the key itself, from the tray
* linux   - the desktop binds it through the GlobalShortcuts portal
"""

from dataclasses import dataclass

DEFAULT_HOTKEY = "ctrl+shift+print"

# name -> (Win32 MOD_* flag, portal name, display name)
_MODIFIERS = {
    "ctrl": (0x0002, "CTRL", "Ctrl"),
    "control": (0x0002, "CTRL", "Ctrl"),
    "alt": (0x0001, "ALT", "Alt"),
    "shift": (0x0004, "SHIFT", "Shift"),
    "win": (0x0008, "LOGO", "Win"),
    "super": (0x0008, "LOGO", "Win"),
    "meta": (0x0008, "LOGO", "Win"),  # Qt's name for the Windows key
}
_ORDER = ("Ctrl", "Alt", "Shift", "Win")

# name -> (virtual-key code, XKB keysym, display name, allowed without a modifier)
_KEYS = {
    "print": (0x2C, "Print", "Print", True),
    "printscreen": (0x2C, "Print", "Print", True),
    "prtsc": (0x2C, "Print", "Print", True),
    "pause": (0x13, "Pause", "Pause", True),
    "space": (0x20, "space", "Space", False),
    "enter": (0x0D, "Return", "Enter", False),
    "return": (0x0D, "Return", "Enter", False),
    "tab": (0x09, "Tab", "Tab", False),
    "insert": (0x2D, "Insert", "Insert", False),
    "ins": (0x2D, "Insert", "Insert", False),
    "delete": (0x2E, "Delete", "Delete", False),
    "del": (0x2E, "Delete", "Delete", False),
    "home": (0x24, "Home", "Home", False),
    "end": (0x23, "End", "End", False),
    "pageup": (0x21, "Page_Up", "PageUp", False),
    "pgup": (0x21, "Page_Up", "PageUp", False),
    "pagedown": (0x22, "Page_Down", "PageDown", False),
    "pgdown": (0x22, "Page_Down", "PageDown", False),
}


@dataclass(frozen=True)
class Hotkey:
    modifiers: tuple[str, ...]
    key: str
    win_modifiers: int
    win_vk: int
    portal: str

    @property
    def text(self) -> str:
        """For people: Ctrl+Shift+Print."""
        return "+".join((*self.modifiers, self.key))

    @property
    def qt_text(self) -> str:
        """For QKeySequence, which calls the Windows key Meta."""
        return "+".join("Meta" if part == "Win" else part for part in (*self.modifiers, self.key))

    @property
    def config_value(self) -> str:
        return self.text.lower()


def _resolve(name: str) -> tuple[int, str, str, bool]:
    if name in _KEYS:
        return _KEYS[name]
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        number = int(name[1:])
        return 0x70 + number - 1, f"F{number}", f"F{number}", True
    if len(name) == 1 and name.isascii() and name.isalnum():
        return ord(name.upper()), name.lower(), name.upper(), False
    raise ValueError(
        f"Unknown key '{name}'. Use a letter, a digit, F1-F24, or one of: print, "
        "pause, space, enter, tab, insert, delete, home, end, pageup, pagedown."
    )


def parse_hotkey(text: str) -> Hotkey:
    parts = [part.strip().lower() for part in text.split("+") if part.strip()]
    if not parts:
        raise ValueError("No hotkey given. Write it like ctrl+shift+print.")
    modifiers = {_MODIFIERS[p][2]: _MODIFIERS[p] for p in parts if p in _MODIFIERS}
    keys = [p for p in parts if p not in _MODIFIERS]
    if len(keys) != 1:
        raise ValueError(
            f"'{text}' needs exactly one key besides ctrl, alt, shift or win, "
            "for example ctrl+shift+print."
        )
    vk, keysym, display, alone_ok = _resolve(keys[0])
    if not modifiers and not alone_ok:
        raise ValueError(
            f"'{display}' on its own would fire every time you type it. Add ctrl, "
            f"alt, shift or win, for example ctrl+shift+{keys[0]}."
        )
    ordered = tuple(name for name in _ORDER if name in modifiers)
    return Hotkey(
        modifiers=ordered,
        key=display,
        win_modifiers=sum(modifiers[name][0] for name in ordered),
        win_vk=vk,
        portal="+".join((*(modifiers[name][1] for name in ordered), keysym)),
    )
