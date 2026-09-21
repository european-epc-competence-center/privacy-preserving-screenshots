"""Plain data passed between the layers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    """A rectangle in image pixels: left, top, width, height, as ShinrAI reports it."""

    x: int
    y: int
    w: int
    h: int

    def padded(self, pad: int) -> "Box":
        """Grow by `pad` on every side, clamped at the image origin.

        Only growth that actually happened is added to the size: a box at x=0
        cannot move left, and must not gain that width on the right instead.
        """
        x, y = max(0, self.x - pad), max(0, self.y - pad)
        return Box(x, y, self.w + (self.x - x) + pad, self.h + (self.y - y) + pad)

    def contains(self, other: "Box") -> bool:
        return (
            self.x <= other.x
            and self.y <= other.y
            and self.x + self.w >= other.x + other.w
            and self.y + self.h >= other.y + other.h
        )


@dataclass(frozen=True)
class Finding:
    """One detected entity. An IBAN comes back as one finding with a box per OCR
    token, which is why the review toggles findings, not boxes."""

    info_type: str
    boxes: tuple[Box, ...] = ()


@dataclass(frozen=True)
class Detection:
    findings: tuple[Finding, ...] = ()
    records_remaining: int | None = None
    warnings: str = ""

    @property
    def boxes(self) -> list[Box]:
        return [box for finding in self.findings for box in finding.boxes]
