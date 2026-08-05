"""Composition roots — the only place concrete adapters are named.

Editions import from here, never from ``infrastructure`` directly. That is what
keeps the wiring invariants (resilience decorators, VLM grounding, script
lexicons, promoted-model routing) true for every edition instead of only the
one that happened to wire them. ``scripts/check_layering.py`` enforces it.
"""

from omniocr.composition.desktop import (
    create_ensemble_pipeline,
    create_tesseract_pipeline,
)
from omniocr.composition.training import create_training_pipeline

__all__ = [
    "create_ensemble_pipeline",
    "create_tesseract_pipeline",
    "create_training_pipeline",
]
