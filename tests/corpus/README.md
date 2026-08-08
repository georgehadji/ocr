# OCR regression corpus

Two tiers, declared per fixture in `PROVENANCE.json` and enforced by
`tests/test_corpus_provenance.py`. The distinction is the whole point of this
directory — collapsing it is how a rendered-Arial CER of 0.0000 ended up
committed as a polytonic accuracy baseline.

## `synthetic`

Machine-rendered from a known string by `scripts/generate_fixtures.py`.

Valid for: harness tests — does CER compute, does the gate trip, does an
engine read Greek at all rather than emitting noise or Latin transliteration.

**Not valid for accuracy claims.** A render has no scan noise, no historical
typeface, no skew or show-through, and its "ground truth" is the input string
rather than a human transcription. All four current fixtures are this tier,
rendered in Arial — including `byzantine-1`, which contains no Byzantine
typeface and no ligatures.

## `scan`

A real page image plus a human **diplomatic** transcription. The only tier
accuracy baselines may be computed from.

Every scan fixture needs:

- the page image (`{id}.png`) and its ground truth (`{id}.txt`, NFC-normalized)
- `source`, `licence`, and `transcribed_by` in `PROVENANCE.json`
- the script variety: `modern`, `polytonic`, `ancient`, `byzantine`, or `pontian`

Ground truth must come from a human reading the page. Never seed it from OCR
output — that is the D10 defect the training pipeline was restructured to make
impossible (`GroundTruthLine.from_correction()` is deliberately the only
constructor).

## Rules

- Do not add uploaded documents or generated OCR output.
- Update baselines only alongside a reviewed model or preprocessing change.
- Relabelling a fixture from `synthetic` to `scan` to make a gate pass is
  tested against; it requires real source and transcriber provenance.
