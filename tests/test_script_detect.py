"""Latin-vs-Greek evidence and the reconciler that depends on it."""

from __future__ import annotations

import pytest

from omniocr.application.reconcile import ConfidenceWeightedReconciler, ScriptAwareReconciler
from omniocr.application.script_detect import is_latin_dominant, latin_evidence
from omniocr.domain.models import (
    BBox,
    Confidence,
    EngineRun,
    ModelRef,
    OCRLine,
    Script,
    TenantContext,
)
from omniocr.domain.result import Err, Ok

CONTEXT = TenantContext(organization_id="test", user_id="test", subscription_tier="desktop")


def line(
    text: str,
    confidence: float,
    engine: str = "kraken",
    model_name: str = "greek.mlmodel",
) -> OCRLine:
    return OCRLine(
        id="line-1",
        text=text,
        confidence=Confidence(confidence),
        bbox=BBox(0, 0, 100, 20),
        script=Script.POLYTONIC,
        provenance=EngineRun(
            engine=engine,
            model_ref=ModelRef(engine=engine, model_name=model_name, model_hash="x"),
            model_hash="x",
            params=(),
            timestamp="2026-08-09T00:00:00Z",
        ),
    )


def kraken_line(text: str, confidence: float) -> OCRLine:
    return line(text, confidence, engine="kraken", model_name="greek.mlmodel")


def tesseract_line(text: str, confidence: float, langs: str = "grc+ell+eng+deu+fra") -> OCRLine:
    return line(text, confidence, engine="tesseract", model_name=langs)


class TestLatinEvidence:
    def test_greek_majuscule_title_is_not_latin_evidence(self) -> None:
        """The homoglyph trap: this reads as Latin 'TA BYZANTINA MNHMEIA'.

        Every letter has a Latin lookalike, so a naive Latin-letter count
        would score it high and hand the book's own title to a Latin engine.
        """
        assert latin_evidence("ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ") == 0
        assert not is_latin_dominant("ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ")

    def test_latin_read_of_a_greek_title_still_scores_zero(self) -> None:
        # What Tesseract actually emits for that title when Latin packs load.
        assert latin_evidence("TA BYZANTINA MNHMEIA") == 0

    def test_polytonic_greek_prose_is_not_latin(self) -> None:
        assert not is_latin_dominant("ὅπου σχηματίζεται ἕνα εἶδος μικροσκοπικοῦ ὀροπεδίου")

    @pytest.mark.parametrize(
        "text",
        [
            "biblionet.gr",
            "https://www.greek-language.gr",
            "Byzantine Monuments of Thessaloniki",
            "Die byzantinischen Denkmäler",
            "Les monuments byzantins de Thessalonique",
        ],
    )
    def test_real_latin_is_detected(self, text: str) -> None:
        assert is_latin_dominant(text)

    def test_empty_text_is_not_latin(self) -> None:
        assert latin_evidence("") == 0


