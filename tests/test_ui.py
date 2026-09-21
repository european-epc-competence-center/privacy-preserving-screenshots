"""The Qt layer, driven headlessly in child processes: crossing threads, the
selection overlay, the review gate, and the single instance socket.

Each case runs in its own offscreen process so that the bug these guard
against - a hang - fails one test instead of freezing the suite.
"""

import os
import subprocess
import sys
import uuid

from conftest import child, source

QT = """
    import threading
    from PySide6.QtCore import QPoint, Qt, QTimer
    from PySide6.QtGui import QColor, QGuiApplication, QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QPushButton, QTreeWidget
    from eecc_redact.errors import AppError, Cancelled
    from eecc_redact.ui import ensure_app, main_thread, run_blocking
    app = ensure_app()
    def visible():
        return next(w for w in QApplication.topLevelWidgets() if w.isVisible())
"""


def test_run_blocking_returns_from_a_worker_and_reraises_on_the_ui_thread():
    out = child(
        QT,
        """
        main = threading.current_thread()
        where = run_blocking(lambda: threading.current_thread() is main)
        print("WORKER_ON_MAIN", where)
        try:
            run_blocking(lambda: (_ for _ in ()).throw(AppError("boom", detail="d")))
        except AppError as exc:
            print("RAISED", exc.message, exc.detail, threading.current_thread() is main)
        seen = []
        call = main_thread(lambda: seen.append(threading.current_thread() is main))
        threading.Thread(target=call).start()
        QTimer.singleShot(300, app.quit)
        app.exec()
        print("CALLBACK_ON_MAIN", seen)
    """,
    )
    assert "WORKER_ON_MAIN False" in out
    assert "RAISED boom d True" in out
    assert "CALLBACK_ON_MAIN [True]" in out


OVERLAY = (
    QT
    + """
    from eecc_redact.capture.overlay import select_region
    from eecc_redact.imaging import size
    import eecc_redact.capture.overlay as overlay
    screen = QGuiApplication.primaryScreen()
    frame = QPixmap(screen.geometry().size())
    frame.fill(QColor("white"))
    overlay.grab_screens = lambda: [(screen, frame)]
"""
)


def test_a_drag_returns_exactly_the_dragged_region():
    out = child(
        OVERLAY,
        """
        def drag():
            w = visible()
            QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                             QPoint(50, 50))
            QTest.mouseMove(w, QPoint(250, 200))
            QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                               QPoint(250, 200))
        QTimer.singleShot(200, drag)
        print("SIZE", *size(select_region()))
    """,
    )
    assert "SIZE 200 150" in out  # not 201 x 151


def test_escape_and_closing_cancel_without_hanging():
    out = child(
        OVERLAY,
        """
        escape = lambda: QTest.keyClick(visible(), Qt.Key.Key_Escape)
        close = lambda: visible().close()
        for end in (escape, close):
            QTimer.singleShot(200, end)
            try:
                select_region()
                print("RETURNED")
            except Cancelled:
                print("CANCELLED")
    """,
    )
    assert out.split() == ["CANCELLED", "CANCELLED"]


REVIEW = (
    QT
    + """
    import io
    from PIL import Image
    from eecc_redact.config import Config
    from eecc_redact.models import Box, Detection, Finding
    from eecc_redact.ui.review import ReviewDialog, still_covering
    buffer = io.BytesIO()
    Image.new("RGB", (200, 100), "white").save(buffer, format="PNG")
    png = buffer.getvalue()
    detection = Detection(findings=(
        Finding("EMAIL_ADDRESS", (Box(10, 10, 50, 10),)),
        Finding("PERSON_NAME", (Box(100, 10, 30, 10), Box(140, 10, 30, 10))),
        Finding("PHONE_NUMBER", (Box(100, 10, 30, 10),)),  # same pixels as the name's first box
    ))
    def pixel(data, x, y):
        with Image.open(io.BytesIO(data)) as image:
            return image.getpixel((x, y))
    def leaf(dialog, index):
        return next(item for item in dialog._items()
                    if item.data(0, Qt.ItemDataRole.UserRole) == index)
"""
)


