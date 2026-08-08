"""Guards on corpus provenance.

The corpus is the evidence base for every accuracy claim the project makes,
so its own metadata gets tested. These catch the failure that produced a
committed polytonic baseline of CER 0.0000: a machine-rendered fixture being
treated as if it measured recognition of a printed page.
"""

from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from omniocr.testing.fixtures import (
    fixture_tier,
    list_fixture_ids,
    list_scan_ids,
    load_fixture_ground_truth,
    load_provenance,
)

_SCAN_REQUIRED_FIELDS = ("source", "licence", "transcribed_by")


def test_every_fixture_declares_its_tier() -> None:
    """An undeclared fixture could silently be used to back an accuracy claim."""
    undeclared = [fid for fid in list_fixture_ids() if fixture_tier(fid) == "unknown"]
    assert not undeclared, (
        f"fixtures missing a PROVENANCE.json entry: {undeclared} — "
        "declare tier 'synthetic' or 'scan'"
    )


def test_tiers_are_from_the_known_set() -> None:
    bad = {
        fid: tier
        for fid, tier in ((fid, fixture_tier(fid)) for fid in list_fixture_ids())
        if tier not in ("synthetic", "scan")
    }
    assert not bad, f"unknown corpus tiers: {bad}"


@pytest.mark.parametrize("fixture_id", list_scan_ids() or ["__none__"])
def test_scan_fixtures_carry_source_licence_and_transcriber(fixture_id: str) -> None:
    """A real scan without licence and transcriber provenance is unusable.

    Skips cleanly while the corpus is all-synthetic; becomes a real gate the
    moment the first scan lands.
    """
    if fixture_id == "__none__":
        pytest.skip("no scan-tier fixtures in the corpus yet")
    record = load_provenance()[fixture_id]
    missing = [field for field in _SCAN_REQUIRED_FIELDS if not record.get(field)]
    assert not missing, f"{fixture_id}: scan fixture missing {missing}"


def test_ground_truth_is_nfc_normalized() -> None:
    """CLAUDE.md rule 5 — Unicode hygiene, applied to the reference text itself.

    Un-normalized ground truth inflates CER against correctly-normalized
    engine output, so the corpus would punish a correct pipeline.
    """
    for fixture_id in list_fixture_ids():
        text = load_fixture_ground_truth(fixture_id)
        assert text == unicodedata.normalize("NFC", text), f"{fixture_id}: not NFC"


def test_synthetic_fixtures_are_not_silently_promoted() -> None:
    """Changing a tier to 'scan' must come with real provenance, not a relabel.

    The cheap way to make an accuracy gate pass is to relabel the fixture it
    runs on. This makes that require forging a source and transcriber.
    """
    for fixture_id, record in load_provenance().items():
        if record.get("tier") == "scan":
            assert not record.get("generated_by"), (
                f"{fixture_id}: declared 'scan' but still names a generator script"
            )


def test_provenance_file_covers_no_phantom_fixtures() -> None:
    """A stale entry for a deleted fixture hides that coverage was lost."""
    declared = set(load_provenance())
    present = set(list_fixture_ids())
    phantom = declared - present
    assert not phantom, f"PROVENANCE.json lists missing fixtures: {sorted(phantom)}"


def test_provenance_json_is_valid_and_has_a_fixtures_object() -> None:
    path = Path(__file__).parent / "corpus" / "PROVENANCE.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data.get("fixtures"), dict)
