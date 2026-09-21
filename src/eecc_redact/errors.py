"""Failures the user can see.

`message` is written for the person in front of the screen; `detail` is for a
bug report. The rule the tool rests on: when detection does not succeed, the
failure is shown and the unredacted image is never offered.
"""


class AppError(Exception):
    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


class Cancelled(AppError):
    """The user backed out. Not a failure; nothing is kept."""

    def __init__(self) -> None:
        super().__init__("Cancelled.")
