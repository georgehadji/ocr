"""Railway-oriented ``Result`` type.

``Result`` is a *union alias*, not a base class. That distinction is what makes
the type usable: with a base class, ``if isinstance(x, Err): return`` leaves
mypy still seeing ``Result``, so ``x.value`` fails to type-check afterwards and
call sites drift into ``hasattr(x, "value")`` or ``cast`` to silence it — both
of which turn a contract violation into a silent wrong value. As a union,
narrowing works in both branches and those workarounds become unnecessary.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from typing import Callable, Generic, TypeAlias, TypeVar


T = TypeVar("T")
U = TypeVar("U")
# Covariant: a Result carrying a LayoutError *is* a Result carrying a
# PipelineError, since LayoutError subclasses it. Invariance made that false,
# so a stage returning the narrower error type could not be returned from a
# function declaring the wider one — `PipelineOrchestrator.run` returning
# `IDocumentAssembler.assemble`'s Result was rejected on exactly that.
#
# Sound here because Err is a frozen dataclass and `error` is only ever read;
# no method accepts a bare E, so E never appears in a contravariant position.
# T cannot be covariant for the same reason in reverse — `unwrap_or` takes a
# `default: T` parameter.
E = TypeVar("E", covariant=True)
F = TypeVar("F")


class _ResultBase(Generic[T, E]):
    """Shared combinators. Not part of the public API — use ``Result``."""

    def map(self, func: Callable[[T], U]) -> "Result[U, E]":
        if isinstance(self, Ok):
            return Ok(func(self.value))
        return Err(cast("Err[T, E]", self).error)

    def and_then(self, func: Callable[[T], "Result[U, E]"]) -> "Result[U, E]":
        if isinstance(self, Ok):
            return func(self.value)
        return Err(cast("Err[T, E]", self).error)

    def map_err(self, func: Callable[[E], F]) -> "Result[T, F]":
        if isinstance(self, Err):
            return Err(func(self.error))
        return Ok(cast("Ok[T, E]", self).value)

    def unwrap_or(self, default: T) -> T:
        return self.value if isinstance(self, Ok) else default

    def is_ok(self) -> bool:
        return isinstance(self, Ok)

    def is_err(self) -> bool:
        return isinstance(self, Err)


@dataclass(frozen=True, slots=True)
class Ok(_ResultBase[T, E]):
    value: T


@dataclass(frozen=True, slots=True)
class Err(_ResultBase[T, E]):
    error: E


Result: TypeAlias = Ok[T, E] | Err[T, E]
