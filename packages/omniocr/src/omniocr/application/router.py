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
        return self._by_script.get(line.script, self._default)


__all__ = ["ScriptRouter"]
