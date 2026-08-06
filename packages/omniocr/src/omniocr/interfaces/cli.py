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
from typing import Any, Sequence

from omniocr.domain.models import DocumentPage, DocumentStructure, Script, TenantContext
from omniocr.domain.result import Err
from omniocr.ports.interfaces import IExporter

# Exit codes. Stable contract for scripted callers — do not renumber.
EXIT_OK = 0
EXIT_FAILURE = 1  # pipeline or export failed
EXIT_USAGE = 2  # argparse default for bad arguments
EXIT_ENVIRONMENT = 3  # a required engine/model/dependency is missing

_MODELS_DIR = Path("models")
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
    if explicit is not None:
        return explicit
    candidates = sorted(_MODELS_DIR.glob("*.mlmodel"))
    if not candidates:
        raise FileNotFoundError(
            f"no Kraken model found in {_MODELS_DIR}/ — pass --model or see models/README.md"
        )
    return str(candidates[0])


def _build_pipeline(args: argparse.Namespace, exporter: IExporter) -> Any:
    from omniocr.composition.desktop import (
        create_ensemble_pipeline,
        create_tesseract_pipeline,
    )

    script = Script(args.script)
    if args.engine == "tesseract":
        return create_tesseract_pipeline(language=args.lang, script=script, exporter=exporter)
    # kraken and ensemble share the ensemble root; it routes per script and
    # falls back to Tesseract for varieties Kraken is not the best tool for.
    return create_ensemble_pipeline(
        tesseract_language=args.lang,
        kraken_model_path=_resolve_model(args.model),
        script=script,
        exporter=exporter,
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
    run.add_argument("--lang", default="grc", help="Tesseract language pack (default: grc)")
    run.add_argument(
        "--model", default=None, help="Kraken .mlmodel path (default: first in models/)"
    )
    run.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="stop after N pages — use this before running a full book",
    )
    run.add_argument("--org", default="cli", help="tenant organization id for provenance")
    run.add_argument("--user", default="cli", help="user id for provenance")
    run.add_argument("--json", action="store_true", help="emit JSON on stdout")
    run.set_defaults(func=_cmd_run)

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
