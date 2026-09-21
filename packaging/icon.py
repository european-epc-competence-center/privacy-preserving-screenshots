"""Write the app icon from the same drawing the tray uses. Run by the build scripts.

python packaging/icon.py build/eecc-redact.ico      # Windows: every size in one file
python packaging/icon.py build/<app id>.png         # Linux: 256 px for the AppImage
"""

import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice

from eecc_redact.ui import ensure_app, icon

ICO_SIZES = [(side, side) for side in (16, 24, 32, 48, 64, 128, 256)]


def main(target: Path) -> None:
    ensure_app()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    icon(256).pixmap(256).save(buffer, "PNG")
    image = Image.open(io.BytesIO(bytes(buffer.data())))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.suffix == ".ico":
        image.save(target, sizes=ICO_SIZES)
    else:
        image.save(target)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
