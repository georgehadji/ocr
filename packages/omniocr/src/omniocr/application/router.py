"""Script-based router.

Selects engines from immutable script rules with a safe default. Fine-tuned
checkpoints are not routed per line: a promoted model is a new file in the
registry, and a run uses one by passing its path as the Kraken model.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from omniocr.domain.models import OCRLine, Script, TenantContext
from omniocr.ports.interfaces import IOCREngine, IRouter


class ScriptRouter(IRouter):
    """Select engines from immutable script rules with a safe default."""

    def __init__(
        self,
        by_script: Mapping[Script, Sequence[IOCREngine]],
        default: Sequence[IOCREngine] = (),
    ) -> None:
        self._by_script = {script: tuple(engines) for script, engines in by_script.items()}
        self._default = tuple(default)

    def route(self, line: OCRLine, context: TenantContext) -> Sequence[IOCREngine]:
        """Select engines for a line from its script."""
        return self._by_script.get(line.script, self._default)

    def engines(self) -> tuple[IOCREngine, ...]:
        """Every engine this router can hand out, each once, in routing order."""
        listed: list[IOCREngine] = []
        for group in (*self._by_script.values(), self._default):
            for engine in group:
                if not any(engine is seen for seen in listed):
                    listed.append(engine)
        return tuple(listed)


__all__ = ["ScriptRouter"]
