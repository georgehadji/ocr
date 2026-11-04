"""Evaluation counts misses, and training compares real engines (audit PR4)."""

from __future__ import annotations

from pathlib import Path

from omniocr.application.evaluation import evaluate
from omniocr.application.ground_truth import SampleSpecification, assemble_training_samples
from omniocr.application.promotion import BeatsParentOnHeldOut
from omniocr.application.training_orchestrator import TrainingOrchestrator
from omniocr.composition.training import _CorpusEvaluator
from omniocr.domain.corpus import SplitName
from omniocr.domain.corrections import Correction
from omniocr.domain.errors import TrainingError
from omniocr.domain.models import BBox, Confidence, ModelRef, OCRBlock, Script
from omniocr.domain.result import Err, Ok
from omniocr.domain.training import ModelCandidate
from omniocr.infrastructure.corpus_repository import FileCorpusRepository
from omniocr.infrastructure.mlflow_registry import InMemoryModelRegistry

TEXT = "γειά σου"


def _block(text: str) -> OCRBlock:
    return OCRBlock("b", text, Confidence(90), BBox(0, 0, 10, 10))


def _corpus(root: Path, pages: int) -> FileCorpusRepository:
    split = root / "test"
    split.mkdir(parents=True)
    for number in range(1, pages + 1):
        (split / f"modern-{number}.png").write_bytes(f"page-{number}".encode())
        (split / f"modern-{number}.txt").write_text(TEXT, encoding="utf-8")
    return FileCorpusRepository(root)


# --- [10] an empty output is an error, not a missing sample ---------------


def test_a_model_that_reads_one_page_of_ten_scores_cer_0_9(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus", 10)

    class ReadsPageOne:
        name = "reads-page-one"

        def extract(self, page, context):
            return Ok((_block(TEXT),) if page.content == b"page-1" else ())

    result = evaluate(ReadsPageOne(), corpus.pages(SplitName.TEST), SplitName.TEST)

    assert result.is_ok()
    assert abs(result.value.per_script_cer[Script.MODERN] - 0.9) < 1e-9
    assert abs(result.value.per_script_wer[Script.MODERN] - 0.9) < 1e-9


# --- [11] the orchestrator evaluates the candidate and its parent ---------


class _Store:
    def __init__(self, corrections: list[Correction]) -> None:
        self._corrections = corrections

    def all_accepted(self):
        return self._corrections


class _Cropper:
    def crop(self, page_image, bbox):
        return Ok(b"crop")


class _Exporter:
    def export(self, samples, out):
        out.mkdir(parents=True, exist_ok=True)
        return Ok(out)


class _Trainer:
    def __init__(self, directory: Path) -> None:
        self._directory = directory

    def train(self, data, parent, params):
        path = self._directory / "candidate.mlmodel"
        path.write_bytes(b"weights")
        return Ok(ModelCandidate(path, "c" * 64, parent, "run-1"))


def _correction() -> Correction:
    return Correction(
        line_id="p1-line-1",
        page_number=1,
        original_text="x",
        corrected_text=TEXT,
        corrected_by="tester",
        corrected_at="2026-11-04T12:00:00",
        bbox=BBox(0, 0, 100, 20),
        script=Script.MODERN,
    )


class _EngineOn:
    """Reads every corpus page correctly (``good``) or not at all."""

    def __init__(self, path: Path, good: bool) -> None:
        self.name = f"engine:{path.name}"
        self._good = good

    def extract(self, page, context):
        return Ok((_block(TEXT),) if self._good else ())


def _orchestrator(tmp_path: Path, candidate_is_good: bool, calls: list[Path], evaluator):
    def factory(path: Path):
        calls.append(path)
        return _EngineOn(path, good=candidate_is_good and path.name == "candidate.mlmodel")

    return TrainingOrchestrator(
        correction_store=_Store([_correction()]),
        cropper=_Cropper(),
        exporter=_Exporter(),
        trainer=_Trainer(tmp_path),
        evaluator=evaluator,
        promotion_policy=BeatsParentOnHeldOut(min_improvement_pct=1.0, min_sample_count=1),
        model_registry=InMemoryModelRegistry(),
        engine_factory=factory,
    )


def _run(orchestrator: TrainingOrchestrator, tmp_path: Path):
    parent = ModelRef("kraken", str(tmp_path / "parent.mlmodel"), "p" * 64)
    return orchestrator.run_training(parent, {1: b"page"}, tmp_path / "out")


def test_training_runs_end_to_end_and_promotes_a_candidate_that_beats_its_parent(
    tmp_path: Path,
) -> None:
    calls: list[Path] = []
    evaluator = _CorpusEvaluator(_corpus(tmp_path / "corpus", 2))

    result = _run(_orchestrator(tmp_path, True, calls, evaluator), tmp_path)

    assert result.is_ok() and result.value is not None
    assert result.value.checkpoint == tmp_path / "candidate.mlmodel"
    # Both the candidate and the parent were built from their own weights.
    assert calls == [tmp_path / "candidate.mlmodel", tmp_path / "parent.mlmodel"]


def test_a_candidate_that_does_not_beat_its_parent_is_not_promoted(tmp_path: Path) -> None:
    evaluator = _CorpusEvaluator(_corpus(tmp_path / "corpus", 2))

    result = _run(_orchestrator(tmp_path, False, [], evaluator), tmp_path)

    assert result.is_ok() and result.value is None


def test_without_an_evaluation_corpus_training_refuses_to_start(tmp_path: Path) -> None:
    result = _run(_orchestrator(tmp_path, True, [], None), tmp_path)

    assert isinstance(result, Err)
    assert isinstance(result.error, TrainingError)
    assert "no evaluation corpus" in str(result.error)


# --- a crop under the minimum height is skipped ---------------------------


def test_a_line_shorter_than_the_minimum_height_is_not_a_training_sample(tmp_path: Path) -> None:
    from dataclasses import replace

    tall = _correction()
    short = replace(tall, line_id="p1-line-2", bbox=BBox(0, 30, 100, 3))

    result = assemble_training_samples(
        [tall, short], _Cropper(), {1: b"page"}, tmp_path, SampleSpecification(min_line_height=8)
    )

    assert isinstance(result, Ok)
    assert [s.image_path.name for s in result.value] == ["line-p1-line-1.png"]
