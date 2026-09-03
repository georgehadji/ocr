from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class Suggestion:
    line_id: str
    source_text: str
    suggestion_text: str
    reason: str
    reversible: bool = True


@dataclass(frozen=True, slots=True)
class PageFailure:
    """A page-local failure retained for review without aborting the job."""

    error_type: str
    message: str


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
