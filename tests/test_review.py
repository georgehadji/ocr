"""Tests for the review UI data model and helpers."""

from __future__ import annotations

from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    EngineRun,
    ModelRef,
    OCRBlock,
    OCRLine,
    RegionType,
    Script,
    Suggestion,
)
from omniocr.infrastructure.review import (
    ReviewDocument,
    build_review_document,
    build_review_page,
    group_suggestions_by_reason,
)


def _page(number: int = 1) -> DocumentPage:
    return DocumentPage(
        number=number,
        width=600,
        height=800,
        lines=(
            OCRLine(
                id=f"line-{number}-1",
                text="Ἑλληνικά",
                confidence=Confidence(92.0),
                bbox=BBox(10, 20, 300, 30),
                script=Script.POLYTONIC,
                region_type=RegionType.MAIN_TEXT,
                reading_order=1,
            ),
            OCRLine(
                id=f"line-{number}-2",
                text="low conf text",
                confidence=Confidence(30.0),
                bbox=BBox(10, 60, 200, 20),
                script=Script.POLYTONIC,
                region_type=RegionType.APPARATUS,
                reading_order=2,
            ),
        ),
        suggestions=(
            Suggestion(
                line_id="line-1-1",
                source_text="Ἑλληνικά",
                suggestion_text="ἑλληνικά",
                reason="check_casing",
                reversible=True,
            ),
        ),
    )


def test_build_review_page_structure() -> None:
    """ReviewPage preserves page metadata and line details."""
    page = _page()
    review = build_review_page(page, b"image-data")

    assert review.number == 1
    assert review.width == 600
    assert review.height == 800
    assert review.image_bytes == b"image-data"
    assert len(review.lines) == 2


def test_review_line_confidence_flag() -> None:
    """Lines below 50% confidence are marked as low confidence."""
    page = _page()
    review = build_review_page(page, b"")

    assert review.lines[0].text == "Ἑλληνικά"
    assert review.lines[0].confidence == 92.0
    assert not review.lines[0].is_low_confidence

    assert review.lines[1].text == "low conf text"
    assert review.lines[1].confidence == 30.0
    assert review.lines[1].is_low_confidence


def test_review_line_preserves_metadata() -> None:
    """Script, region type, and reading order are preserved."""
    review = build_review_page(_page(), b"")

    assert review.lines[0].script == "polytonic"
    assert review.lines[0].region_type == "main"
    assert review.lines[0].reading_order == 1
    assert review.lines[1].region_type == "apparatus"
    assert review.lines[1].reading_order == 2


def test_review_line_suggestions_grouped_by_id() -> None:
    """Suggestions are assigned to the correct line by line_id."""
    suggestions = {
        "line-1-1": (Suggestion("line-1-1", "source", "suggestion", "check", True),),
    }
    review = build_review_page(_page(), b"", suggestions)

    matching = [line for line in review.lines if line.line_id == "line-1-1"]
    assert len(matching) == 1
    assert len(matching[0].suggestions) == 1
    assert matching[0].suggestions[0].reason == "check"


def test_build_review_document_assembles_multiple_pages() -> None:
    """ReviewDocument correctly stitches pages together."""
    doc = DocumentStructure(pages=(_page(1), _page(2)))
    review = build_review_document(doc, [b"img-1", b"img-2"])

    assert len(review.pages) == 2
    assert review.pages[0].number == 1
    assert review.pages[0].image_bytes == b"img-1"
    assert review.pages[1].number == 2
    assert review.pages[1].image_bytes == b"img-2"


def test_group_suggestions_by_reason_counts() -> None:
    """Suggestion counts are correctly tallied across pages."""
    doc = DocumentStructure(pages=(_page(1), _page(2)))
    review = build_review_document(doc, [b"", b""])
    counts = group_suggestions_by_reason(review)

    assert counts.get("check_casing", 0) == 2  # one per page


def test_build_review_page_empty_lines() -> None:
    """A page with no lines produces an empty review page."""
    page = DocumentPage(number=1, width=100, height=100)
    review = build_review_page(page, b"")
    assert len(review.lines) == 0
    assert len(review.failures) == 0


