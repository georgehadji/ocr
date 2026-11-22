"""Every declared lifecycle in one place, for the generated catalog and its test.

Lives in ``composition`` because the machines are owned by different layers
(``ports``, ``application``, ``infrastructure``) and only the composition root
may import them all. Each phase of ``docs/STATE_MACHINES_PLAN.md`` adds its
``MachineSpec`` here; ``scripts/gen_state_docs.py`` renders them into
``docs/STATE_MACHINES.md`` and CI fails if that file is stale.
"""

from __future__ import annotations

from omniocr.domain.state import MachineSpec, machine
from omniocr.ports.interfaces import JOB_TRANSITIONS, JobState

OCR_JOB = machine(
    "OCR job",
    "omniocr.ports.interfaces",
    JobState,
    JobState.QUEUED,
    JOB_TRANSITIONS,
    external=frozenset({JobState.NOT_FOUND}),
)

ALL_MACHINES: tuple[MachineSpec, ...] = (OCR_JOB,)

__all__ = ["ALL_MACHINES", "OCR_JOB"]
