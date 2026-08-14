from __future__ import annotations

import string

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from omniocr.application.structure.assembler import DocumentAssembler, unjoin
from omniocr.application.structure.hyphenation import Dehyphenator
from omniocr.domain.models import BBox, Confidence, DocumentPage, DocumentStructure, OCRLine, TenantContext
from omniocr.domain.result import Ok
from omniocr.ports.lexicon import SetLexicon


def test_dehyphenator_decision_table() -> None:
    # Arrange: Create a lexicon with "εὐχή" and "ἀντι-εὐχή"
    lexicon = SetLexicon("greek", ["εὐχή", "ἀντι-εὐχή"])
    lexicons = {
        "ancient": lexicon,
        "byzantine": lexicon,
        "polytonic": lexicon,
    }
    dehyphenator = Dehyphenator(lexicons)

    conf = Confidence(1.0)
    bbox = BBox(10, 20, 100, 15)

    # Case 1: AB in lexicon, A-B not in lexicon -> drop hyphen ("joined_in_lexicon")
    # "εὐ-" and "χή" -> "εὐχή" in lexicon
    l1 = OCRLine(id="l1", text="εὐ-", confidence=conf, bbox=bbox, script="polytonic")
    l2 = OCRLine(id="l2", text="χή", confidence=conf, bbox=bbox, script="polytonic")
    join1 = dehyphenator.join(l1, l2)
    assert join1 is not None
    assert join1.separator == ""
    assert join1.verdict == "joined_in_lexicon"

    # Case 2: A-B in lexicon -> keep hyphen ("hyphen_in_lexicon")
    # "ἀντι-" and "εὐχή" -> "ἀντι-εὐχή" in lexicon
    l3 = OCRLine(id="l3", text="ἀντι-", confidence=conf, bbox=bbox, script="polytonic")
    l4 = OCRLine(id="l4", text="εὐχή", confidence=conf, bbox=bbox, script="polytonic")
    join2 = dehyphenator.join(l3, l4)
    assert join2 is not None
    assert join2.separator == "-"
    assert join2.verdict == "hyphen_in_lexicon"

    # Case 3: Neither in lexicon -> drop hyphen ("unverified")
    l5 = OCRLine(id="l5", text="unverified-", confidence=conf, bbox=bbox, script="polytonic")
    l6 = OCRLine(id="l6", text="word", confidence=conf, bbox=bbox, script="polytonic")
    join3 = dehyphenator.join(l5, l6)
    assert join3 is not None
    assert join3.separator == ""
    assert join3.verdict == "unverified"


# Bounded for relevance, not speed. This drew from whitelist_categories=("Lu",
# "Ll", "Lt", "Lm", "Lo", "Nd") — essentially every letter in Unicode. join and
# unjoin do codepoint index arithmetic, so what the property needs is multi-byte
# characters and real precomposed diacritics, which Greek supplies; drawing
# Hangul and CJK adds cost without adding a failure mode.
_WORD_ALPHABET = (
    string.ascii_letters
    + string.digits
    + "αβγδεζηθικλμνξοπρστυφχψω"
    + "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"
    + "ἀἁἄἅἆἐἑἔὀὁόὸῶῷᾳῃῳϊΐῒ"
)


@st.composite
def line_strategy(draw):
    text = draw(st.text(min_size=1, max_size=30, alphabet=_WORD_ALPHABET))
    text = text.strip()
    if not text:
        text = "dummy"

    # Optionally end with a hyphen
    if draw(st.booleans()):
        hyphen = draw(st.sampled_from(["-", "‐", "¬", "\u00ad"]))
        text = text + "word" + hyphen

    return OCRLine(
        id=f"line-{draw(st.uuids())}",
        text=text,
        confidence=Confidence(1.0),
        bbox=BBox(10, 20, 100, 15),
    )


# too_slow measures wall-clock during input generation, which here reflects the
# state of the whole process rather than anything this strategy does. Run alone
# — even under coverage — generation is fast; run after the Kraken suite has
# loaded torch models into the same interpreter, it crawls, and the check fires
# at a different example count each time (4 inputs/37s, then 8 inputs/25s).
# That is an intermittent red build with no commit behind it, on a property
# that holds over 20k examples. Suppressed rather than tuned, because there is
# no strategy change that makes it deterministic.
#
# The deadline stays on: a slow *example* would be a real signal about
# assemble/unjoin, unlike slow generation.
@settings(suppress_health_check=[HealthCheck.too_slow])
@given(lines=st.lists(line_strategy(), min_size=2, max_size=5))
def test_joins_are_exactly_reversible(lines: list[OCRLine]) -> None:
    # To test pure join reversibility without break rules splitting paragraphs,
    # let's construct paragraph/lines where vertical leading is constant and spacing is tight.
    # We rebuild the lines to have sequential vertical positions so GapRule doesn't split them.
    # And we set x=10 for all to avoid IndentRule splitting them.
    # And we set w=90 to avoid ShortLineRule splitting them.
    sequential_lines = []
    for idx, l in enumerate(lines):
        sequential_lines.append(
            OCRLine(
                id=l.id,
                text=l.text,
                confidence=l.confidence,
                bbox=BBox(10, 10 + idx * 25, 90, 15),
                script=l.script,
                region_type=l.region_type,
            )
        )

    doc = DocumentStructure(
        pages=(
            DocumentPage(
                number=1,
                width=150,
                height=500,
                lines=tuple(sequential_lines),
            ),
        )
    )
    context = TenantContext(organization_id="org", user_id="user", subscription_tier="desktop")

    assembler = DocumentAssembler()
    res = assembler.assemble(doc, context)
    assert isinstance(res, Ok)

    page = res.value.pages[0]
    # Since we designed lines to not split, there should be exactly 1 paragraph
    assert len(page.paragraphs) == 1
    paragraph = page.paragraphs[0]

    # Act & Assert: reverse the join to reconstruct original line texts
    reconstructed = unjoin(paragraph)
    assert reconstructed == tuple(sl.text for sl in sequential_lines)
