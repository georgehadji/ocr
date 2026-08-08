"""Script-based router with optional model registry integration.

Selects engines from immutable script rules with a safe default. Optionally
consults an ``IModelRegistry`` to override with a promoted per-script model
when one is registered.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Sequence

from omniocr.domain.models import OCRLine, Script, TenantContext
from omniocr.domain.training import PromotedModel
from omniocr.ports.interfaces import IOCREngine, IModelRegistry, IRouter

EngineFactory = Callable[[str, Path], IOCREngine]
"""Build an engine of a given family bound to a specific checkpoint."""


class _PromotedResolver:
    """Resolve a promoted model to a runnable engine, loading each checkpoint once.

    Routing happens per line, but constructing an engine loads model weights,
    so resolved engines are cached by checkpoint. Without the cache a promoted
    model would be re-read from disk for every line on the page.
    """

    def __init__(
        self,
        engine_map: Mapping[str, IOCREngine] | None,
        engine_factory: EngineFactory | None,
    ) -> None:
        self._engine_map: Mapping[str, IOCREngine] = engine_map or {}
        self._engine_factory = engine_factory
        self._cache: dict[Path, IOCREngine] = {}

    def resolve(self, promoted: PromotedModel) -> IOCREngine | None:
        """Return an engine running the promoted checkpoint, or ``None``.

        Without a factory this degrades to the engine-family instance, which
        runs the *parent* weights — promotion then has no effect at inference.
        A caller that wants fine-tuned models actually used must wire an
        ``engine_factory``.
        """
        if self._engine_factory is not None:
            checkpoint = promoted.checkpoint
            cached = self._cache.get(checkpoint)
            if cached is None:
                cached = self._engine_factory(promoted.model_ref.engine, checkpoint)
                self._cache[checkpoint] = cached
            return cached
        return self._engine_map.get(promoted.model_ref.engine)


class ScriptRouter(IRouter):
    """Select engines from immutable script rules with a safe default.

    When a ``model_registry`` is provided, the router checks for promoted
    models per script and routes to them preferentially, falling back to
    the default engine mapping.

    ``engine_factory`` builds an engine bound to the promoted *checkpoint*.
    ``engine_map`` (family name → instance) is only the degraded fallback for
    callers that have not wired a factory; it cannot run fine-tuned weights.
    """

    def __init__(
        self,
        by_script: Mapping[Script, Sequence[IOCREngine]],
        default: Sequence[IOCREngine] = (),
        model_registry: IModelRegistry | None = None,
        engine_map: Mapping[str, IOCREngine] | None = None,
        engine_factory: EngineFactory | None = None,
    ) -> None:
        self._by_script = {script: tuple(engines) for script, engines in by_script.items()}
        self._default = tuple(default)
        self._model_registry = model_registry
        self._resolver = _PromotedResolver(engine_map, engine_factory)

    def route(self, line: OCRLine, context: TenantContext) -> Sequence[IOCREngine]:
        """Select engines for a line, consulting the model registry first."""
        if self._model_registry is not None:
            promoted = self._model_registry.promoted_for_script(line.script)
            if promoted is not None:
                engine = self._resolver.resolve(promoted)
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
        engine_factory: EngineFactory | None = None,
    ) -> None:
        self._fallback = ScriptRouter(
            by_script=by_script, default=default, engine_map=engine_map or {}
        )
        self._model_registry = model_registry
        self._resolver = _PromotedResolver(engine_map, engine_factory)

    def route(self, line: OCRLine, context: TenantContext) -> Sequence[IOCREngine]:
        """Route a line, preferring promoted models over defaults."""
        promoted = self._model_registry.promoted_for_script(line.script)
        if promoted is not None:
            engine = self._resolver.resolve(promoted)
            if engine is not None:
                return (engine,)
        return self._fallback.route(line, context)


__all__ = [
    "EngineFactory",
    "RegistryAwareRouter",
    "ScriptRouter",
]
