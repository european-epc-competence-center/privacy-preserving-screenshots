"""eecc-redact: screenshots with the personal data already covered.

A captured region goes to the user's own ShinrAI deployment, which reports
where names, email addresses, phone numbers, IBANs and addresses are. eecc-redact
draws the boxes locally and only then lets the image be copied or saved.
"""

from importlib.metadata import PackageNotFoundError, version

#: The command, the display name, the config folder and the keyring service.
APP_NAME = "eecc-redact"
#: The import package: for `python -m`, and wherever a hyphen is not allowed.
MODULE = __name__
#: Reverse-DNS ID for the desktop entry and the portals.
#: Changing it after release resets every user's hotkey approval on Linux.
APP_ID = "io.github.european_epc_competence_center.eecc_redact"

try:
    __version__ = version(APP_NAME)
except PackageNotFoundError:  # a checkout that was never installed
    __version__ = "0.0.0"
