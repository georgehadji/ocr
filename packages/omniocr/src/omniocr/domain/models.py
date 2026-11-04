from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple


@dataclass(frozen=True, slots=True)
class BBox:
    x: int
    y: int
    w: int
    h: int

    def __post_init__(self) -> None:
        if min(self.x, self.y, self.w, self.h) < 0:
            raise ValueError("bounding box values must be non-negative")
        if self.w == 0 or self.h == 0:
            raise ValueError("bounding box width and height must be positive")

    @property
    def right(self) -> int:
        return self.x + self.w

    @property
    def bottom(self) -> int:
        return self.y + self.h


@dataclass(frozen=True, slots=True)
class Confidence:
    value: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.value <= 100.0:
            raise ValueError("confidence must be between 0 and 100")


class Script(str, Enum):
    MODERN = "modern"
    POLYTONIC = "polytonic"
    ANCIENT = "ancient"
    BYZANTINE = "byzantine"
    PONTIAN = "pontian"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class AgreementTier(str, Enum):
    """How much the engines agreed on a line (ENHANCEMENT_PLAN A5).

    The honest confidence number. An engine's self-reported confidence is
    known to lie on exactly the lines that matter — `ScriptAwareReconciler`
    records a Kraken model reporting high confidence while emitting Latin
    mush — whereas independent readers disagreeing is direct evidence that a
    line is hard.

    Used to order the review queue and exported as an attribute in ALTO and
    PAGE, because an archival consumer should be able to see how contested a
    line was.

    **Never gate export on this.** A `SPLIT` line exports its text, flagged.
    Dropping low-agreement lines would be a faithfulness violation dressed up
    as quality control.
    """

    UNANIMOUS = "unanimous"  # every candidate identical after NFC
    MAJORITY = "majority"  # at least two agree, at least one differs
    SPLIT = "split"  # no two candidates agree
    SINGLE = "single"  # only one candidate existed
    UNKNOWN = "unknown"


class RegionType(str, Enum):
    """Page region classification for critical-edition layout.

    Kraken pageseg returns these as the ``category`` attribute on each record.
    """

    MAIN_TEXT = "main"
    APPARATUS = "apparatus"
    SCHOLIA = "scholia"
    RUNNING_HEAD = "running_head"
    FOOTNOTE = "footnote"
    MARGIN = "margin"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ModelRef:
    engine: str
    model_name: str
    model_hash: str
    params: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class EngineRun:
    engine: str
    model_ref: ModelRef
    model_hash: str
    params: Tuple[str, ...]
    timestamp: str
    # Which preprocessing variant produced this reading (ENHANCEMENT_PLAN A3).
    # Empty when only one variant ran. Without it A4's merge cannot say whether
    # two disagreeing readings came from two engines or from one engine given
    # two differently binarized images — and an unattributable merge is exactly
    # what CLAUDE.md rule 1 forbids.
    variant: str = ""


@dataclass(frozen=True, slots=True)
class AlignedToken:
    """One token position across every engine that read the line.

    `readings` carries the per-engine provenance that makes a merge auditable:
    a reviewer can see which engine supplied the winning token rather than
    being handed an unattributed synthesis. Kept as a sorted tuple of pairs
    rather than a dict so the value stays hashable and comparable.
    """

    position: int
    pivot_text: str
    winning_text: str
    readings: Tuple[Tuple[str, str], ...]

    @property
    def contested(self) -> bool:
        """True when the engines did not all read this position the same way."""
        return len({text for _, text in self.readings}) > 1


@dataclass(frozen=True, slots=True)
class Suggestion:
    line_id: str
    source_text: str
    suggestion_text: str
    reason: str
    reversible: bool = True
    # Where ``source_text`` sits in the line's text; None means the suggestion
    # replaces the whole line. A flag (``reversible=False``) has no replacement.
    start: int | None = None
    end: int | None = None


def apply_suggestion(text: str, suggestion: Suggestion) -> str:
    """The one place a suggestion is applied to a line's text. Pure.

    Without a span the suggestion replaces the whole line. With one it replaces
    only ``source_text``, so several accepted suggestions on one line compose
    instead of the last overwriting the rest. A flag is not applicable, and a
    span the text no longer matches is relocated only when ``source_text`` now
    occurs exactly once (an earlier accepted edit changed the length); anything
    ambiguous is left alone rather than guessed.
    """
    if not suggestion.reversible:
        return text
    if suggestion.start is None or suggestion.end is None:
        return suggestion.suggestion_text
    start, end = suggestion.start, suggestion.end
    if text[start:end] != suggestion.source_text:
        if text.count(suggestion.source_text) != 1:
            return text
        start = text.index(suggestion.source_text)
        end = start + len(suggestion.source_text)
    return text[:start] + suggestion.suggestion_text + text[end:]


