"""The state-machine building block, the registry contract, and the generated catalog."""

from __future__ import annotations

import importlib.util
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest

from omniocr.composition.state_machines import ALL_MACHINES
from omniocr.domain.result import Err, Ok
from omniocr.domain.state import IllegalTransition, MachineSpec, machine, transition

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "gen_state_docs.py"


class Light(StrEnum):
    RED = "red"
    GREEN = "green"
    BROKEN = "broken"


_TABLE = MappingProxyType(
    {
        Light.RED: frozenset({Light.GREEN, Light.BROKEN}),
        Light.GREEN: frozenset({Light.RED, Light.BROKEN}),
        Light.BROKEN: frozenset[Light](),
    }
)


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_state_docs", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_declared_move_is_allowed() -> None:
    assert transition("light", _TABLE, Light.RED, Light.GREEN) == Ok(Light.GREEN)


def test_an_undeclared_move_is_refused_with_both_states_named() -> None:
    result = transition("light", _TABLE, Light.BROKEN, Light.RED)
    assert result == Err(IllegalTransition("light", "broken", "red"))
    assert str(IllegalTransition("light", "broken", "red")) == (
        "light: cannot go from 'broken' to 'red'"
    )


def test_a_state_with_no_row_refuses_every_move() -> None:
    assert isinstance(transition("light", {}, Light.RED, Light.GREEN), Err)


def test_members_keep_their_string_values_on_the_wire() -> None:
    """The reason for StrEnum: f-strings and comparisons give the old literal."""
    assert Light("green") is Light.GREEN
    assert Light.GREEN == "green"
    assert f"{Light.GREEN}" == "green"


def test_machine_returns_a_plain_spec() -> None:
    spec = machine("light", "tests.light", Light, Light.RED, _TABLE)
    assert spec.states == ("red", "green", "broken")
    assert spec.initial == "red"
    assert spec.terminal == ("broken",)
    assert spec.transitions["red"] == frozenset({"green", "broken"})


def test_machine_rejects_a_state_without_a_row() -> None:
    table = {Light.RED: frozenset({Light.GREEN}), Light.GREEN: frozenset[Light]()}
    with pytest.raises(ValueError, match="no transition row for broken"):
        machine("light", "tests.light", Light, Light.RED, table)


def test_machine_rejects_a_state_nothing_reaches() -> None:
    table = {
        Light.RED: frozenset({Light.GREEN}),
        Light.GREEN: frozenset[Light](),
        Light.BROKEN: frozenset[Light](),
    }
    with pytest.raises(ValueError, match="unreachable from 'red': broken"):
        machine("light", "tests.light", Light, Light.RED, table)


def test_an_external_state_is_exempt_from_reachability() -> None:
    """A state entered by expiry, not by a caller, is declared rather than faked."""
    table = {
        Light.RED: frozenset({Light.GREEN}),
        Light.GREEN: frozenset[Light](),
        Light.BROKEN: frozenset[Light](),
    }
    spec = machine(
        "light", "tests.light", Light, Light.RED, table, external=frozenset({Light.BROKEN})
    )
    assert spec.external == frozenset({"broken"})


@pytest.mark.parametrize("spec", ALL_MACHINES, ids=lambda spec: spec.name)
def test_every_registered_machine_is_well_formed(spec: MachineSpec) -> None:
    assert spec.initial in spec.states
    assert set(spec.transitions) == set(spec.states)
    for targets in spec.transitions.values():
        assert targets <= set(spec.states)


def test_registered_machine_names_are_unique() -> None:
    names = [spec.name for spec in ALL_MACHINES]
    assert len(names) == len(set(names))


def test_the_committed_catalog_matches_the_registry() -> None:
    """The same check CI runs: the doc is what the code declares."""
    generator = _load_generator()
    assert generator.main(["--check"]) == 0


def test_the_catalog_renders_a_diagram_and_a_table_per_machine() -> None:
    generator = _load_generator()
    spec = machine("light", "tests.light", Light, Light.RED, _TABLE)
    text: str = generator.render([spec])
    assert "## light" in text
    assert "stateDiagram-v2" in text
    assert "[*] --> red" in text
    assert "| `broken` | (terminal) |" in text
    assert "No machines are registered yet." in generator.render([])
