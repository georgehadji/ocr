"""Run the real Streamlit script against a prebuilt review document.

``AppTest`` executes ``editions/desktop/review_ui.py`` without a browser, so these
tests catch what the pure helpers cannot: a name bound in one branch and used
in another, which is how edit mode crashed with ``NameError`` (audit [9]).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")

from streamlit.testing.v1 import AppTest  # noqa: E402

from omniocr.domain.models import (  # noqa: E402
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRLine,
    RegionType,
    Script,
)
from omniocr.infrastructure.review import build_review_document  # noqa: E402

_UI = Path(__file__).resolve().parent.parent / "editions" / "desktop" / "review_ui.py"
_LINE_ID = "line-1-1"


def _app(*, edit_mode: bool, text: str = "καὶ <τὸν> λόγον") -> AppTest:
    page = DocumentPage(
        number=1,
        width=600,
        height=800,
        lines=(
            OCRLine(
                id=_LINE_ID,
                text=text,
                confidence=Confidence(92.0),
                bbox=BBox(10, 20, 300, 30),
                script=Script.POLYTONIC,
                region_type=RegionType.MAIN_TEXT,
                reading_order=1,
            ),
        ),
    )
    app = AppTest.from_file(str(_UI), default_timeout=60)
    app.session_state["review_document"] = build_review_document(
        DocumentStructure(pages=(page,)), [b""]
    )
    app.session_state["file_name"] = "book.pdf"
    app.session_state["edit_mode"] = edit_mode
    return app.run()


def test_view_mode_renders_without_error() -> None:
    assert not _app(edit_mode=False).exception


def test_edit_mode_renders_without_error() -> None:
    """Edit mode used the view-only ``bar`` and ``badge`` names unbound."""
    app = _app(edit_mode=True)

    assert not app.exception
    assert [box.value for box in app.text_input if box.key == f"em_{_LINE_ID}"] == [
        "καὶ <τὸν> λόγον"
    ]


def test_undo_button_reverses_the_last_edit_in_order() -> None:
    app = _app(edit_mode=False)

    for typed in ("one", "two"):
        app.text_area(key=f"ta_{_LINE_ID}").set_value(typed).run()
    assert app.session_state["edited_lines"] == {_LINE_ID: "two"}

    undo = next(b for b in app.sidebar.button if b.label == "↩ Undo last action")
    undo.click().run()
    assert app.session_state["edited_lines"] == {_LINE_ID: "one"}

    undo = next(b for b in app.sidebar.button if b.label == "↩ Undo last action")
    undo.click().run()
    assert app.session_state["edited_lines"] == {}


def test_clear_all_corrections_is_not_written_back_by_the_text_box() -> None:
    app = _app(edit_mode=False)
    app.text_area(key=f"ta_{_LINE_ID}").set_value("typed").run()

    clear = next(b for b in app.sidebar.button if b.label == "Clear all corrections")
    clear.click().run()

    assert app.session_state["edited_lines"] == {}


def test_angle_brackets_in_ocr_text_are_escaped_not_parsed_as_html() -> None:
    """``<τὸν>`` read as a tag disappears from the page the scholar is checking."""
    app = _app(edit_mode=False)

    assert any("&lt;τὸν&gt;" in block.value for block in app.markdown)
    assert not any("<τὸν>" in block.value for block in app.markdown)