def apply_suggestions(text: str, suggestions: Sequence[Suggestion]) -> str:
    """Apply several suggestions to one line: whole-line first, then spans right to left.

    Right to left keeps each remaining span valid after a replacement that
    changes the length.
    """
    for suggestion in suggestions:
        if suggestion.start is None:
            text = apply_suggestion(text, suggestion)
    spans = sorted(
        (s for s in suggestions if s.start is not None),
        key=lambda s: -(s.start or 0),
    )
    for suggestion in spans:
        text = apply_suggestion(text, suggestion)
    return text


@dataclass(frozen=True, slots=True)
class PageFailure:
    """A page-local failure retained for review without aborting the job."""

    error_type: str
    message: str


@dataclass(frozen=True, slots=True)
class UnreadablePage:
    """A page the source could not read; it travels as data, not as an exception.

    Satisfies the ``RawPage`` protocol, so one bad page in a long PDF is
    reported as a ``PageFailure`` and the pages after it are still read.
    """

    number: int
    error: str
    content: bytes = b""
    width: int = 1
    height: int = 1


def page_scoped_id(page_number: int, local_id: str) -> str:
    """Line ids are unique per document: engines number lines per page."""
    return f"p{page_number}-{local_id}"


@dataclass(frozen=True, slots=True)
class PipelineEvent:
    """Immutable progress event emitted after a page reaches a terminal state."""

    event_type: str
    page_number: int
    detail: str = ""
    duration_ms: float = 0.0


@dataclass(frozen=True, slots=True)
class OCRBlock:
    id: str
    text: str
    confidence: Confidence
    bbox: BBox
    provenance: EngineRun | None = None


@dataclass(frozen=True, slots=True)
class OCRLine:
    id: str
    text: str
    confidence: Confidence
    bbox: BBox
    script: Script = Script.UNKNOWN
    region_type: RegionType = RegionType.UNKNOWN
    reading_order: int = 0
    blocks: Tuple[OCRBlock, ...] = field(default_factory=tuple)
    provenance: EngineRun | None = None
    # Additive with a default, so every existing construction site keeps
    # working and slots=True is preserved (ENHANCEMENT_PLAN A5).
    agreement: AgreementTier = AgreementTier.UNKNOWN


class ParagraphRole(str, Enum):
    BODY = "body"
    HEADING = "heading"
    SUBHEADING = "subheading"
    RUNNING_HEAD = "running_head"
    PAGE_NUMBER = "page_number"
    FOOTNOTE = "footnote"


@dataclass(frozen=True, slots=True)
class LineJoin:
    """How two consecutive lines were joined, recorded so it can be undone."""

    first_line_id: str
    second_line_id: str
    separator: str  # "" hyphen dropped | "-" hyphen kept | " " plain wrap
    removed: str  # the exact character removed from line one, "" if none
    verdict: str  # joined_in_lexicon | hyphen_in_lexicon | unverified


@dataclass(frozen=True, slots=True)
class OCRParagraph:
    id: str
    lines: Tuple[OCRLine, ...] = field(default_factory=tuple)
    text: str = ""
    role: ParagraphRole = ParagraphRole.BODY
    joins: Tuple[LineJoin, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DocumentPage:
    number: int
    width: int
    height: int
    lines: Tuple[OCRLine, ...] = field(default_factory=tuple)
    suggestions: Tuple[Suggestion, ...] = field(default_factory=tuple)
    failures: Tuple[PageFailure, ...] = field(default_factory=tuple)
    paragraphs: Tuple[OCRParagraph, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DocumentStructure:
    pages: Tuple[DocumentPage, ...] = field(default_factory=tuple)

    @classmethod
    def empty(cls) -> "DocumentStructure":
        return cls()


@dataclass(frozen=True, slots=True)
class TenantContext:
    organization_id: str
    user_id: str
    subscription_tier: str
    custom_model_id: str | None = None
