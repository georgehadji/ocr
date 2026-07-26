from __future__ import annotations

from dataclasses import dataclass
from typing import cast
from typing import Callable, Generic, TypeVar


T = TypeVar("T")
U = TypeVar("U")
E = TypeVar("E")
F = TypeVar("F")


class Result(Generic[T, E]):
    def map(self, func: Callable[[T], U]) -> "Result[U, E]":
        if isinstance(self, Ok):
            return Ok(func(self.value))
        return Err(cast(Err[T, E], self).error)

    def and_then(self, func: Callable[[T], "Result[U, E]"]) -> "Result[U, E]":
        if isinstance(self, Ok):
            return func(self.value)
        return Err(cast(Err[T, E], self).error)

    def map_err(self, func: Callable[[E], F]) -> "Result[T, F]":
        if isinstance(self, Err):
            return Err(func(self.error))
        return Ok(cast(Ok[T, E], self).value)

    def unwrap_or(self, default: T) -> T:
        return self.value if isinstance(self, Ok) else default

    def is_ok(self) -> bool:
        return isinstance(self, Ok)

    def is_err(self) -> bool:
        return isinstance(self, Err)


@dataclass(frozen=True, slots=True)
class Ok(Result[T, E]):
    value: T


@dataclass(frozen=True, slots=True)
class Err(Result[T, E]):
    error: E
