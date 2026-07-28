"""Script-based router with optional model registry integration.

Selects engines from immutable script rules with a safe default. Optionally
consults an ``IModelRegistry`` to override with a promoted per-script model
when one is registered.
"""

from __future__ import annotations

from typing import Mapping, Sequence

from omniocr.domain.models import OCRLine, Script, TenantContext
from omniocr.ports.interfaces import IOCREngine, IModelRegistry, IRouter


class ScriptRouter(IRouter):
    """Select engines from immutable script rules with a safe default.

    When a ``model_registry`` is provided, the router checks for promoted
    models per script and routes to them preferentially, falling back to
    the default engine mapping.

    ``engine_map`` provides a mapping from engine ``.name`` (e.g. ``"kraken"``)
    to actual ``IOCREngine`` instances, so a promoted model's engine family
    can be resolved to a runnable engine.
    """

    def __init__(
        self,
        by_script: Mapping[Script, Sequence[IOCREngine]],
        default: Sequence[IOCREngine] = (),
        model_registry: IModelRegistry | None = None,
        engine_map: Mapping[str, IOCREngine] | None = None,
    ) -> None:
        self._by_script = {script: tuple(engines) for script, engines in by_script.items()}
        self._default = tuple(default)
        self._model_registry = model_registry
        self._engine_map: Mapping[str, IOCREngine] = engine_map or {}

    def route(self, line: OCRLine, context: TenantContext) -> Sequence[IOCREngine]:
        """Select engines for a line, consulting the model registry first."""
        # Check for a promoted model for this script
        if self._model_registry is not None:
            promoted = self._model_registry.promoted_for_script(line.script)
            if promoted is not None and self._engine_map:
                engine = self._engine_map.get(promoted.model_ref.engine)
                if engine is not None:
                    return (engine,)

        return self._by_script.get(line.script, self._default)


class RegistryAwareRouter(IRouter):
    """Router that integrates an IModelRegistry for promoted model routing.

    Falls back to ScriptRouter rules when no promoted model is registered.
    """

    def __init__(
        self,
        by_script: Mapping[Script, Sequence[IOCREngine]],
        model_registry: IModelRegistry,
        default: Sequence[IOCREngine] = (),
        engine_map: Mapping[str, IOCREngine] | None = None,
    ) -> None:
        self._fallback = ScriptRouter(
            by_script=by_script, default=default, engine_map=engine_map or {}
        )
        self._model_registry = model_registry
        self._engine_map: Mapping[str, IOCREngine] = engine_map or {}

    def route(self, line: OCRLine, context: TenantContext) -> Sequence[IOCREngine]:
        """Route a line, preferring promoted models over defaults."""
        promoted = self._model_registry.promoted_for_script(line.script)
        if promoted is not None and self._engine_map:
            engine = self._engine_map.get(promoted.model_ref.engine)
            if engine is not None:
                return (engine,)
        return self._fallback.route(line, context)


__all__ = [
    "RegistryAwareRouter",
    "ScriptRouter",
]
