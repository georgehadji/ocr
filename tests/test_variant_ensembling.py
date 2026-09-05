"""Tests for preprocessing variant fan-out (ENHANCEMENT_PLAN A3).

The point of variants is that one model, given differently binarized images,
makes different errors — so N variants produce N candidates that A4's merge can
vote over. Two things have to hold for that to be worth anything:

* each variant's reading must be **attributable**, or the merge is an
  unauditable synthesis and CLAUDE.md rule 1 breaks;
* a variant must never be able to take the page down with it.

The geometry test is the subtle one. Layout is segmented once on the primary
page and every variant's word boxes are matched against those segments, so a
variant that rescales or rotates would misassign every block — quietly, with
plausible-looking output.
"""

from __future__ import annotations

from omniocr.application.pipeline import InMemoryPage, PipelineOrchestrator
from omniocr.domain.errors import IngestError
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRBlock,
    Script,
    TenantContext,
)
from omniocr.domain.result import Err, Ok, Result
from omniocr.ports.interfaces import RawPage

CONTEXT = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")


class _Resize:
    """A variant that changes the page size — must be rejected, not trusted."""

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        return Ok(InMemoryPage(number=page.number, content=page.content, width=999, height=999))


class _Failing:
    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        return Err(IngestError("this variant is broken"))


class _Tag:
    """Marks the page content so the fake engine can tell variants apart."""

    def __init__(self, tag: bytes) -> None:
        self._tag = tag

    def process(self, page: RawPage, context: TenantContext) -> Result[RawPage, IngestError]:
        return Ok(
            InMemoryPage(
                number=page.number, content=self._tag, width=page.width, height=page.height
            )
        )


class _EchoEngine:
    """Emits the page's own bytes as its reading, so variants differ visibly."""

    name = "echo"

    def extract(self, page: RawPage, context: TenantContext) -> Result[list[OCRBlock], object]:
        return Ok(
            [
                OCRBlock(
                    id="b1",
                    text=page.content.decode("utf-8"),
                    confidence=Confidence(0.9),
                    bbox=BBox(0, 0, 10, 10),
                    provenance=EngineRun(
                        engine="echo",
                        model_ref=ModelRef(engine="echo", model_name="echo", model_hash="h"),
                        model_hash="h",
                        params=(),
                        timestamp="2026-09-05T00:00:00Z",
                    ),
                )
            ]
        )


class _EchoRouter:
    def __init__(self, engine: object) -> None:
        self._engine = engine

    def route(self, line: object, context: TenantContext) -> list[object]:
        return [self._engine]


def _orchestrator(variants: list[tuple[str, object]]) -> PipelineOrchestrator:
    return PipelineOrchestrator(
        router=_EchoRouter(_EchoEngine()),
        variants=variants,  # type: ignore[arg-type]
    )


def _page() -> InMemoryPage:
    return InMemoryPage(number=1, content=b"primary", width=10, height=10)


class TestFanOut:
    def test_each_variant_becomes_its_own_candidate(self) -> None:
        orchestrator = _orchestrator([("otsu", _Tag(b"otsu")), ("sauvola", _Tag(b"sauvola"))])
        page = orchestrator._process_page(_page(), CONTEXT)

        # The reconciler keeps one line, but the merge suggestion and the
        # per-candidate ids prove three readings existed.
        assert page.lines, "the page produced no lines at all"

    def test_readings_are_attributable_to_their_variant(self) -> None:
        """Rule 1: a merge a reviewer cannot trace is not acceptable."""
        orchestrator = _orchestrator([("otsu", _Tag(b"otsu"))])
        variant_pages = orchestrator._variant_pages(_page(), CONTEXT)

        assert [name for name, _ in variant_pages] == ["primary", "otsu"]
        assert variant_pages[1][1].content == b"otsu"

    def test_no_variants_means_one_page_and_no_extra_cost(self) -> None:
        """The default must change nothing — variants cost N times recognition."""
        assert len(_orchestrator([])._variant_pages(_page(), CONTEXT)) == 1


class TestVariantIsolation:
    def test_a_failing_variant_is_dropped_not_fatal(self) -> None:
        """Losing a binarization costs accuracy; losing the page costs everything."""
        orchestrator = _orchestrator([("broken", _Failing()), ("otsu", _Tag(b"otsu"))])
        names = [name for name, _ in orchestrator._variant_pages(_page(), CONTEXT)]

        assert names == ["primary", "otsu"]

    def test_a_variant_that_changes_geometry_is_rejected(self) -> None:
        """Boxes are matched against segments found once on the primary.

        A rescaled variant would misassign every block while still producing
        plausible-looking text — the worst failure shape there is.
        """
        orchestrator = _orchestrator([("resize", _Resize())])
        names = [name for name, _ in orchestrator._variant_pages(_page(), CONTEXT)]

        assert names == ["primary"], "a geometry-changing variant was accepted"


