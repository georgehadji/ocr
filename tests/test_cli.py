"""Tests for the headless CLI entry point.

The CLI is the contract an unattended caller (agent, CI job, batch script)
depends on, so these tests pin the parts that such a caller cannot recover
from if they drift: exit codes, JSON-on-stdout, and diagnostics-on-stderr.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from omniocr.interfaces import cli


class _StubOrchestrator:
    """Stands in for a wired pipeline so CLI tests need no OCR engine."""

    def __init__(self, pages, export_result) -> None:
        self._pages = pages
        self._export_result = export_result
        self.exported = False

    def run_iteratively(self, document, context):
        for page in self._pages:
            yield (page.number, page)

    def export(self, document, context):
        self.exported = True
        return self._export_result


def _page(number: int, lines=(), failures=(), suggestions=()):
    from omniocr.domain.models import DocumentPage

    return DocumentPage(
        number=number,
        width=100,
        height=100,
        lines=tuple(lines),
        failures=tuple(failures),
        suggestions=tuple(suggestions),
    )


def _line(line_id: str, text: str):
    from omniocr.domain.models import BBox, Confidence, OCRLine

    return OCRLine(id=line_id, text=text, confidence=Confidence(90.0), bbox=BBox(0, 0, 10, 10))


# ---------------------------------------------------------------- parsing


def test_format_inferred_from_extension() -> None:
    assert cli._resolve_format(Path("out.docx"), None) == "docx"
    assert cli._resolve_format(Path("out.md"), None) == "md"
    assert cli._resolve_format(Path("out.xml"), None) == "alto"


def test_format_override_wins() -> None:
    assert cli._resolve_format(Path("out.txt"), "page") == "page"


def test_unknown_extension_is_a_usage_error() -> None:
    with pytest.raises(ValueError, match="cannot infer output format"):
        cli._resolve_format(Path("out.rtf"), None)


def test_out_is_optional() -> None:
    """--out may be omitted; the caller gets a default path instead of an error."""
    args = cli.build_parser().parse_args(["run", "book.pdf"])
    assert args.out is None


def test_no_subcommand_is_rejected() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.build_parser().parse_args([])
    assert excinfo.value.code == cli.EXIT_USAGE


# ---------------------------------------------------------------- doctor


def test_doctor_json_goes_to_stdout(capsys, monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "_environment",
        lambda: {
            "tesseract": {
                "available": True,
                "path": "/usr/bin/tesseract",
                "languages": ["ell", "grc"],
            },
            "kraken": {"installed": True, "models": ["models/a.mlmodel"]},
            "device": {"selected": "cpu"},
            "extras": {"pdf": True, "docx": True, "opencv": True, "pillow": True},
        },
    )
    code = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert payload["ok"] is True
    assert payload["problems"] == []


def test_doctor_exits_environment_when_language_pack_missing(capsys, monkeypatch) -> None:
    """The failure this exists to catch: tesseract present, `grc` absent."""
    monkeypatch.setattr(
        cli,
        "_environment",
        lambda: {
            "tesseract": {"available": True, "path": "/usr/bin/tesseract", "languages": ["eng"]},
            "kraken": {"installed": True, "models": ["models/a.mlmodel"]},
            "device": {"selected": "cpu"},
            "extras": {"pdf": True, "docx": True, "opencv": True, "pillow": True},
        },
    )
    code = cli.main(["doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_ENVIRONMENT
    assert payload["ok"] is False
    assert any("ell, grc" in problem or "grc" in problem for problem in payload["problems"])


class TestDoctorReportsTheDevice:
    """ "Do I have a GPU?" is the question `doctor` exists to answer."""

    @staticmethod
    def _env(device: dict[str, str]) -> dict[str, object]:
        return {
            "tesseract": {
                "available": True,
                "path": "/usr/bin/tesseract",
                "languages": ["ell", "grc"],
            },
            "kraken": {"installed": True, "models": ["models/a.mlmodel"]},
            "device": device,
            "extras": {"pdf": True, "docx": True, "opencv": True, "pillow": True},
        }

    def test_device_is_top_level_not_nested_under_kraken(self, capsys, monkeypatch) -> None:
        """Fine-tuning uses the same device, so it is not a Kraken sub-field."""
        monkeypatch.setattr(cli, "_environment", lambda: self._env({"selected": "cuda:0"}))

        cli.main(["doctor", "--json"])

        assert json.loads(capsys.readouterr().out)["device"]["selected"] == "cuda:0"

    def test_text_output_prints_the_device(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(cli, "_environment", lambda: self._env({"selected": "cuda:0"}))

        cli.main(["doctor"])

        assert "device    : cuda:0" in capsys.readouterr().out

    def test_text_output_prints_the_note_when_cpu_is_explainable(self, capsys, monkeypatch) -> None:
        """A bare "cpu" hides the difference between no GPU and the wrong torch wheel."""
        monkeypatch.setattr(
            cli,
            "_environment",
            lambda: self._env({"selected": "cpu", "note": "torch 2.10.0+cpu is a CPU-only build."}),
        )

        cli.main(["doctor"])

        assert "CPU-only build" in capsys.readouterr().out

    def test_no_note_is_printed_when_there_is_nothing_to_explain(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(cli, "_environment", lambda: self._env({"selected": "cuda:0"}))

        cli.main(["doctor"])

        assert "note" not in capsys.readouterr().out


class TestJsonIsAlwaysUtf8:
    """``--json`` is the contract an unattended caller parses.

    ``print()`` encodes through the console codepage — cp1253 on the Greek
    Windows install this project targets — so a payload holding a Greek
    filename reached the caller as bytes that were not valid UTF-8 and
    ``json.loads`` failed outright.
    """

    def test_payload_is_written_as_utf8_bytes(self, monkeypatch) -> None:
        written = io.BytesIO()

        class _Cp1253Stdout:
            """Stands in for a console that cannot encode Greek."""

            encoding = "cp1253"
            buffer = written

            def write(self, text: str) -> int:
                raise UnicodeEncodeError("cp1253", text, 0, 1, "cannot encode")

            def flush(self) -> None:
                pass

        monkeypatch.setattr(cli.sys, "stdout", _Cp1253Stdout())

        cli._emit_json({"input": "Πολυχρονιάδης Δεδούσης 2.0.pdf", "ok": True})

        payload = json.loads(written.getvalue().decode("utf-8"))
        assert payload["input"] == "Πολυχρονιάδης Δεδούσης 2.0.pdf"

    def test_greek_text_survives_the_round_trip(self, monkeypatch) -> None:
        written = io.BytesIO()
        monkeypatch.setattr(
            cli.sys, "stdout", type("S", (), {"buffer": written, "flush": lambda self: None})()
        )

        cli._emit_json({"lines": ["ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙ͂Α", "ΤῊΣ ΘΕΣΣΑΛΟΝΙΚΗΣ"]})

        assert json.loads(written.getvalue().decode("utf-8"))["lines"][1] == "ΤῊΣ ΘΕΣΣΑΛΟΝΙΚΗΣ"


def test_environment_problems_flags_missing_kraken_model() -> None:
    problems = cli._environment_problems(
        {
            "tesseract": {"available": True, "path": "t", "languages": ["ell", "grc"]},
            "kraken": {"installed": True, "models": []},
            "extras": {"pdf": True},
        }
    )
    assert any("mlmodel" in problem for problem in problems)


# ---------------------------------------------------------------- run


def test_run_writes_output_and_reports_json(tmp_path, capsys, monkeypatch) -> None:
    from omniocr.domain.result import Ok

    source = tmp_path / "in.png"
    source.write_bytes(b"fake-image")
    out = tmp_path / "nested" / "out.txt"

    greek = "Ἑλληνικά"
    stub = _StubOrchestrator([_page(1, lines=(_line("l1", greek),))], Ok(greek.encode("utf-8")))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    code = cli.main(["run", str(source), "--out", str(out), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert out.read_bytes() == "Ἑλληνικά".encode("utf-8")
    assert payload["ok"] is True
    assert payload["pages"] == 1
    assert payload["lines"] == 1
    assert payload["failures"] == []


def test_default_output_path_uses_input_stem() -> None:
    assert cli._default_output_path(Path("book.pdf"), "docx") == Path("Outputs/book.docx")
    assert cli._default_output_path(Path("scan.png"), "txt") == Path("Outputs/scan.txt")
    assert cli._default_output_path(Path("page.png"), "page") == Path("Outputs/page.xml")


def test_run_without_out_writes_into_outputs_dir(tmp_path, capsys, monkeypatch) -> None:
    """The default output location this exists to guarantee."""
    from omniocr.domain.result import Ok

    monkeypatch.setattr(cli, "_OUTPUT_DIR", tmp_path / "Outputs")
    source = tmp_path / "book.png"
    source.write_bytes(b"fake-image")

    stub = _StubOrchestrator([_page(1, lines=(_line("l1", "ok"),))], Ok(b"ok"))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    code = cli.main(["run", str(source), "--json"])
    payload = json.loads(capsys.readouterr().out)

    expected = tmp_path / "Outputs" / "book.txt"
    assert code == cli.EXIT_OK
    assert expected.is_file()
    assert expected.read_bytes() == b"ok"
    assert payload["output"] == str(expected)


def test_run_without_out_respects_format_override(tmp_path, capsys, monkeypatch) -> None:
    from omniocr.domain.result import Ok

    monkeypatch.setattr(cli, "_OUTPUT_DIR", tmp_path / "Outputs")
    source = tmp_path / "book.png"
    source.write_bytes(b"fake-image")

    stub = _StubOrchestrator([_page(1)], Ok(b"<xml/>"))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    cli.main(["run", str(source), "--format", "alto", "--json"])

    assert (tmp_path / "Outputs" / "book.xml").is_file()


def test_run_max_pages_stops_early(tmp_path, capsys, monkeypatch) -> None:
    from omniocr.domain.result import Ok

    source = tmp_path / "in.pdf"
    source.write_bytes(b"fake-pdf")
    out = tmp_path / "out.txt"

    stub = _StubOrchestrator([_page(n) for n in range(1, 11)], Ok(b""))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    code = cli.main(["run", str(source), "--out", str(out), "--max-pages", "3", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert payload["pages"] == 3


def test_run_reports_page_failures_and_exits_nonzero(tmp_path, capsys, monkeypatch) -> None:
    """Partial output is written, but the caller must be able to detect it."""
    from omniocr.domain.models import PageFailure
    from omniocr.domain.result import Ok

    source = tmp_path / "in.pdf"
    source.write_bytes(b"fake-pdf")
    out = tmp_path / "out.txt"

    failed = _page(2, failures=(PageFailure(error_type="EngineError", message="boom"),))
    stub = _StubOrchestrator([_page(1, lines=(_line("l1", "ok"),)), failed], Ok(b"ok"))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    code = cli.main(["run", str(source), "--out", str(out), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_FAILURE
    assert out.is_file(), "partial output must still be written"
    assert payload["ok"] is False
    assert payload["failures"] == [{"page": 2, "type": "EngineError", "message": "boom"}]


def test_run_export_failure_exits_failure(tmp_path, capsys, monkeypatch) -> None:
    from omniocr.domain.errors import ExportError
    from omniocr.domain.result import Err

    source = tmp_path / "in.png"
    source.write_bytes(b"x")
    out = tmp_path / "out.txt"

    stub = _StubOrchestrator([_page(1)], Err(ExportError("no font")))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    code = cli.main(["run", str(source), "--out", str(out), "--json"])
    captured = capsys.readouterr()

    assert code == cli.EXIT_FAILURE
    assert not out.exists()
    assert "no font" in captured.err
    assert captured.out == "", "stdout must stay clean for machine consumers"


def test_run_missing_input_exits_usage(tmp_path, capsys) -> None:
    code = cli.main(
        ["run", str(tmp_path / "nope.pdf"), "--out", str(tmp_path / "out.txt"), "--json"]
    )
    assert code == cli.EXIT_USAGE
    assert "input not found" in capsys.readouterr().err


def test_run_missing_kraken_model_exits_environment(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / "in.png"
    source.write_bytes(b"x")
    monkeypatch.setattr(cli, "_MODELS_DIR", tmp_path / "empty-models")
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    code = cli.main(
        ["run", str(source), "--out", str(tmp_path / "out.txt"), "--engine", "kraken", "--json"]
    )
    assert code == cli.EXIT_ENVIRONMENT
    assert "no Kraken model" in capsys.readouterr().err


def test_progress_never_pollutes_stdout(tmp_path, capsys, monkeypatch) -> None:
    """Human progress goes to stderr so `--json | jq` is always safe."""
    from omniocr.domain.result import Ok

    source = tmp_path / "in.pdf"
    source.write_bytes(b"x")
    out = tmp_path / "out.txt"

    stub = _StubOrchestrator([_page(n) for n in (1, 2)], Ok(b""))
    monkeypatch.setattr(cli, "_build_pipeline", lambda args, exporter: stub)
    monkeypatch.setattr(cli, "_build_exporter", lambda fmt, payload: object())

    cli.main(["run", str(source), "--out", str(out), "--json"])
    captured = capsys.readouterr()

    json.loads(captured.out)  # stdout must be exactly one JSON document
    assert "page 1" in captured.err