def test_review_line_carries_engine_blocks() -> None:
    """Per-engine blocks survive into ReviewLine so the UI can show disagreement.

    Without this the review UI's engine-comparison panel raises
    ``AttributeError: 'ReviewLine' object has no attribute 'blocks'``.
    """
    run = EngineRun(
        engine="tesseract",
        model_ref=ModelRef(engine="tesseract", model_name="grc", model_hash="abc"),
        model_hash="abc",
        params=("psm6",),
        timestamp="2026-08-01T00:00:00+00:00",
    )
    block = OCRBlock(
        id="blk-1",
        text="Ἑλληνικά",
        confidence=Confidence(92.0),
        bbox=BBox(10, 20, 300, 30),
        provenance=run,
    )
    page = DocumentPage(
        number=1,
        width=600,
        height=800,
        lines=(
            OCRLine(
                id="line-1",
                text="Ἑλληνικά",
                confidence=Confidence(92.0),
                bbox=BBox(10, 20, 300, 30),
                blocks=(block,),
            ),
        ),
    )

    review = build_review_page(page, b"")

    assert len(review.lines[0].blocks) == 1
    assert review.lines[0].blocks[0].provenance is not None
    assert review.lines[0].blocks[0].provenance.engine == "tesseract"


def test_review_line_blocks_default_empty() -> None:
    """A line with no per-engine blocks yields an empty tuple, never None."""
    review = build_review_page(_page(1), b"")
    assert review.lines[0].blocks == ()


