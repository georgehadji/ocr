# Pending scan fixtures — transcription needed

Three real page images are committed and waiting for diplomatic ground truth.
Until each has a `.txt`, it is **not** a fixture: `list_fixture_ids()` globs
`*.txt`, so these images are inert and no gate reads them.

| Image | Source page | Content |
|---|---|---|
| `polytonic-scan-1.png` | PDF page index 20 | body prose |
| `polytonic-scan-2.png` | PDF page index 34 (printed p. 35) | prose + two-column hymn quotes |
| `polytonic-scan-3.png` | PDF page index 48 | body prose |

Source: `PDFs for OCR/Πολυχρονιάδης Δεδούσης 2.0.pdf` — *Τα Βυζαντινά Μνημεία
της Θεσσαλονίκης*. 74 pages, no text layer, 300 DPI render.

## Steps

**1. Transcribe.** For each image write `tests/corpus/polytonic-scan-N.txt`.

Diplomatic means: reproduce what is printed, not what it should say. Keep
original orthography, accents, breathings, and abbreviations. Do not correct
the source, modernise spelling, or expand abbreviations. Preserve line breaks
as printed. Save UTF-8, NFC-normalized (`test_ground_truth_is_nfc_normalized`
enforces this).

For `polytonic-scan-2.png`, transcribe the two-column section in reading order
— left column fully, then right column.

**2. Declare provenance.** Add to `PROVENANCE.json` under `fixtures`, one per
transcribed page, filling `licence` and `transcribed_by`:

```json
"polytonic-scan-1": {
  "tier": "scan",
  "script": "polytonic",
  "source": "Τα Βυζαντινά Μνημεία της Θεσσαλονίκης, PDF page index 20, 300 DPI",
  "licence": "FILL IN",
  "transcribed_by": "FILL IN",
  "typeface": "20c polytonic serif"
}
```

`licence` is not optional — `test_scan_fixtures_carry_source_licence_and_transcriber`
fails without it. If the book is not redistributable, say so here and move the
images out of the repo rather than leaving the field vague.

**3. Compute baselines.**

```bash
python scripts/compute_engine_baselines.py
```

**4. Retire the synthetic accuracy gate.** Remove the `xfail` marker on
`test_kraken_beats_tesseract_on_hard_scripts` in `tests/test_engine_accuracy.py`
and point the accuracy tests at `list_scan_ids()` instead of `list_fixture_ids()`.
That is the moment the CER gate starts meaning something.

## Coverage gap

These three cover **polytonic only** — one book, one typeface. `modern`,
`ancient`, `byzantine`, and `pontian` still have no scan-tier fixture. Byzantine
in particular needs a real Byzantine typeface with ligatures, which this source
does not contain.
