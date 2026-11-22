"""Explicit lifecycles: a state enum, a table of legal moves, one guard.

A lifecycle that lives in bare strings or in several booleans that only make
sense together is a state machine nobody declared. This module is the small,
dependency-free shape every such lifecycle is moved into:

* a ``StrEnum`` of the states (members equal their old string values, so wire
  formats and persisted rows are unchanged);
* a ``Mapping[State, frozenset[State]]`` of the transitions that are allowed;
* ``transition()``, which says whether one move is allowed, as a ``Result``;
* ``machine()``, which checks the table is complete and reachable and returns a
  plain ``MachineSpec`` for the generated catalog (``docs/STATE_MACHINES.md``).

``StrEnum`` rather than ``(str, Enum)``: on Python 3.11+ an f-string of a
``(str, Enum)`` member prints ``Class.MEMBER``, not the value, so swapping one in
for a string would change log lines and error messages.

The domain stays pure: nothing here logs. A caller that moves an entity logs one
``state_transition`` line itself.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeVar

from omniocr.domain.result import Err, Ok, Result

S = TypeVar("S", bound=StrEnum)


@dataclass(frozen=True, slots=True)
class IllegalTransition:
    """A move the entity's table does not allow. A value, not an exception."""

    entity: str
    source: str
    target: str

    def __str__(self) -> str:
        return f"{self.entity}: cannot go from '{self.source}' to '{self.target}'"


def transition(
    entity: str, table: Mapping[S, frozenset[S]], source: S, target: S
) -> Result[S, IllegalTransition]:
    """``Ok(target)`` when ``source -> target`` is in ``table``, else ``Err``."""
    if target in table.get(source, frozenset()):
        return Ok(target)
    return Err(IllegalTransition(entity, str(source), str(target)))


@dataclass(frozen=True, slots=True)
class MachineSpec:
    """A declared lifecycle as plain strings: what docs and agents read."""

    name: str
    owner: str  # dotted path of the module that owns the enum and the table
    states: tuple[str, ...]
    initial: str
    transitions: Mapping[str, frozenset[str]]
    external: frozenset[str] = frozenset()  # entered from outside, not by a transition

    @property
    def terminal(self) -> tuple[str, ...]:
        return tuple(state for state in self.states if not self.transitions[state])


def machine(
    name: str,
    owner: str,
    states: type[S],
    initial: S,
    transitions: Mapping[S, frozenset[S]],
    external: frozenset[S] = frozenset(),
) -> MachineSpec:
    """Check a transition table and return its spec.

    Raises ``ValueError`` when a state has no row (a forgotten state is how a
    machine stays implicit) or cannot be reached from ``initial``. ``external``
    names states entered from outside the machine, such as an expiry that no
    caller triggers; they are exempt from the reachability check.
    """
    members = tuple(states)
    missing = [str(member) for member in members if member not in transitions]
    if missing:
        raise ValueError(f"{name}: no transition row for {', '.join(missing)}")
    seen = {initial} | set(external)
    queue = deque(seen)
    while queue:
        for target in transitions[queue.popleft()]:
            if target not in seen:
                seen.add(target)
                queue.append(target)
    unreachable = [str(member) for member in members if member not in seen]
    if unreachable:
        raise ValueError(f"{name}: unreachable from '{initial}': {', '.join(unreachable)}")
    return MachineSpec(
        name=name,
        owner=owner,
        states=tuple(str(member) for member in members),
        initial=str(initial),
        transitions={
            str(state): frozenset(str(target) for target in transitions[state]) for state in members
        },
        external=frozenset(str(state) for state in external),
    )


__all__ = ["IllegalTransition", "MachineSpec", "machine", "transition"]
