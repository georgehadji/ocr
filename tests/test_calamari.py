"""CalamariEngine: a line recognizer run as a GPLv3-isolated subprocess.

Unit tests replace ``subprocess.run`` with a fake that writes the same
``<line>.json`` files ``calamari-predict`` 2.x writes, so they run without
TensorFlow. ``test_real_calamari_*`` runs the real binary and is skipped unless
``calamari-predict`` is on PATH and ``OMNIOCR_CALAMARI_MODEL`` names a
checkpoint glob (the CI image has neither).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import pytest
from PIL import Image

from omniocr.domain.errors import LayoutError
from omniocr.domain.models import BBox, Confidence, OCRLine, Script, TenantContext
from omniocr.domain.result import Err, Ok, Result
from omniocr.infrastructure import calamari
from omniocr.infrastructure.calamari import CalamariEngine

CONTEXT = TenantContext(organization_id="t", user_id="t", subscription_tier="desktop")


@dataclass(frozen=True)
class Page:
    number: int
    content: bytes
    width: int
    height: int


def _page(width: int = 300, height: int = 120) -> Page:
    buffer = io.BytesIO()
    Image.new("L", (width, height), 255).save(buffer, format="PNG")
    return Page(1, buffer.getvalue(), width, height)


def _line(index: int, bbox: BBox) -> OCRLine:
    return OCRLine(f"l{index}", "", Confidence(0.0), bbox, Script.POLYTONIC)


class Lines:
    """Layout analyzer returning canned boxes (or an error)."""

    def __init__(self, result: Result[Sequence[OCRLine], LayoutError]) -> None:
        self._result = result

    def segment(self, page: Any, context: TenantContext) -> Result[Sequence[OCRLine], LayoutError]:
        return self._result


TWO_LINES = Lines(
    Ok((_line(0, BBox(10, 10, 200, 30)), _line(1, BBox(10, 60, 200, 30)))),
)


def _checkpoints(tmp_path: Path, count: int = 2) -> str:
    for index in range(count):
        (tmp_path / f"{index}.ckpt.h5").write_bytes(b"")
        (tmp_path / f"{index}.ckpt.json").write_text("{}")
    return str(tmp_path / "*.ckpt.h5")


def _fake_predict(
    monkeypatch: pytest.MonkeyPatch,
    predictions: dict[str, list[dict[str, Any]]],
    *,
    returncode: int = 0,
    stderr: str = "",
) -> list[list[str]]:
    """Stand in for ``calamari-predict``; returns the argv of every call."""
    calls: list[list[str]] = []

    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if returncode == 0:
            out = Path(kwargs["cwd"]) / cmd[cmd.index("--output_dir") + 1]
            start = cmd.index("--data.images") + 1
            for name in cmd[start:]:
                if name.startswith("--"):
                    break
                stem = Path(name).stem
                if stem in predictions:
                    (out / f"{stem}.json").write_text(
                        json.dumps({"predictions": predictions[stem]}), encoding="utf-8"
                    )
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    monkeypatch.setattr(calamari.subprocess, "run", run)
    return calls


def test_lines_are_cropped_predicted_once_and_boxed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_predict(
        monkeypatch,
        {
            "line_0000": [{"id": "0", "sentence": "ὁ λόγος", "avg_char_probability": 0.9}],
            "line_0001": [{"id": "0", "sentence": "τοῦ θεοῦ", "avg_char_probability": 0.5}],
        },
    )

    result = CalamariEngine(_checkpoints(tmp_path, 1), TWO_LINES).extract(_page(), CONTEXT)

    assert isinstance(result, Ok)
    first, second = result.value
    assert (first.text, second.text) == ("ὁ λόγος", "τοῦ θεοῦ")
    assert (first.bbox, second.bbox) == (BBox(10, 10, 200, 30), BBox(10, 60, 200, 30))
    # Calamari reports 0-1; the other engines report 0-100.
    assert (first.confidence.value, second.confidence.value) == (90.0, 50.0)
    assert first.provenance is not None and first.provenance.engine == "calamari"
    assert len(calls) == 1, "one process per page, not one per line"
    assert calls[0][0] == "calamari-predict"
    assert "--files" not in calls[0], "Calamari 2.x rejects --files"


def test_voted_prediction_wins_over_single_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_predict(
        monkeypatch,
        {
            "line_0000": [
                {"id": "0", "sentence": "wrong", "avg_char_probability": 0.4},
                {"id": "voted", "sentence": "ὁ λόγος", "avg_char_probability": 0.8},
            ],
        },
    )

    result = CalamariEngine(_checkpoints(tmp_path), TWO_LINES).extract(_page(), CONTEXT)

    assert isinstance(result, Ok)
    assert [block.text for block in result.value] == ["ὁ λόγος"]


def test_checkpoint_glob_becomes_extensionless_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _fake_predict(monkeypatch, {})

    CalamariEngine(_checkpoints(tmp_path, 2), TWO_LINES).extract(_page(), CONTEXT)

    cmd = calls[0]
    given = cmd[cmd.index("--checkpoint") + 1 : cmd.index("--data.images")]
    assert given == [str(tmp_path / "0.ckpt"), str(tmp_path / "1.ckpt")]


def test_checkpoints_still_resolve_after_calamari_migrates_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calamari renames ``N.ckpt.h5`` to ``N.ckpt.h5_v3`` on first load."""
    (tmp_path / "0.ckpt.h5_v3").write_bytes(b"")
    (tmp_path / "0.ckpt.json").write_text("{}")
    calls = _fake_predict(monkeypatch, {})

    CalamariEngine(str(tmp_path / "*.ckpt.h5"), TWO_LINES).extract(_page(), CONTEXT)

    assert calls[0][calls[0].index("--checkpoint") + 1] == str(tmp_path / "0.ckpt")