def test_everything_starts_covered_and_revealing_is_explicit():
    out = child(
        REVIEW,
        """
        dialog = ReviewDialog(png, detection, Config(box_padding=0))
        def drive():
            print("COVERED", pixel(dialog.export(), 20, 15), pixel(dialog.export(), 5, 5))
            leaf(dialog, 0).setCheckState(0, Qt.CheckState.Unchecked)
            leaf(dialog, 2).setCheckState(0, Qt.CheckState.Unchecked)
            QTimer.singleShot(50, after)
        def after():
            print("REVEALED", pixel(dialog.export(), 20, 15), pixel(dialog.export(), 110, 15))
            print("HINT", leaf(dialog, 2).text(0))
            dialog.findChild(QPushButton, "copy").click()
        QTimer.singleShot(100, drive)
        dialog.exec()
        pasted = not QGuiApplication.clipboard().image().isNull()
        print("OUTCOME", dialog.outcome, "CLIPBOARD", pasted)
        dialog.set_all(False)
        print("ALL_REVEALED", pixel(dialog.export(), 150, 15))
    """,
    )
    assert "COVERED (0, 0, 0) (255, 255, 255)" in out
    assert "REVEALED (255, 255, 255) (0, 0, 0)" in out  # the phone stays hidden under the name
    assert "still hidden by Person Name" in out
    assert "OUTCOME copied CLIPBOARD True" in out
    assert "ALL_REVEALED (255, 255, 255)" in out


def test_still_covering_names_only_complete_covers():
    out = child(
        REVIEW,
        """
        active = [("PERSON_NAME", Box(100, 10, 30, 10)), ("IBAN_CODE", Box(0, 0, 200, 100))]
        print(sorted(still_covering((Box(100, 10, 30, 10),), active)))
        print(sorted(still_covering((Box(100, 10, 30, 10), Box(140, 10, 30, 10)), active)))
        partial = [("PERSON_NAME", Box(100, 10, 20, 10))]
        print(sorted(still_covering((Box(100, 10, 30, 10),), partial)))
    """,
    )
    # Both kinds are named when each hides part: the user must untick both to reveal.
    both = "['IBAN_CODE', 'PERSON_NAME']"
    assert out.splitlines() == [both, both, "[]"]


def test_the_failure_dialog_offers_retry_or_discard_never_the_image():
    out = child(
        QT,
        """
        from PySide6.QtWidgets import QMessageBox
        from eecc_redact.ui.review import show_failure
        def press(text):
            box = visible()
            next(b for b in box.buttons() if b.text() == text).click()
        QTimer.singleShot(100, lambda: press("Try again"))
        print(show_failure(AppError("ShinrAI is down", detail="key shr_live_SECRET_1 used")))
        QTimer.singleShot(100, lambda: press("Discard"))
        print(show_failure(AppError("ShinrAI is down")))
    """,
    )
    assert out.split() == ["retry", "discard"]


INSTANCE = """
    import sys
    from PySide6.QtCore import QCoreApplication, QTimer
    from eecc_redact import instance
    app = QCoreApplication([])
    name = sys.argv[1]
"""


def test_one_tray_per_name_and_messages_reach_it():
    name = f"eecc-redact-test-{uuid.uuid4().hex[:8]}"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    server = subprocess.Popen(
        [
            sys.executable,
            "-c",
            source(
                INSTANCE,
                """
        got = []
        def on_message(text):
            got.append(text)
            if text == "quit":
                print("GOT", got, flush=True)
                app.quit()
        server = instance.claim(on_message, name)
        print("CLAIMED" if server else "TAKEN", flush=True)
        QTimer.singleShot(15000, app.quit)
        app.exec()
    """,
            ),
            name,
        ],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert server.stdout.readline().strip() == "CLAIMED"
        out = child(
            INSTANCE,
            """
            print("SECOND", instance.claim(lambda text: None, name))
            instance.server_name = lambda: name
            print("SENT", instance.send("capture", name), "LEFT", instance.request_quit())
        """,
            args=(name,),
        )
        assert "SECOND None" in out and "SENT True LEFT True" in out
        rest, err = server.communicate(timeout=20)
        assert "GOT ['capture', 'quit']" in rest, err
    finally:
        if server.poll() is None:
            server.kill()
    out = child(INSTANCE, 'print("NOBODY", instance.send("show", name))', args=(name,))
    assert "NOBODY False" in out
