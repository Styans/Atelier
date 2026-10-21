from pathlib import Path

import numpy as np
import pytest

from atelier_domain import fleet_from_preset
from atelier_plugin_sdk import (
    ContractViolation,
    InvalidParams,
    MeshRef,
    Plugin,
    RunContext,
    TransitionPlan,
)
from atelier_plugin_sdk.dispatch import invoke, normalize_params
from atelier_plugin_sdk.rpc.codec import decode, encode
from atelier_plugin_sdk.testing import reference_mesh


@pytest.fixture
def source(tmp_path: Path) -> MeshRef:
    return MeshRef(path=reference_mesh().save(tmp_path / "mesh.npz"))


def ctx(tmp_path: Path) -> RunContext:
    return RunContext(seed=3, workdir=tmp_path / "work")


def test_invoke_runs_the_role_method(
    tmp_path: Path, source: MeshRef, fixture_plugin: Plugin
) -> None:
    plugin = fixture_plugin

    ref = invoke(
        plugin.component("point_distributor", "random"),
        {"source": source, "n": 20},
        {},
        ctx(tmp_path),
    )

    assert len(ref.load()) == 20


def test_invalid_params_are_reported(
    tmp_path: Path, source: MeshRef, fixture_plugin: Plugin
) -> None:
    component = fixture_plugin.component("point_distributor", "random")

    with pytest.raises(InvalidParams):
        invoke(component, {"source": source, "n": 5}, {"area_weighted": "maybe"}, ctx(tmp_path))
    with pytest.raises(InvalidParams, match="missing"):
        invoke(component, {"source": source}, {}, ctx(tmp_path))


def test_contract_violation_is_detected(
    tmp_path: Path, source: MeshRef, fixture_plugin: Plugin
) -> None:
    component = fixture_plugin.component("point_distributor", "short")

    with pytest.raises(ContractViolation, match="expected 10"):
        invoke(component, {"source": source, "n": 10}, {}, ctx(tmp_path))


def test_normalize_fills_defaults(fixture_plugin: Plugin) -> None:
    component = fixture_plugin.component("animation_primitive", "shift")

    assert normalize_params(component, {}) == {"dz": 1.0}


def test_codec_round_trip(tmp_path: Path, source: MeshRef) -> None:
    fleet = fleet_from_preset("conservative", 4)
    plan = TransitionPlan(assignment=np.arange(4), positions=np.zeros((3, 4, 3)), duration_s=0.5)
    value = {
        "mesh": source,
        "fleet": fleet,
        "arr": np.eye(3),
        "plan": plan,
        "out": tmp_path,
        "n": 3,
    }

    decoded = decode(encode(value, tmp_path / "rpc"))

    assert decoded["mesh"] == source
    assert decoded["fleet"] == fleet
    assert np.array_equal(decoded["arr"], np.eye(3))
    assert np.array_equal(decoded["plan"].assignment, np.arange(4))
    assert decoded["out"] == tmp_path
    assert decoded["n"] == 3