def test_text_is_nfc_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    decomposed = unicodedata.normalize("NFD", "ἢ")
    _fake_predict(
        monkeypatch,
        {"line_0000": [{"id": "0", "sentence": decomposed, "avg_char_probability": 1.0}]},
    )

    result = CalamariEngine(_checkpoints(tmp_path), TWO_LINES).extract(_page(), CONTEXT)

    assert isinstance(result, Ok)
    assert result.value[0].text == "ἢ"


def test_blank_predictions_yield_no_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_predict(
        monkeypatch,
        {"line_0000": [{"id": "0", "sentence": "  ", "avg_char_probability": 0.9}]},
    )

    result = CalamariEngine(_checkpoints(tmp_path), TWO_LINES).extract(_page(), CONTEXT)

    assert result == Ok(())


def test_a_box_outside_the_page_is_skipped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_predict(monkeypatch, {})
    outside = Lines(Ok((_line(0, BBox(900, 900, 50, 20)),)))

    result = CalamariEngine(_checkpoints(tmp_path), outside).extract(_page(), CONTEXT)

    assert result == Ok(())
    assert calls == [], "nothing to recognize, so no process"


def test_a_page_with_no_lines_is_empty_not_an_error(tmp_path: Path) -> None:
    empty = Lines(Ok(()))

    assert CalamariEngine(_checkpoints(tmp_path), empty).extract(_page(), CONTEXT) == Ok(())


@pytest.mark.parametrize(
    ("engine", "message"),
    [
        (CalamariEngine("no/such/*.ckpt.h5", TWO_LINES), "no Calamari checkpoint"),
        (CalamariEngine("*.ckpt.h5", None), "needs a layout analyzer"),
    ],
)
def test_misconfiguration_is_an_error(engine: CalamariEngine, message: str) -> None:
    result = engine.extract(_page(), CONTEXT)

    assert isinstance(result, Err)
    assert message in str(result.error)


def test_segmentation_failure_is_an_error(tmp_path: Path) -> None:
    failing = Lines(Err(LayoutError("boom")))

    result = CalamariEngine(_checkpoints(tmp_path), failing).extract(_page(), CONTEXT)

    assert isinstance(result, Err)
    assert "boom" in str(result.error)


def test_nonzero_exit_reports_stderr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_predict(monkeypatch, {}, returncode=2, stderr="UnknownArgumentError: --files")

    result = CalamariEngine(_checkpoints(tmp_path), TWO_LINES).extract(_page(), CONTEXT)

    assert isinstance(result, Err)
    assert "exit 2" in str(result.error)
    assert "UnknownArgumentError" in str(result.error)


