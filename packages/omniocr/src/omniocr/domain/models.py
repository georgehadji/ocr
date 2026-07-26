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
    blocks: Tuple[OCRBlock, ...] = field(default_factory=tuple)
    provenance: EngineRun | None = None


@dataclass(frozen=True, slots=True)
class OCRParagraph:
    id: str
    lines: Tuple[OCRLine, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DocumentPage:
    number: int
    width: int
    height: int
    lines: Tuple[OCRLine, ...] = field(default_factory=tuple)
    suggestions: Tuple[Suggestion, ...] = field(default_factory=tuple)
    failures: Tuple[PageFailure, ...] = field(default_factory=tuple)


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
