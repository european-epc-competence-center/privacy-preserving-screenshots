"""The settings file. It never holds the API key; that lives in the OS keychain."""

import json
import tomllib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from platformdirs import user_config_path

from eecc_redact import APP_NAME

DEFAULT_BASE_URL = "https://api.shinrai.innovius.io"


def config_path() -> Path:
    return user_config_path(APP_NAME, appauthor=False) / "config.toml"


@dataclass
class Config:
    base_url: str = DEFAULT_BASE_URL
    project: str = APP_NAME
    location: str = "global"
    #: Which ShinrAI route detects: ``auto`` takes the PII API v2 (``POST /v2/detect``,
    #: every type the model offers, boxes in source pixels) whenever the deployment
    #: serves images on it, else the Google-compatible ``image:redact``; ``v2`` and
    #: ``google`` force one.
    api: str = "auto"
    #: Pixels added on every side of a box; OCR boxes sit tight on the glyphs.
    box_padding: int = 2
    #: The capture hotkey in eecc-redact's syntax (ctrl+shift+print). On Linux this is
    #: only what eecc-redact suggests; the desktop stores the user's actual choice.
    hotkey: str = ""
    #: Linux: the desktop keeps the real autostart entry and offers no way to
    #: read it back, so the user's choice is remembered here. Windows reads its
    #: registry instead.
    start_at_login: bool = False
    #: Set when setup was finished here. A key alone is not enough: one left by
    #: an earlier install would silently skip setup.
    setup_complete: bool = False

    @classmethod
    def load(cls) -> "Config":
        path = config_path()
        data = tomllib.loads(path.read_text("utf-8")) if path.is_file() else {}
        known = {field.name for field in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in known})

    def save(self) -> Path:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # json.dumps writes valid TOML for booleans, integers and basic strings.
        lines = [f"{key} = {json.dumps(value)}" for key, value in asdict(self).items()]
        path.write_text("\n".join(lines) + "\n", "utf-8")
        return path
