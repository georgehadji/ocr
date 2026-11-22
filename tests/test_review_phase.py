"""The review session's phase, session-file reading and commit de-duplication."""

from __future__ import annotations

import pytest

from omniocr.domain.state import transition
from omniocr.infrastructure.review import (
    REVIEW_TRANSITIONS,
    ReviewPhase,
    SessionData,
    pending_commit,
    read_session,
    review_phase,
)


@pytest.mark.parametrize(
    ("loaded", "upload", "error", "expected"),
    [
        (False, False, None, ReviewPhase.EMPTY),
        (False, False, "boom", ReviewPhase.EMPTY),  # stale error, upload gone
        (False, True, None, ReviewPhase.OCR_RUNNING),
        (False, True, "boom", ReviewPhase.OCR_FAILED),
        (True, True, None, ReviewPhase.REVIEWING),
        (True, False, None, ReviewPhase.REVIEWING),
        (True, True, "boom", ReviewPhase.REVIEWING),
    ],
)
def test_review_phase(loaded: bool, upload: bool, error: str | None, expected: ReviewPhase) -> None:
    assert review_phase(document_loaded=loaded, upload_present=upload, ocr_error=error) is expected


def test_a_failed_run_can_retry_or_be_abandoned() -> None:
    assert transition(
        "Review session", REVIEW_TRANSITIONS, ReviewPhase.OCR_FAILED, ReviewPhase.OCR_RUNNING
    ).is_ok()
    assert transition(
        "Review session", REVIEW_TRANSITIONS, ReviewPhase.OCR_FAILED, ReviewPhase.EMPTY
    ).is_ok()


def test_a_review_cannot_jump_back_into_ocr() -> None:
    assert not transition(
        "Review session", REVIEW_TRANSITIONS, ReviewPhase.REVIEWING, ReviewPhase.OCR_RUNNING
    ).is_ok()


def test_read_session_of_a_complete_file() -> None:
    data = read_session(
        {
            "ground_truth_lines": {"p1-1": "a"},
            "edited_lines": {"p1-2": "b"},
            "committed_lines": {"p1-1": "a"},
            "current_page": 4,
        }
    )
    assert data == SessionData({"p1-1": "a"}, {"p1-2": "b"}, {"p1-1": "a"}, 4)


def test_read_session_of_an_old_file_without_committed_lines() -> None:
    data = read_session({"ground_truth_lines": {"p1-1": "a"}})
    assert data.committed_lines == {}
    assert data.edited_lines == {}
    assert data.current_page == 0


@pytest.mark.parametrize("page", [-1, True, "3", 2.5, None])
def test_read_session_rejects_a_bad_page(page: object) -> None:
    assert read_session({"current_page": page}).current_page == 0


def test_read_session_drops_non_text_entries() -> None:
    data = read_session(
        {"ground_truth_lines": {"p1-1": "a", "p1-2": 7, 3: "x"}, "edited_lines": []}
    )
    assert data.ground_truth_lines == {"p1-1": "a"}
    assert data.edited_lines == {}


def test_pending_commit_skips_what_is_already_committed() -> None:
    assert pending_commit({"a": "x", "b": "y"}, {"a": "x"}) == {"b": "y"}


def test_pending_commit_keeps_a_revised_line() -> None:
    assert pending_commit({"a": "x2"}, {"a": "x"}) == {"a": "x2"}


def test_pending_commit_of_nothing_is_empty() -> None:
    assert pending_commit({}, {"a": "x"}) == {}