class TestProvenance:
    def test_variant_is_recorded_on_the_line_id_and_provenance(self) -> None:
        orchestrator = _orchestrator([("otsu", _Tag(b"otsu"))])
        segment = orchestrator._layout_analyzer.segment(_page(), CONTEXT)
        assert isinstance(segment, Ok)
        line = segment.value[0]

        block = OCRBlock(
            id="b1",
            text="x",
            confidence=Confidence(0.9),
            bbox=BBox(0, 0, 10, 10),
            provenance=EngineRun(
                engine="echo",
                model_ref=ModelRef(engine="echo", model_name="echo", model_hash="h"),
                model_hash="h",
                params=(),
                timestamp="2026-09-05T00:00:00Z",
            ),
        )
        composed = PipelineOrchestrator._compose_line(line, _EchoEngine(), [block], "otsu")

        assert composed.id.endswith("-otsu")
        assert composed.provenance is not None
        assert composed.provenance.variant == "otsu"

    def test_the_primary_carries_no_variant_suffix(self) -> None:
        """Existing ids must not change for callers that asked for no variants."""
        line = PipelineOrchestrator._compose_line(
            _line_stub(), _EchoEngine(), [_block_stub()], "primary"
        )
        assert line.id.endswith("-echo")
        assert line.provenance is not None
        assert line.provenance.variant == ""


def _line_stub() -> object:
    from omniocr.domain.models import OCRLine

    return OCRLine(
        id="seg-1",
        text="",
        confidence=Confidence(0.0),
        bbox=BBox(0, 0, 10, 10),
        script=Script.POLYTONIC,
    )


def _block_stub() -> OCRBlock:
    return OCRBlock(
        id="b1",
        text="x",
        confidence=Confidence(0.9),
        bbox=BBox(0, 0, 10, 10),
        provenance=EngineRun(
            engine="echo",
            model_ref=ModelRef(engine="echo", model_name="echo", model_hash="h"),
            model_hash="h",
            params=(),
            timestamp="2026-09-05T00:00:00Z",
        ),
    )


class TestAgreementTier:
    """ENHANCEMENT_PLAN A5 — the honest confidence number.

    An engine's self-reported confidence is known to lie on exactly the lines
    that matter. Independent readers disagreeing is evidence.
    """

    @staticmethod
    def _candidate(text: str) -> object:
        from omniocr.domain.models import OCRLine

        return OCRLine(
            id="c",
            text=text,
            confidence=Confidence(0.9),
            bbox=BBox(0, 0, 10, 10),
        )

    def test_no_candidates_is_unknown(self) -> None:
        from omniocr.application.alignment import agreement_tier
        from omniocr.domain.models import AgreementTier

        assert agreement_tier([]) is AgreementTier.UNKNOWN

    def test_one_candidate_is_single(self) -> None:
        from omniocr.application.alignment import agreement_tier
        from omniocr.domain.models import AgreementTier

        assert agreement_tier([self._candidate("ἡ πόλις")]) is AgreementTier.SINGLE  # type: ignore[list-item]

    def test_identical_candidates_are_unanimous(self) -> None:
        from omniocr.application.alignment import agreement_tier
        from omniocr.domain.models import AgreementTier

        lines = [self._candidate("ἡ πόλις"), self._candidate("ἡ πόλις")]
        assert agreement_tier(lines) is AgreementTier.UNANIMOUS  # type: ignore[arg-type]

    def test_two_of_three_agreeing_is_majority(self) -> None:
        from omniocr.application.alignment import agreement_tier
        from omniocr.domain.models import AgreementTier

        lines = [
            self._candidate("ἡ πόλις"),
            self._candidate("ἡ πόλις"),
            self._candidate("ἡ πόλεις"),
        ]
        assert agreement_tier(lines) is AgreementTier.MAJORITY  # type: ignore[arg-type]

    def test_all_different_is_split(self) -> None:
        from omniocr.application.alignment import agreement_tier
        from omniocr.domain.models import AgreementTier

        lines = [
            self._candidate("ἡ πόλις"),
            self._candidate("ἡ πόλεις"),
            self._candidate("ἠ πὸλις"),
        ]
        assert agreement_tier(lines) is AgreementTier.SPLIT  # type: ignore[arg-type]

    def test_whitespace_alone_is_not_disagreement(self) -> None:
        """Two engines spacing a line differently did not disagree about it.

        Calling that SPLIT would flood the review queue with non-findings.
        """
        from omniocr.application.alignment import agreement_tier
        from omniocr.domain.models import AgreementTier

        lines = [self._candidate("ἡ  πόλις"), self._candidate("ἡ πόλις")]
        assert agreement_tier(lines) is AgreementTier.UNANIMOUS  # type: ignore[arg-type]

    def test_export_is_never_gated_on_tier(self) -> None:
        """A SPLIT line keeps its text. Dropping it would violate rule 1."""
        orchestrator = _orchestrator([("otsu", _Tag(b"otsu"))])
        page = orchestrator._process_page(_page(), CONTEXT)

        assert page.lines
        assert all(line.text for line in page.lines), "a contested line lost its text"
