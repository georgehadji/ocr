from __future__ import annotations

import pytest

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.application.structure import IdentityAssembler
from omniocr.domain.models import (
    BBox,
    Confidence,
    DocumentPage,
    DocumentStructure,
    OCRLine,
    TenantContext,
)
from omniocr.domain.result import Ok
from omniocr.interfaces import cli


def test_identity_assembler_returns_unmodified_document() -> None:
    # Arrange
    line = OCRLine(
        id="line-1",
        text="Θουκυδίδης Ἀθηναῖος ξυνέγραψε τὸν πόλεμον",
        confidence=Confidence(95.0),
        bbox=BBox(10, 20, 100, 30),
    )
    page = DocumentPage(number=1, width=150, height=200, lines=(line,))
    doc = DocumentStructure(pages=(page,))
    context = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")

    assembler = IdentityAssembler()

    # Act
    result = assembler.assemble(doc, context)

    # Assert
    assert isinstance(result, Ok)
    assert result.value == doc


def test_pipeline_orchestrator_defaults_to_identity_assembler() -> None:
    # Arrange
    orchestrator = PipelineOrchestrator()

    # Assert
    assert isinstance(orchestrator._assembler, IdentityAssembler)


def test_cli_parser_accepts_structure_flag() -> None:
    # Arrange
    parser = cli.build_parser()

    # Act
    args = parser.parse_args(["run", "dummy.pdf", "--structure"])

    # Assert
    assert args.structure is True
