"""Tests for the bulk lexicons and their blob-backed adapter (A8a).

The lookup is a hand-written binary search over UTF-8 bytes, and a subtly
wrong binary search does not crash — it silently misses entries and looks like
a merely incomplete word list. So the central test compares it against a
reference set built from the same file rather than against hand-picked words.

The blobs are optional: a source checkout without the data files must degrade
to the curated lexicons rather than failing to import. Every test that needs
real data skips when it is absent.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

from omniocr.domain.models import Script
from omniocr.infrastructure.lexicons import lexicons_by_script
from omniocr.ports.lexicon import LayeredLexicon, SetLexicon, SortedBlobLexicon

DATA = Path("packages/omniocr/src/omniocr/data/lexicons")
ANCIENT = DATA / "ancient.txt"

requires_blobs = pytest.mark.skipif(
    not ANCIENT.is_file(),
    reason="bulk lexicon blobs absent; run scripts/build_lexicon.py",
)


def _write_blob(tmp_path: Path, forms: list[str]) -> Path:
    """Write a blob the way build_lexicon.py does: sorted by UTF-8 bytes."""
    path = tmp_path / "test.txt"
    ordered = sorted(forms, key=lambda form: form.encode("utf-8"))
    path.write_bytes(("\n".join(ordered) + "\n").encode("utf-8"))
    return path


class TestBlobLookup:
    def test_finds_every_form_it_contains(self, tmp_path: Path) -> None:
        forms = ["ἀγαθός", "βίος", "θεοτόκος", "πόλις", "ὕδωρ"]
        lexicon = SortedBlobLexicon("t", _write_blob(tmp_path, forms))

        for form in forms:
            assert lexicon.contains(form), form

    def test_rejects_forms_it_does_not_contain(self, tmp_path: Path) -> None:
        lexicon = SortedBlobLexicon("t", _write_blob(tmp_path, ["πόλις", "βίος"]))

        assert not lexicon.contains("ζζζζ")
        assert not lexicon.contains("πόλισμα")

    def test_finds_the_first_and_last_entries(self, tmp_path: Path) -> None:
        """Boundaries are where a binary search goes wrong."""
        forms = ["ἀγαθός", "βίος", "πόλις", "ὕδωρ", "ὠδή"]
        lexicon = SortedBlobLexicon("t", _write_blob(tmp_path, forms))
        ordered = sorted(forms, key=lambda f: f.encode("utf-8"))

        assert lexicon.contains(ordered[0])
        assert lexicon.contains(ordered[-1])

    def test_a_single_entry_blob_works(self, tmp_path: Path) -> None:
        lexicon = SortedBlobLexicon("t", _write_blob(tmp_path, ["πόλις"]))

        assert lexicon.contains("πόλις")
        assert not lexicon.contains("βίος")

    def test_reports_its_size(self, tmp_path: Path) -> None:
        lexicon = SortedBlobLexicon("t", _write_blob(tmp_path, ["α", "β", "γ"]))
        assert len(lexicon) == 3

    def test_agrees_with_setlexicon_on_exact_and_case_folded_forms(self, tmp_path: Path) -> None:
        forms = ["θεοτόκος", "εὐαγγέλιον", "πόλις"]
        blob = SortedBlobLexicon("t", _write_blob(tmp_path, forms))
        memory = SetLexicon(name="t", words=forms)

        for token in ["θεοτόκος", "ΠΌΛΙΣ", "εὐαγγέλιον", "ζζζζ"]:
            assert blob.contains(token) == memory.contains(token), token

    def test_is_stricter_than_setlexicon_about_dropped_accents(self, tmp_path: Path) -> None:
        """A documented, deliberate divergence — not an oversight.

        SetLexicon keeps a monotonized index of its stored words, so an
        accent-stripped query matches. A blob holds the accented forms and
        monotonizing the query alone matches nothing; reproducing the
        leniency would need a second blob per lexicon, doubling 53 MB of
        committed data.

        The strictness is closer to right here anyway: on a polytonic page a
        dropped accent is a recognition error, and this project transcribes
        diplomatically. The flag now arrives with candidate readings attached.
        """
        forms = ["θεοτόκος"]
        blob = SortedBlobLexicon("t", _write_blob(tmp_path, forms))
        memory = SetLexicon(name="t", words=forms)

        assert memory.contains("θεοτοκος"), "SetLexicon should still be lenient"
        assert not blob.contains("θεοτοκος"), "the blob is expected to be strict"


@requires_blobs
class TestAgainstRealData:
    def test_every_sampled_member_is_found(self) -> None:
        """The assertion that catches an off-by-one in the bisect.

        A binary search that mishandles a boundary still answers correctly for
        most inputs; only a broad sample exposes it.
        """
        lexicon = SortedBlobLexicon("ancient", ANCIENT)
        truth = [line for line in ANCIENT.read_text(encoding="utf-8").split("\n") if line]

        random.seed(11)
        for form in random.sample(truth, 500):
            assert lexicon.contains(form), form

    def test_absent_forms_are_not_invented(self) -> None:
        lexicon = SortedBlobLexicon("ancient", ANCIENT)
        truth = {line for line in ANCIENT.read_text(encoding="utf-8").split("\n") if line}

        random.seed(12)
        for form in random.sample(sorted(truth), 200):
            probe = form + "ζζ"
            if probe not in truth:
                assert not lexicon.contains(probe), probe

    def test_loading_does_not_build_a_per_form_index(self) -> None:
        """Resident cost must stay near the file size, not ~250 MB.

        An earlier implementation kept a Python int per line, which cost about
        30 MB and five seconds across the three lexicons — most of what
        avoiding a frozenset was supposed to save.
        """
        lexicon = SortedBlobLexicon("ancient", ANCIENT)
        assert not hasattr(lexicon, "_offsets")


class TestLayering:
    def test_the_union_is_what_contains_reports(self) -> None:
        first = SetLexicon(name="a", words=["πόλις"])
        second = SetLexicon(name="b", words=["βίος"])
        layered = LayeredLexicon(name="both", layers=[first, second])

        assert layered.contains("πόλις")
        assert layered.contains("βίος")
        assert not layered.contains("ζζζζ")

    def test_layer_order_is_preserved(self) -> None:
        """`confusion.rank` depends on it to rank dialect readings first."""
        first = SetLexicon(name="dialect", words=["κουτάλιν"])
        second = SetLexicon(name="bulk", words=["κουτάλειν"])
        layered = LayeredLexicon(name="pontian", layers=[first, second])

        assert [layer.name for layer in layered.layers] == ["dialect", "bulk"]

    def test_an_empty_layering_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            LayeredLexicon(name="empty", layers=[])


@requires_blobs
class TestScriptMapping:
    def test_the_primary_scripts_finally_have_a_lexicon(self) -> None:
        """The defect this workstream existed to fix.

        Polytonic, ancient and modern had none, which zeroed A6's 0.4 lexicon
        scoring term and left post-correction with nothing to check.
        """
        mapping = lexicons_by_script()

        for script in (Script.POLYTONIC, Script.ANCIENT, Script.MODERN):
            assert script in mapping, script
            assert mapping[script].contains("πόλις") or mapping[script].contains("πόλη")

    def test_curated_dialect_lexicons_come_first(self) -> None:
        """Reversed, a bulk list would 'correct' legitimate regional forms."""
        mapping = lexicons_by_script()

        pontian = mapping[Script.PONTIAN]
        byzantine = mapping[Script.BYZANTINE]

        assert getattr(pontian, "layers")[0].name == "pontian"
        assert getattr(byzantine, "layers")[0].name == "byzantine"

    def test_a_pontian_form_is_still_known(self) -> None:
        """The curated vocabulary must survive being layered over a bulk list."""
        assert lexicons_by_script()[Script.PONTIAN].contains("κουτάλιν")

    def test_polytonic_layers_modern_lists_behind_the_ancient_one(self) -> None:
        """This assertion previously demanded the opposite, and was wrong.

        The reasoning was that ancient and modern share only ~46k of 1.7M
        forms, so pointing a polytonic page at modern Greek would let
        monotonic spellings validate. That mistook the material: the target
        books are *modern Greek in polytonic orthography* - 20th-century
        prose, not classical texts. Against ancient forms alone, 309 of 456
        tokens on a human-transcribed page were flagged unknown (2026-09-09);
        with the modern lists layered in, 9.7% across all three pages.

        Ancient stays first so classical vocabulary still ranks ahead of a
        modern homograph when `confusion.rank` proposes candidates.
        """
        polytonic = lexicons_by_script()[Script.POLYTONIC]
        layers = [layer.name for layer in getattr(polytonic, "layers")]

        assert layers[0] == "ancient"
        assert "modern" in layers


class TestLineEndings:
    """CRLF must not silently break every lookup.

    Git's autocrlf rewrote the committed blobs on Windows checkout, appending
    a carriage return to every line. The byte comparison then missed on every
    form while the file still looked correct: CI went red on Windows and
    stayed green on Linux, and local runs passed because the blobs had been
    written by the build script rather than checked out.

    `.gitattributes` marks the blobs binary so Git cannot do it again, but
    that guard is outside the code and does not cover a blob arriving from an
    archive, so the reader tolerates CRLF too.
    """

    def test_a_crlf_blob_still_resolves(self, tmp_path: Path) -> None:
        forms = ["ἀγαθός", "βίος", "θεοτόκος", "πόλις", "ὕδωρ"]
        ordered = sorted(forms, key=lambda form: form.encode("utf-8"))
        path = tmp_path / "crlf.txt"
        path.write_bytes(("\r\n".join(ordered) + "\r\n").encode("utf-8"))

        lexicon = SortedBlobLexicon("crlf", path)

        for form in forms:
            assert lexicon.contains(form), form
        assert not lexicon.contains("ζζζζ")

    def test_the_blobs_are_marked_binary(self) -> None:
        """The real fix: stop the translation rather than cope with it."""
        attributes = Path(".gitattributes")
        assert attributes.is_file(), ".gitattributes is missing"
        assert "data/lexicons/*.txt -text" in attributes.read_text(encoding="utf-8")
