"""Headless command-line entry point.

Designed for unattended callers (agents, CI, batch scripts):

- never prompts, never opens a UI, never writes to stdin
- ``--json`` emits one machine-readable object on **stdout**; all progress
  and diagnostics go to **stderr**, so ``omniocr ... --json | jq`` is safe
- exit codes are stable and meaningful (see ``ExitCode``)

    omniocr doctor --json
    omniocr run book.pdf --out book.docx --engine tesseract --lang ell
    omniocr run scan.png --out page.txt --max-pages 5

This module is a composition root: it parses arguments, wires adapters via
``omniocr.composition.desktop``, and formats output. It contains no
recognition, correction, or export logic.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, used only to query tesseract
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from omniocr.domain.corpus import SplitName
from omniocr.infrastructure.config import DEFAULT_TESSERACT_LANGUAGE
from omniocr.infrastructure.model_manifest import MANIFEST_PATH, MODELS_DIR
from omniocr.domain.models import DocumentPage, DocumentStructure, Script, TenantContext
from omniocr.domain.result import Err
from omniocr.ports.interfaces import IExporter

if TYPE_CHECKING:
    from omniocr.application.metrics import RegressionBaseline

# Exit codes. Stable contract for scripted callers — do not renumber.
EXIT_OK = 0
EXIT_FAILURE = 1  # pipeline or export failed
EXIT_USAGE = 2  # argparse default for bad arguments
EXIT_ENVIRONMENT = 3  # a required engine/model/dependency is missing

# Re-exported from the module that owns the layout, not redefined here.
_MODELS_DIR = MODELS_DIR
_OUTPUT_DIR = Path("Outputs")  # default destination when --out is omitted


def _emit_json(payload: dict[str, Any]) -> None:
    """Write one JSON object to stdout as UTF-8, whatever the console codepage.

    ``print()`` encodes through ``sys.stdout``, which on Windows is the active
    console codepage — cp1253 on a Greek install, the exact machine this
    project targets. A payload containing a Greek filename then reached the
    caller as bytes that are not valid UTF-8, so ``json.loads`` on the other
    end failed outright. Writing to the underlying buffer keeps the documented
    contract true regardless of locale.
    """
    encoded = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # captured stdout in tests has no buffer
        sys.stdout.write(encoded.decode("utf-8") + "\n")
        return
    buffer.write(encoded + b"\n")
    buffer.flush()


# Output format per --out extension. --format overrides.
_FORMAT_BY_SUFFIX: dict[str, str] = {
    ".txt": "txt",
    ".md": "md",
    ".markdown": "md",
    ".docx": "docx",
    ".pdf": "pdf",
    ".xml": "alto",
}
# Reverse of the above, plus "page" (which has no unique suffix above since
# both ALTO and PAGE-XML are ".xml") — used only to name a *default* path.
_SUFFIX_BY_FORMAT: dict[str, str] = {
    "txt": ".txt",
    "md": ".md",
    "docx": ".docx",
    "pdf": ".pdf",
    "alto": ".xml",
    "page": ".xml",
}


# --------------------------------------------------------------------------
# environment probing (shared by `doctor` and `run` preflight)
# --------------------------------------------------------------------------


def _tesseract_report() -> dict[str, Any]:
    binary = shutil.which("tesseract")
    if binary is None:
        return {"available": False, "path": None, "languages": []}
    try:
        completed = subprocess.run(  # nosec B603 - argv list, no shell, resolved path
            [binary, "--list-langs"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        languages = sorted(
            line.strip()
            for line in completed.stdout.splitlines()
            if line.strip() and " " not in line.strip()
        )
    except (OSError, subprocess.SubprocessError):
        languages = []
    return {"available": True, "path": binary, "languages": languages}


def _kraken_report(models_dir: Path = _MODELS_DIR) -> dict[str, Any]:
    try:
        import kraken  # noqa: F401

        installed = True
    except ImportError:
        installed = False
    models = sorted(str(path) for path in models_dir.glob("*.mlmodel"))
    report: dict[str, Any] = {"installed": installed, "models": models}
    return report


def _torch_stack_report() -> dict[str, Any]:
    """Report whether torch and torchvision are ABI-compatible with each other.

    ``import kraken`` succeeds even when the torch stack is incoherent, because
    kraken defers its torch imports. So ``doctor`` reported a healthy Kraken
    while every page failed with ``operator torchvision::nms does not exist`` —
    a message that names neither the cause nor the fix.

    The usual cause is installing one of the pair from a ``--index-url`` while
    leaving the other alone. Each CUDA index carries its own ceiling: cu124
    tops out at torch 2.6 / torchvision 0.21, so ``pip install --force-reinstall
    torch --index-url .../cu124`` silently *downgrades* torch and strands a
    torchvision built for a newer one. They must be installed together, from
    one index.

    Probing ``torchvision.ops`` rather than the bare import is deliberate: the
    module imports fine and only fails when its compiled extension is first
    touched, which is what makes the real failure land mid-run.
    """
    try:
        import torch
    except ImportError:
        return {"installed": False}
    except Exception as exc:  # pragma: no cover - depends on a broken install
        # A partially installed or ABI-broken torch raises OSError from the
        # import itself (WinError 126: a missing DLL in torch/lib), not
        # ImportError. Catching only ImportError made `doctor` crash on the
        # exact condition it exists to report, which is worse than the bug.
        return {"installed": True, "torch": None, "coherent": False, "error": str(exc)}

    report: dict[str, Any] = {"installed": True, "torch": str(torch.__version__)}
    try:
        import torchvision

        getattr(torchvision.ops, "nms")  # noqa: B009 - forces the C++ extension to load
        report["torchvision"] = str(torchvision.__version__)
        report["coherent"] = True
    except Exception as exc:  # pragma: no cover - depends on a broken install
        report["torchvision"] = None
        report["coherent"] = False
        report["error"] = str(exc)
    return report


def _device_report() -> dict[str, Any]:
    """Report the compute device up front.

    It is the difference between minutes and seconds per page, the caller
    sizes its timeouts on it, and it governs fine-tuning as well as
    recognition — so it is a top-level fact, not a Kraken sub-field.
    """
    from omniocr.infrastructure.device import device_note, select_device

    report: dict[str, Any] = {"selected": select_device()}
    note = device_note()
    if note is not None:
        report["note"] = note
    return report


def _optional_module_report() -> dict[str, bool]:
    """Report which optional extras are importable."""
    names = {"pdf": "fitz", "docx": "docx", "opencv": "cv2", "pillow": "PIL"}
    report: dict[str, bool] = {}
    for extra, module in names.items():
        try:
            __import__(module)
            report[extra] = True
        except ImportError:
            report[extra] = False
    return report


def _environment() -> dict[str, Any]:
    return {
        "tesseract": _tesseract_report(),
        "kraken": _kraken_report(),
        "torch_stack": _torch_stack_report(),
        "device": _device_report(),
        "extras": _optional_module_report(),
    }


def _environment_problems(env: dict[str, Any]) -> list[str]:
    """Return human-readable blockers. Empty list means the install is usable."""
    problems: list[str] = []
    tess = env["tesseract"]
    if not tess["available"]:
        problems.append("tesseract binary not found on PATH")
    else:
        missing = sorted({"ell", "grc"} - set(tess["languages"]))
        if missing:
            problems.append(f"tesseract language packs missing: {', '.join(missing)}")
    if not env["kraken"]["installed"]:
        problems.append("kraken not installed (pip install 'omniocr[kraken]')")
    elif not env["kraken"]["models"]:
        problems.append(f"no Kraken .mlmodel found in {_MODELS_DIR}/ — see models/README.md")
    stack = env["torch_stack"]
    if stack["installed"] and not stack.get("coherent", True):
        problems.append(
            f"torch {stack['torch']} and torchvision are ABI-incompatible "
            f"({stack.get('error', 'unknown error')}) — Kraken cannot run. "
            "Reinstall both from one index, e.g. pip install --force-reinstall "
            "torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu"
        )
    if not env["extras"]["pdf"]:
        problems.append(
            "PyMuPDF not installed (pip install 'omniocr[pdf]') — PDF input unavailable"
        )
    return problems


# --------------------------------------------------------------------------
# wiring
# --------------------------------------------------------------------------


def _build_exporter(fmt: str, source_bytes: bytes) -> IExporter:
    """Return the exporter for a format key. Imported lazily: optional extras."""
    from omniocr.infrastructure import exporters

    if fmt == "txt":
        return exporters.PlainTextExporter()
    if fmt == "md":
        return exporters.MarkdownExporter()
    if fmt == "docx":
        return exporters.DocxExporter()
    if fmt == "alto":
        return exporters.AltoXmlExporter()
    if fmt == "page":
        return exporters.PageXmlExporter()
    if fmt == "pdf":
        # Overlays the invisible text layer on the original page images, so it
        # needs the source document, not just the recognized structure.
        return exporters.SearchablePdfExporter(source_pdf=source_bytes)
    raise ValueError(f"unsupported output format: {fmt}")


def _resolve_format(out: Path, override: str | None) -> str:
    if override is not None:
        return override
    fmt = _FORMAT_BY_SUFFIX.get(out.suffix.lower())
    if fmt is None:
        raise ValueError(
            f"cannot infer output format from '{out.suffix}' — pass --format "
            f"({', '.join(sorted(set(_FORMAT_BY_SUFFIX.values()) | {'page'}))})"
        )
    return fmt


def _default_output_path(source: Path, fmt: str) -> Path:
    """``Outputs/<input-stem>.<ext>`` — used whenever the caller omits --out."""
    return _OUTPUT_DIR / f"{source.stem}{_SUFFIX_BY_FORMAT.get(fmt, f'.{fmt}')}"


def _resolve_model(explicit: str | None) -> str:
    """Choose the Kraken model: explicit, else the manifest default.

    Selection used to be ``sorted(glob("*.mlmodel"))[0]`` — alphabetical, so
    the choice was decided by a filename. That is not a detail. Measured on
    page 31 of the target document, the three bundled models score 0.038,
    0.147 and 0.264 CER, and the alphabetically first one is the 0.264 —
    4.7x worse than Tesseract on the same page, while the best is 1.5x
    better. The manifest now says which one to use and why.
    """
    if explicit is not None:
        return explicit
    candidates = sorted(_MODELS_DIR.glob("*.mlmodel"))
    if not candidates:
        raise FileNotFoundError(
            f"no Kraken model found in {_MODELS_DIR}/ — pass --model or see models/README.md"
        )

    from omniocr.infrastructure.model_manifest import ModelManifest

    preferred = ModelManifest(MANIFEST_PATH).default_for("kraken")
    if preferred is not None:
        chosen = _MODELS_DIR / preferred.name
        if chosen.is_file():
            return str(chosen)
        print(
            f"manifest default {preferred.name} is missing from {_MODELS_DIR}/ — "
            f"falling back to {candidates[0].name}",
            file=sys.stderr,
        )
    return str(candidates[0])


def _build_pipeline(args: argparse.Namespace, exporter: IExporter) -> Any:
    from omniocr.composition.desktop import (
        create_ensemble_pipeline,
        create_tesseract_pipeline,
    )

    script = Script(args.script)
    if args.engine == "tesseract":
        return create_tesseract_pipeline(
            language=args.lang,
            script=script,
            exporter=exporter,
            assemble_structure=args.structure,
            workers=args.workers,
        )
    # kraken and ensemble share the ensemble root; it routes per script and
    # falls back to Tesseract for varieties Kraken is not the best tool for.
    return create_ensemble_pipeline(
        tesseract_language=args.lang,
        kraken_model_path=_resolve_model(args.model),
        script=script,
        exporter=exporter,
        assemble_structure=args.structure,
        workers=args.workers,
    )


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------


def _cmd_doctor(args: argparse.Namespace) -> int:
    env = _environment()
    problems = _environment_problems(env)
    payload = {"ok": not problems, "problems": problems, **env}

    if args.json:
        _emit_json(payload)
    else:
        tess = env["tesseract"]
        print(f"tesseract : {'yes' if tess['available'] else 'NO'} ({tess['path'] or '-'})")
        print(f"  langs   : {', '.join(tess['languages']) or '-'}")
        print(f"kraken    : {'yes' if env['kraken']['installed'] else 'NO'}")
        print(f"  models  : {len(env['kraken']['models'])} in {_MODELS_DIR}/")
        stack = env["torch_stack"]
        if stack["installed"]:
            state = "ok" if stack.get("coherent") else "BROKEN"
            print(f"torch     : {stack['torch']} / torchvision {stack['torchvision']} [{state}]")
        print(f"device    : {env['device']['selected']}")
        if "note" in env["device"]:
            print(f"  note    : {env['device']['note']}")
        for extra, present in sorted(env["extras"].items()):
            print(f"{extra:10}: {'yes' if present else 'NO'}")
        if problems:
            print("\nproblems:")
            for problem in problems:
                print(f"  - {problem}")
        else:
            print("\nno problems found")
    return EXIT_OK if not problems else EXIT_ENVIRONMENT


def _build_eval_engine(args: argparse.Namespace) -> Any:
    """Build a bare ``IOCREngine`` for evaluation — not a full pipeline.

    ``evaluate()`` scores one engine's raw recognition against ground truth;
    routing, reconciliation, and post-correction are pipeline concerns this
    command deliberately does not exercise, so a document's per-engine
    accuracy is not muddied by what the ensemble later does with it.
    """
    if args.engine == "tesseract":
        from omniocr.infrastructure.tesseract import TesseractEngine

        return TesseractEngine(language=args.lang)

    from omniocr.infrastructure.kraken import KrakenEngine

    return KrakenEngine(model_path=Path(_resolve_model(args.model)))


def _load_baseline(path: Path) -> dict[str, RegressionBaseline]:
    from omniocr.application.metrics import RegressionBaseline

    data = json.loads(path.read_text(encoding="utf-8"))
    return {
        script: RegressionBaseline(cer=values["cer"], wer=values["wer"])
        for script, values in data.items()
    }


def _cmd_eval(args: argparse.Namespace) -> int:
    from omniocr.application.evaluation import evaluate
    from omniocr.domain.corpus import SplitName
    from omniocr.infrastructure.corpus_repository import FileCorpusRepository

    corpus_root = Path(args.corpus)
    if not corpus_root.is_dir():
        print(f"corpus not found: {corpus_root}", file=sys.stderr)
        return EXIT_USAGE

    script_filter = Script(args.script) if args.script is not None else None
    repo = FileCorpusRepository(corpus_root)
    split = SplitName(args.split)
    pages = repo.pages(split, script_filter)
    if not pages:
        print(f"no pages found for split={args.split} under {corpus_root}", file=sys.stderr)
        return EXIT_USAGE

    try:
        engine = _build_eval_engine(args)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ENVIRONMENT

    result = evaluate(engine, pages, split)
    if isinstance(result, Err):
        print(f"evaluation failed: {result.error}", file=sys.stderr)
        return EXIT_FAILURE
    report = result.value

    baseline_by_script: dict[str, Any] | None = None
    regressed: list[str] = []
    if args.baseline is not None:
        baseline_path = Path(args.baseline)
        if not baseline_path.is_file():
            print(f"baseline not found: {baseline_path}", file=sys.stderr)
            return EXIT_USAGE
        baseline_by_script = _load_baseline(baseline_path)
        for script, cer in report.per_script_cer.items():
            baseline = baseline_by_script.get(script.value)
            if baseline is None:
                continue
            wer = report.per_script_wer.get(script, 0.0)
            if cer > baseline.cer + args.tolerance or wer > baseline.wer + args.tolerance:
                regressed.append(script.value)

    if args.json:
        _emit_json(
            {
                "ok": not regressed,
                "engine": engine.name,
                "split": args.split,
                "sample_count": report.sample_count,
                "per_script_cer": {s.value: v for s, v in report.per_script_cer.items()},
                "per_script_wer": {s.value: v for s, v in report.per_script_wer.items()},
                "regressed": regressed,
            }
        )
    else:
        print(f"engine={engine.name} split={args.split} samples={report.sample_count}")
        for script, cer in sorted(report.per_script_cer.items(), key=lambda kv: kv[0].value):
            wer = report.per_script_wer.get(script, 0.0)
            flag = " REGRESSED" if script.value in regressed else ""
            print(f"  {script.value:10} cer={cer:.4f} wer={wer:.4f}{flag}")
        if regressed:
            print(f"\nregression exceeded baseline for: {', '.join(regressed)}")

    return EXIT_OK if not regressed else EXIT_FAILURE


def _cmd_run(args: argparse.Namespace) -> int:
    source = Path(args.input)
    if not source.is_file():
        print(f"input not found: {source}", file=sys.stderr)
        return EXIT_USAGE

    if args.out is None:
        # No --out: land in Outputs/ under the current directory, named after
        # the input, instead of forcing every caller to compute a path.
        fmt = args.format or "txt"
        out = _default_output_path(source, fmt)
    else:
        out = Path(args.out)
        try:
            fmt = _resolve_format(out, args.format)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return EXIT_USAGE

    payload = source.read_bytes()

    try:
        exporter = _build_exporter(fmt, payload)
        orchestrator = _build_pipeline(args, exporter)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_ENVIRONMENT

    context = TenantContext(
        organization_id=args.org, user_id=args.user, subscription_tier="desktop"
    )

    # run_iteratively streams pages so progress is visible and --max-pages can
    # stop early; a per-page failure is captured on the page, not raised.
    pages: list[DocumentPage] = []
    failures: list[dict[str, Any]] = []
    for number, page in orchestrator.run_iteratively(payload, context):
        pages.append(page)
        for failure in page.failures:
            failures.append(
                {"page": number, "type": failure.error_type, "message": failure.message}
            )
        # Progress always goes to stderr, including under --json: stdout stays
        # machine-clean either way, and an unattended caller processing a long
        # book needs to see that it is still making progress.
        state = "failed" if page.failures else "ok"
        print(f"page {number}: {state} ({len(page.lines)} lines)", file=sys.stderr)
        if args.max_pages is not None and len(pages) >= args.max_pages:
            break

    if not pages:
        print("no pages were produced from the input", file=sys.stderr)
        return EXIT_FAILURE

    document = DocumentStructure(pages=tuple(pages))
    exported = orchestrator.export(document, context)
    if isinstance(exported, Err):
        print(f"export failed: {exported.error}", file=sys.stderr)
        return EXIT_FAILURE

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(exported.value)

    line_count = sum(len(page.lines) for page in pages)
    suggestion_count = sum(len(page.suggestions) for page in pages)
    if args.json:
        _emit_json(
            {
                "ok": not failures,
                "input": str(source),
                "output": str(out),
                "format": fmt,
                "engine": args.engine,
                "script": args.script,
                "pages": len(pages),
                "lines": line_count,
                "suggestions": suggestion_count,
                "failures": failures,
            }
        )
    else:
        print(
            f"wrote {out} ({len(pages)} pages, {line_count} lines, {suggestion_count} suggestions)"
        )

    # Partial output is still written, but a caller must be able to detect it.
    return EXIT_OK if not failures else EXIT_FAILURE


# --------------------------------------------------------------------------
# argument parsing
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="omniocr",
        description="Faithful OCR for printed Greek. Headless, non-interactive.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor", help="report engine/model availability; exit 3 if unusable"
    )
    doctor.add_argument("--json", action="store_true", help="emit JSON on stdout")
    doctor.set_defaults(func=_cmd_doctor)

    run = subparsers.add_parser("run", help="recognize a document and export it")
    run.add_argument("input", help="source PDF or image")
    run.add_argument(
        "--out",
        default=None,
        help=f"output file; extension selects the format (default: {_OUTPUT_DIR}/<input-name>.txt)",
    )
    run.add_argument(
        "--format",
        choices=sorted(set(_FORMAT_BY_SUFFIX.values()) | {"page"}),
        default=None,
        help="override the format inferred from --out",
    )
    run.add_argument(
        "--engine",
        choices=("tesseract", "kraken", "ensemble"),
        default="ensemble",
        help=(
            "recognition engine (default: ensemble). ensemble/kraken run Kraken "
            "on CPU and can take minutes PER PAGE — set process timeouts "
            "accordingly, or use --engine tesseract for a fast first pass "
            "(seconds per page)."
        ),
    )
    run.add_argument(
        "--script",
        choices=[script.value for script in Script],
        default=Script.POLYTONIC.value,
        help="script variety hint for routing and lexicons (default: polytonic)",
    )
    run.add_argument(
        "--lang",
        default=DEFAULT_TESSERACT_LANGUAGE,
        help=f"Tesseract language pack (default: {DEFAULT_TESSERACT_LANGUAGE})",
    )
    run.add_argument(
        "--model",
        default=None,
        help="Kraken .mlmodel path (default: the model flagged in models/manifest.json)",
    )
    run.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="stop after N pages — use this before running a full book",
    )
    run.add_argument(
        "--structure",
        action="store_true",
        help="reconstruct paragraphs, join hyphens, and mark running heads",
    )
    run.add_argument(
        "--workers",
        type=int,
        default=1,
        metavar="N",
        help="process N pages concurrently (default: 1, sequential)",
    )
    run.add_argument("--org", default="cli", help="tenant organization id for provenance")
    run.add_argument("--user", default="cli", help="user id for provenance")
    run.add_argument("--json", action="store_true", help="emit JSON on stdout")
    run.set_defaults(func=_cmd_run)

    ev = subparsers.add_parser(
        "eval", help="measure CER/WER for one engine against a ground-truth corpus split"
    )
    ev.add_argument(
        "--corpus",
        default="corpus",
        help="corpus root containing train/dev/test subdirectories (default: corpus)",
    )
    ev.add_argument(
        "--split",
        choices=[split.value for split in SplitName],
        default="test",
        help="corpus split to evaluate against (default: test)",
    )
    ev.add_argument(
        "--engine",
        choices=("tesseract", "kraken"),
        default="tesseract",
        help="recognition engine to evaluate (default: tesseract)",
    )
    ev.add_argument(
        "--script",
        choices=[script.value for script in Script],
        default=None,
        help="restrict to one script variety (default: all scripts in the split)",
    )
    ev.add_argument(
        "--lang",
        default=DEFAULT_TESSERACT_LANGUAGE,
        help=f"Tesseract language pack (default: {DEFAULT_TESSERACT_LANGUAGE})",
    )
    ev.add_argument(
        "--model",
        default=None,
        help="Kraken .mlmodel path (default: the model flagged in models/manifest.json)",
    )
    ev.add_argument(
        "--baseline",
        default=None,
        help="JSON file of {script: {cer, wer}} reviewed baselines; exit 1 if exceeded",
    )
    ev.add_argument(
        "--tolerance",
        type=float,
        default=0.0,
        help="allowed CER/WER drift above the baseline before it counts as a regression",
    )
    ev.add_argument("--json", action="store_true", help="emit JSON on stdout")
    ev.set_defaults(func=_cmd_eval)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Pin diagnostics to stderr before anything can log. Skipping this left
    # structlog on its default PrintLogger, which writes to stdout — pipeline
    # log lines then appeared *ahead of* the payload under --json and broke
    # the "--json | jq is safe" contract this module documents.
    try:
        from omniocr.infrastructure.logging import configure_logging

        configure_logging("omniocr-cli", stream=sys.stderr)
    except ImportError:
        pass  # structlog is a dev-only extra; a base install just logs less
    try:
        exit_code: int = args.func(args)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_FAILURE
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
