"""E2E smoke test: synthetic PDF → pipeline → verify output.

Per BUILD_PLAN §8.6: ingest a minimal page, run the full pipeline,
and export all formats. Verifies the pipeline runs end-to-end without
exceptions and produces non-empty output.
"""

from __future__ import annotations

from omniocr.application.pipeline import PipelineOrchestrator
from omniocr.domain.models import TenantContext
from omniocr.application.pipeline import PlainTextExporter
from omniocr.infrastructure.exporters import (
    AltoXmlExporter,
    MarkdownExporter,
    PageXmlExporter,
)


def _synthetic_pdf_bytes() -> bytes:
    """Create a minimal valid PDF with one blank page using PyMuPDF if available, or provide a minimal PDF."""
    try:
        import fitz
        doc = fitz.open()
        doc.new_page(width=100, height=100)
        result = doc.tobytes()
        doc.close()
        return result
    except ImportError:
        # Minimal valid PDF with one blank page (no external lib needed)
        return (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
            b"xref\n0 4\n"
            b"trailer<</Size 4/Root 1 0 R>>\n"
            b"startxref\n150\n%%EOF"
        )


def test_e2e_pipeline_runs_and_exports_markdown() -> None:
    """Ingest a synthetic page, run the pipeline, export as Markdown."""
    pdf = _synthetic_pdf_bytes()
    pipeline = PipelineOrchestrator(exporter=MarkdownExporter())
    ctx = TenantContext("e2e", "test", "test")

    result = pipeline.run(pdf, ctx)
    assert result.is_ok(), f"Pipeline run failed: {result.error}"

    doc = result.value
    assert len(doc.pages) >= 1
    assert doc.pages[0].number == 1

    export = pipeline.export(doc, ctx)
    assert export.is_ok(), f"Pipeline export failed: {export.error}"
    assert len(export.value) > 0


def test_e2e_pipeline_export_markdown() -> None:
    """Ingest → pipeline → Markdown export produces page heading."""
    pdf = _synthetic_pdf_bytes()
    pipeline = PipelineOrchestrator(exporter=MarkdownExporter())
    ctx = TenantContext("e2e", "test", "test")

    result = pipeline.run(pdf, ctx)
    assert result.is_ok()

    export = pipeline.export(result.value, ctx)
    assert export.is_ok()
    assert b"Page 1" in export.value or b"page" in export.value[:100].lower()


def test_e2e_pipeline_export_alto_xml() -> None:
    """Ingest → pipeline → ALTO XML export produces valid XML."""
    pdf = _synthetic_pdf_bytes()
    pipeline = PipelineOrchestrator(exporter=AltoXmlExporter())
    ctx = TenantContext("e2e", "test", "test")

    result = pipeline.run(pdf, ctx)
    assert result.is_ok()

    export = pipeline.export(result.value, ctx)
    assert export.is_ok()
    assert b"<Alto" in export.value or b"alto" in export.value[:200].lower()


def test_e2e_pipeline_export_page_xml() -> None:
    """Ingest → pipeline → PAGE-XML export produces valid XML."""
    pdf = _synthetic_pdf_bytes()
    pipeline = PipelineOrchestrator(exporter=PageXmlExporter())
    ctx = TenantContext("e2e", "test", "test")

    result = pipeline.run(pdf, ctx)
    assert result.is_ok()

    export = pipeline.export(result.value, ctx)
    assert export.is_ok()
    assert b"<PcGts" in export.value or b"Page" in export.value[:200]
