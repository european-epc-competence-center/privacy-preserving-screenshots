"""Where the ShinrAI key lives.

The key is the user's, not the project's. It goes in the OS keychain, or in the
environment for development. It is never written to the config file or a log,
never accepted on the command line, and scrubbed from anything shown.
"""

import os
import re

from eecc_redact import APP_NAME

ENV_VAR = "SHINRAI_API_KEY"
_ACCOUNT = "shinrai-api-key"
_KEY = re.compile(r"shr_(live|test)_[A-Za-z0-9_\-]+")


def key_source() -> tuple[str, str]:
    """The key and where it came from: "environment", "keychain", or "" for none.

    Only a keychain key is eecc-redact's to replace or remove.
    """
    key = os.environ.get(ENV_VAR, "").strip()
    if key:
        return key, "environment"
    key = _read_keychain()
    return (key, "keychain") if key else ("", "")


def get_key() -> str:
    return key_source()[0]


def _read_keychain() -> str:
    try:
        import keyring

        return (keyring.get_password(APP_NAME, _ACCOUNT) or "").strip()
    except Exception:  # no Secret Service on a headless box, locked keyring, ...
        return ""


def set_key(key: str) -> None:
    import keyring

    keyring.set_password(APP_NAME, _ACCOUNT, key.strip())


def delete_key() -> None:
    import keyring

    try:
        keyring.delete_password(APP_NAME, _ACCOUNT)
    except Exception:
        pass


def environment(key: str) -> str:
    """ "production" for shr_live_, "sandbox" for shr_test_, else "unknown"."""
    if key.startswith("shr_live_"):
        return "production"
    if key.startswith("shr_test_"):
        return "sandbox"
    return "unknown"


def looks_like_key(text: str) -> bool:
    return _KEY.search(text) is not None


def scrub(text: str) -> str:
    """Replace anything key-shaped. Bug reports get pasted in public."""
    return _KEY.sub(r"shr_\1_***", text)