def test_missing_binary_gives_an_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(cmd[0])

    monkeypatch.setattr(calamari.subprocess, "run", run)

    result = CalamariEngine(_checkpoints(tmp_path), TWO_LINES).extract(_page(), CONTEXT)

    assert isinstance(result, Err)
    assert "pip install omniocr[calamari]" in str(result.error)


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def test_ensemble_adds_calamari_only_when_a_model_is_given() -> None:
    from omniocr.composition.desktop import create_ensemble_pipeline

    def names(pipeline: object) -> set[str]:
        by_script = pipeline._router._by_script  # type: ignore[attr-defined]
        return {engine.name for engines in by_script.values() for engine in engines}

    without = create_ensemble_pipeline(tesseract_language="grc", kraken_model_path="x.mlmodel")
    with_model = create_ensemble_pipeline(
        tesseract_language="grc",
        kraken_model_path="x.mlmodel",
        calamari_model_glob="models/*.ckpt.h5",
    )

    assert "calamari" not in names(without)
    assert "calamari" in names(with_model)


def test_calamari_gets_the_pipelines_own_line_segmenter() -> None:
    from omniocr.application.layout import FallbackLayoutAnalyzer
    from omniocr.composition.desktop import create_ensemble_pipeline
    from omniocr.infrastructure.resilience import RetryingEngine

    pipeline = create_ensemble_pipeline(
        tesseract_language="grc",
        kraken_model_path="x.mlmodel",
        calamari_model_glob="models/*.ckpt.h5",
    )
    by_script = pipeline._router._by_script  # type: ignore[attr-defined]
    [engine] = [e for e in by_script[Script.POLYTONIC] if e.name == "calamari"]

    assert isinstance(engine, RetryingEngine)
    assert isinstance(engine.engine, CalamariEngine)
    assert isinstance(engine.engine._layout_analyzer, FallbackLayoutAnalyzer)
    assert engine.engine._layout_analyzer is pipeline._layout_analyzer  # type: ignore[attr-defined]


def test_cli_passes_the_model_to_the_ensemble(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    from omniocr.composition import desktop
    from omniocr.interfaces import cli

    seen: dict[str, Any] = {}

    def fake(**kwargs: Any) -> object:
        seen.update(kwargs)
        return object()

    monkeypatch.setattr(desktop, "create_ensemble_pipeline", fake)
    monkeypatch.setattr(cli, "_resolve_model", lambda model: "x.mlmodel")
    args = argparse.Namespace(
        engine="ensemble",
        script="polytonic",
        lang="grc",
        model=None,
        structure=False,
        workers=1,
        calamari_model="models/*.ckpt.h5",
    )

    cli._build_pipeline(args, exporter=object())  # type: ignore[arg-type]

    assert seen["calamari_model_glob"] == "models/*.ckpt.h5"


def test_cli_rejects_calamari_with_tesseract(tmp_path: Path) -> None:
    from omniocr.interfaces import cli

    source = tmp_path / "page.png"
    source.write_bytes(b"")

    code = cli.main(["run", str(source), "--engine", "tesseract", "--calamari-model", "m"])

    assert code == cli.EXIT_USAGE


@pytest.mark.parametrize("script", ["modern", "mixed", "unknown", "pontian"])
def test_cli_rejects_calamari_for_scripts_it_never_reads(tmp_path: Path, script: str) -> None:
    from omniocr.interfaces import cli

    source = tmp_path / "page.png"
    source.write_bytes(b"")

    code = cli.main(["run", str(source), "--script", script, "--calamari-model", "m"])

    assert code == cli.EXIT_USAGE


# --------------------------------------------------------------------------
# the real binary
# --------------------------------------------------------------------------

_REAL_MODEL = os.environ.get("OMNIOCR_CALAMARI_MODEL")
_CORPUS = Path(__file__).parent / "corpus"


@pytest.mark.skipif(
    shutil.which("calamari-predict") is None or not _REAL_MODEL,
    reason="needs calamari-predict on PATH and OMNIOCR_CALAMARI_MODEL=<checkpoint glob>",
)
def test_real_calamari_reads_a_line_cropped_from_a_fixture() -> None:
    """The adapter's arguments and the JSON it parses match the installed Calamari."""
    assert _REAL_MODEL is not None
    page_path = _CORPUS / "ancient-1.png"
    content = page_path.read_bytes()
    width, height = Image.open(page_path).size
    one_line = Lines(Ok((_line(0, BBox(0, 0, width, min(height, 80))),)))

    result = CalamariEngine(_REAL_MODEL, one_line).extract(Page(1, content, width, height), CONTEXT)

    assert isinstance(result, Ok), result
    [block] = result.value
    assert block.text.strip()
    assert 0.0 <= block.confidence.value <= 100.0
    assert block.bbox == BBox(0, 0, width, min(height, 80))