class TestPersistReviewedLines:
    """The link that gives the training pipeline data.

    review_line_to_correction and SqliteCorrectionStore both already existed;
    nothing called them together, so reviewed corrections never reached the
    store TrainingOrchestrator.all_accepted() reads.
    """

    @staticmethod
    def _document() -> ReviewDocument:
        from omniocr.infrastructure.review import build_review_document

        return build_review_document(DocumentStructure(pages=(_page(1),)), [b""])

    def test_appends_only_lines_the_reviewer_touched(self, tmp_path) -> None:
        """An untouched OCR line is not ground truth (CLAUDE.md rule 1)."""
        from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
        from omniocr.infrastructure.review import persist_reviewed_lines

        store = SqliteCorrectionStore(tmp_path / "c.db")
        document = self._document()
        touched = document.pages[0].lines[0].line_id

        result = persist_reviewed_lines(
            store, document, {touched: "διορθωμένο"}, corrected_by="scholar"
        )

        assert result.is_ok()
        assert result.value == 1
        stored = list(store.all_accepted())
        assert len(stored) == 1
        assert stored[0].line_id == touched
        assert stored[0].corrected_text == "διορθωμένο"
        assert stored[0].corrected_by == "scholar"
        store.close()

    def test_persists_nothing_when_reviewer_touched_nothing(self, tmp_path) -> None:
        from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
        from omniocr.infrastructure.review import persist_reviewed_lines

        store = SqliteCorrectionStore(tmp_path / "c.db")

        result = persist_reviewed_lines(store, self._document(), {}, corrected_by="scholar")

        assert result.is_ok()
        assert result.value == 0
        assert list(store.all_accepted()) == []
        store.close()

    def test_unknown_line_ids_are_ignored(self, tmp_path) -> None:
        """A stale id from session state must not invent a correction."""
        from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
        from omniocr.infrastructure.review import persist_reviewed_lines

        store = SqliteCorrectionStore(tmp_path / "c.db")

        result = persist_reviewed_lines(
            store, self._document(), {"no-such-line": "text"}, corrected_by="s"
        )

        assert result.is_ok()
        assert result.value == 0
        store.close()

    def test_carries_page_number_bbox_and_script_from_the_line(self, tmp_path) -> None:
        from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
        from omniocr.infrastructure.review import persist_reviewed_lines

        store = SqliteCorrectionStore(tmp_path / "c.db")
        document = self._document()
        line = document.pages[0].lines[0]

        persist_reviewed_lines(store, document, {line.line_id: "τ"}, corrected_by="s")

        stored = list(store.all_accepted())[0]
        assert stored.page_number == document.pages[0].number
        assert stored.bbox == line.bbox
        assert stored.script.value == line.script
        store.close()

    def test_second_call_appends_rather_than_mutating(self, tmp_path) -> None:
        """Append-only: a revised transcription is a new row, not an update.

        This is the audit trail scholarship needs — the earlier reading stays
        recoverable, and consumers order by corrected_at.
        """
        from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
        from omniocr.infrastructure.review import persist_reviewed_lines

        store = SqliteCorrectionStore(tmp_path / "c.db")
        document = self._document()
        line_id = document.pages[0].lines[0].line_id

        persist_reviewed_lines(store, document, {line_id: "first"}, corrected_by="s")
        persist_reviewed_lines(store, document, {line_id: "second"}, corrected_by="s")

        stored = list(store.all_accepted())
        assert len(stored) == 2
        assert [c.corrected_text for c in stored] == ["first", "second"]
        store.close()

    def test_survives_restart(self, tmp_path) -> None:
        """Corrections must outlive the process — the point of persisting."""
        from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
        from omniocr.infrastructure.review import persist_reviewed_lines

        db = tmp_path / "c.db"
        document = self._document()
        line_id = document.pages[0].lines[0].line_id

        store = SqliteCorrectionStore(db)
        persist_reviewed_lines(store, document, {line_id: "μένει"}, corrected_by="s")
        store.close()

        reopened = SqliteCorrectionStore(db)
        stored = list(reopened.all_accepted())
        assert len(stored) == 1
        assert stored[0].corrected_text == "μένει"
        reopened.close()

    def test_store_error_short_circuits_and_propagates(self, tmp_path) -> None:
        from omniocr.domain.errors import CorrectionStoreError
        from omniocr.domain.result import Err
        from omniocr.infrastructure.review import persist_reviewed_lines

        class _FailingStore:
            def append(self, correction):
                return Err(CorrectionStoreError("disk full"))

            def append_many(self, corrections):
                return Err(CorrectionStoreError("disk full"))

            def for_document(self, document_id):
                return []

            def all_accepted(self):
                return []

        document = self._document()
        line_id = document.pages[0].lines[0].line_id

        result = persist_reviewed_lines(_FailingStore(), document, {line_id: "x"}, corrected_by="s")

        assert result.is_err()
        assert "disk full" in str(result.error)

    def test_failure_partway_saves_nothing_and_a_retry_does_not_duplicate(self, tmp_path) -> None:
        """The batch is one transaction: the first rows do not survive the failure."""
        import sqlite3

        from omniocr.infrastructure.corrections_store import SqliteCorrectionStore
        from omniocr.infrastructure.review import persist_reviewed_lines

        db = tmp_path / "c.db"
        store = SqliteCorrectionStore(db)
        document = self._document()
        first, second = (line.line_id for line in document.pages[0].lines)

        # A trigger makes the second insert fail after the first has succeeded.
        raw = sqlite3.connect(str(db))
        raw.execute(
            "CREATE TRIGGER boom BEFORE INSERT ON corrections WHEN NEW.corrected_text = 'boom' "
            "BEGIN SELECT RAISE(ABORT, 'boom'); END"
        )
        raw.commit()

        failed = persist_reviewed_lines(store, document, {first: "ok", second: "boom"}, "s")

        assert failed.is_err()
        assert list(store.all_accepted()) == []

        raw.execute("DROP TRIGGER boom")
        raw.commit()
        raw.close()
        retried = persist_reviewed_lines(store, document, {first: "ok", second: "fine"}, "s")

        assert retried.value == 2
        assert [c.corrected_text for c in store.all_accepted()] == ["ok", "fine"]
        store.close()