class TestScriptAwareReconciler:
    def test_latin_candidate_beats_higher_confidence_greek(self) -> None:
        """The real observed case: Kraken's mush outranks the URL on confidence.

        Kraken's charset does contain Latin letters, so its garbage also looks
        Latin -- text shape alone cannot separate these. Provenance can.
        """
        kraken = kraken_line("biblionetgrt?ocfposo?ocf〉o812oceobf", 0.95)
        tesseract = tesseract_line("biblionet.gr", 0.55)

        result = ScriptAwareReconciler().reconcile((kraken, tesseract), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.text == "biblionet.gr"

    def test_greek_line_still_decided_by_confidence(self) -> None:
        kraken = kraken_line("ὀροπεδίου", 0.91)
        tesseract = tesseract_line("ὀοοπεδίου", 0.62)

        result = ScriptAwareReconciler().reconcile((kraken, tesseract), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.text == "ὀροπεδίου"

    def test_greek_majuscule_does_not_trigger_latin_preference(self) -> None:
        """Regression guard for the homoglyph trap, end to end.

        Tesseract is Latin-equipped and more confident here. Without homoglyph
        filtering it would win and replace the book's Greek title with Latin
        lookalikes.
        """
        kraken = kraken_line("ΤΑ ΒΥΖΑΝΤΙΝΑ ΜΝΗΜΕΙΑ", 0.88)
        tesseract = tesseract_line("TA BYZANTINA MNHMEIA", 0.93)

        result = ScriptAwareReconciler().reconcile((kraken, tesseract), CONTEXT)

        assert isinstance(result, Ok)
        assert latin_evidence(result.value.text) == 0

    def test_greek_only_engines_fall_back_to_confidence(self) -> None:
        """No Latin pack loaded anywhere: nothing to prefer, so confidence rules."""
        kraken = kraken_line("biblionetgrt?ocfposo", 0.95)
        tesseract = tesseract_line("biblionet.gr", 0.55, langs="grc+ell")

        result = ScriptAwareReconciler().reconcile((kraken, tesseract), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.text == "biblionetgrt?ocfposo"

    def test_all_latin_candidates_fall_back_to_confidence(self) -> None:
        low = tesseract_line("Byzantine Monuments", 0.40)
        high = tesseract_line("Byzantine Monuments of Thessaloniki", 0.90)

        result = ScriptAwareReconciler().reconcile((low, high), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.confidence.value == pytest.approx(0.90)

    def test_missing_provenance_degrades_to_confidence(self) -> None:
        bare = OCRLine(
            id="line-1",
            text="biblionet.gr",
            confidence=Confidence(0.2),
            bbox=BBox(0, 0, 100, 20),
            script=Script.POLYTONIC,
        )
        kraken = kraken_line("βιβλιονετ", 0.8)

        result = ScriptAwareReconciler().reconcile((bare, kraken), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.text == "βιβλιονετ"

    def test_no_candidates_is_an_error(self) -> None:
        assert isinstance(ScriptAwareReconciler().reconcile((), CONTEXT), Err)


class TestHomoglyphGuard:
    """Real pairs observed on the Δεδούσης scans once Latin packs were loaded.

    In every case the Latinized reading was the *more confident* one, so
    confidence weighting alone Latinizes the book's Greek headings.
    """

    @pytest.mark.parametrize(
        ("greek", "latinized"),
        [
            ("ΕΡΓΑ ΤΟΥ ΙδΙΟΥ", "ΕΡΓΑ TOY IAIOY"),
            ("ΡΟΤΟΝΤΑ ΤΟΥ ΑΓΙΟΥ ΓΕΩΡΓΙΟΥ", "POTONTA ΤΟΥ ΑΓΙΟΥ ΓΕΩΡΓΙΟΥ"),
            ("χεθος τῆς ὑδρίας δείχνει", "yedos τῆς ὑδρίας δείχνει"),
            ("Ο ΜΑΡΙΝΗΣ Ο ΜΠΟΥΚΑΣ", "O MAPINHE O ΜΠΟΥΚΑΣ"),
        ],
    )
    def test_greek_script_survives_a_more_confident_latinization(
        self, greek: str, latinized: str
    ) -> None:
        kraken = kraken_line(greek, 0.60)
        tesseract = tesseract_line(latinized, 0.95)

        result = ScriptAwareReconciler().reconcile((kraken, tesseract), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.text == greek

    def test_guard_does_not_block_genuine_latin(self) -> None:
        """The guard must not undo the case it was built alongside."""
        kraken = kraken_line("βιβλιονετ", 0.95)
        tesseract = tesseract_line("biblionet.gr", 0.55)

        result = ScriptAwareReconciler().reconcile((kraken, tesseract), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.text == "biblionet.gr"

    def test_mixed_greek_and_url_line_is_left_to_confidence(self) -> None:
        """Both readings carry heavy ASCII, so this is a real Latin fragment.

        Preferring "fewest ASCII" here would pick whichever engine mangled the
        URL hardest — the opposite of the intent.
        """
        kraken = kraken_line(
            "τον Ἰανουαριο Φεβρουάριο 1962, (httpsftuww.greeklanguagegrtperiodika", 0.50
        )
        tesseract = tesseract_line(
            "τον Ιανουάριο-Φεβρουάριο 1962, (https://www.greek-language.gr/periodika", 0.80
        )

        result = ScriptAwareReconciler().reconcile((kraken, tesseract), CONTEXT)

        assert isinstance(result, Ok)
        assert "greek-language.gr" in result.value.text

    def test_pure_greek_candidates_still_decided_by_confidence(self) -> None:
        low = kraken_line("ἁλίκτυπο παραλία", 0.55)
        high = kraken_line("ἁλίπτυπο παραλία", 0.90)

        result = ScriptAwareReconciler().reconcile((low, high), CONTEXT)

        assert isinstance(result, Ok)
        assert result.value.text == "ἁλίπτυπο παραλία"

    def test_matches_confidence_reconciler_when_no_latin_present(self) -> None:
        candidates = (kraken_line("ἁλίκτυπο", 0.7), kraken_line("ἁλίπτυπο", 0.8))

        script_aware = ScriptAwareReconciler().reconcile(candidates, CONTEXT)
        confidence_only = ConfidenceWeightedReconciler().reconcile(candidates, CONTEXT)

        assert isinstance(script_aware, Ok)
        assert isinstance(confidence_only, Ok)
        assert script_aware.value.text == confidence_only.value.text