class TestUndoLog:
    @staticmethod
    def _stores() -> dict[str, dict[str, str]]:
        return {"ground_truth_lines": {}, "edited_lines": {}}

    def test_undo_after_setting_the_same_key_twice_restores_the_middle_value(self) -> None:
        from omniocr.infrastructure.review import UndoLog

        stores, log = self._stores(), UndoLog()
        log.set(stores, "edited_lines", "a", "one")
        log.set(stores, "edited_lines", "a", "two")
        log.set(stores, "edited_lines", "a", "three")

        log.undo(stores)
        assert stores["edited_lines"] == {"a": "two"}
        log.undo(stores)
        assert stores["edited_lines"] == {"a": "one"}
        log.undo(stores)
        assert stores["edited_lines"] == {}

    def test_undo_reverses_the_true_last_action_across_stores(self) -> None:
        """Dict order is not action order: the key set first stays first when rewritten."""
        from omniocr.infrastructure.review import UndoLog

        stores, log = self._stores(), UndoLog()
        log.set(stores, "ground_truth_lines", "a", "accepted")
        log.set(stores, "edited_lines", "b", "typed")
        log.set(stores, "ground_truth_lines", "a", "accepted again")

        log.undo(stores)

        assert stores["ground_truth_lines"] == {"a": "accepted"}
        assert stores["edited_lines"] == {"b": "typed"}

    def test_removing_a_key_is_undoable(self) -> None:
        from omniocr.infrastructure.review import UndoLog

        stores, log = self._stores(), UndoLog()
        log.set(stores, "edited_lines", "a", "typed")
        log.set(stores, "edited_lines", "a", None)
        assert stores["edited_lines"] == {}

        log.undo(stores)

        assert stores["edited_lines"] == {"a": "typed"}

    def test_a_write_that_changes_nothing_is_not_an_undo_step(self) -> None:
        from omniocr.infrastructure.review import UndoLog

        stores, log = self._stores(), UndoLog()
        log.set(stores, "edited_lines", "a", "typed")
        log.set(stores, "edited_lines", "a", "typed")
        log.set(stores, "edited_lines", "missing", None)

        assert len(log) == 1

    def test_undo_with_an_empty_log_is_a_no_op(self) -> None:
        from omniocr.infrastructure.review import UndoLog

        assert UndoLog().undo(self._stores()) is None

    def test_undo_returns_the_entry_it_reversed(self) -> None:
        from omniocr.infrastructure.review import UndoEntry, UndoLog

        stores, log = self._stores(), UndoLog()
        log.set(stores, "edited_lines", "a", "typed")

        assert log.undo(stores) == UndoEntry("edited_lines", "a", None)


class TestLineViewHtml:
    def test_angle_brackets_are_escaped(self) -> None:
        from omniocr.infrastructure.review import line_view_html

        rendered = line_view_html("καὶ <τὸν> λόγον", 90.0, changed=False)

        assert "&lt;τὸν&gt;" in rendered
        assert "<τὸν>" not in rendered

    def test_markup_in_the_text_cannot_inject_elements(self) -> None:
        from omniocr.infrastructure.review import line_view_html

        rendered = line_view_html('<img src=x onerror="alert(1)">', 90.0, changed=True)

        assert "<img" not in rendered

    def test_changed_text_is_marked(self) -> None:
        from omniocr.infrastructure.review import line_view_html

        assert "underline" in line_view_html("x", 90.0, changed=True)
        assert "underline" not in line_view_html("x", 90.0, changed=False)

    def test_badge_follows_confidence(self) -> None:
        from omniocr.infrastructure.review import confidence_style

        assert [confidence_style(c)[0] for c in (10, 50, 70, 95)] == ["🔴", "🟡", "🟢", "✅"]


class TestPageImages:
    @staticmethod
    def _png() -> bytes:
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (40, 30), "white").save(buffer, format="PNG")
        return buffer.getvalue()

    def test_a_png_upload_gives_its_own_image_and_builds_a_review_document(self) -> None:
        """An image used to be handed to PyMuPDF as a PDF, which raised."""
        from omniocr.infrastructure.review import page_images

        png = self._png()
        images = page_images(png)

        assert images == [png]
        document = build_review_document(DocumentStructure(pages=(_page(1),)), images)
        assert document.pages[0].image_bytes == png

    def test_a_multi_page_tiff_gives_one_image_per_frame(self) -> None:
        import io

        from PIL import Image

        from omniocr.infrastructure.review import page_images

        frames = [Image.new("RGB", (40, 30), color) for color in ("white", "black", "red")]
        buffer = io.BytesIO()
        frames[0].save(buffer, format="TIFF", save_all=True, append_images=frames[1:])

        images = page_images(buffer.getvalue())

        assert len(images) == 3
        assert all(image.startswith(b"\x89PNG") for image in images)

    def test_a_pdf_gives_one_png_per_page(self) -> None:
        import pytest

        fitz = pytest.importorskip("fitz")
        from omniocr.infrastructure.review import page_images

        pdf = fitz.open()
        pdf.new_page()
        pdf.new_page()
        data = pdf.tobytes()
        pdf.close()

        images = page_images(data)

        assert len(images) == 2
        assert all(image.startswith(b"\x89PNG") for image in images)

    def test_unreadable_documents_give_no_images(self) -> None:
        from omniocr.infrastructure.review import page_images

        assert page_images(b"not an image") == []
        assert page_images(b"%PDF-1.7 truncated") == []
