import math
from pathlib import Path

import numpy as np
import pytest

from atelier_domain import (
    FlightSettings,
    ModelCard,
    RegionBox,
    RegionPlaneSplit,
    Scene,
    fleet_from_preset,
)
from atelier_show_engine.geometry import (
    ease,
    grid_positions,
    nearest_spacing,
    place,
    region_mask,
    vertical_move,
)
from atelier_show_engine.importing import load_mesh_file, probe_audio

FLEET = fleet_from_preset("conservative", 16)


def card(**kw: object) -> ModelCard:
    return ModelCard(id="m", name="m", **kw)  # type: ignore[arg-type]


def scene(**kw: object) -> Scene:
    return Scene(id="s", title="s", model_id="m", hold_s=1, **kw)  # type: ignore[arg-type]


def test_auto_size_reaches_the_safe_spacing() -> None:
    points = np.random.default_rng(0).random((40, 3))

    placed = place(points, card(), scene(), FLEET, FlightSettings(), "-Y")

    assert placed.spacing_m == pytest.approx(math.sqrt(2) * FLEET.min_distance_m)
    world = placed.to_world(placed.local)
    assert world[:, 2].min() == pytest.approx(15.0)


def test_target_height_and_offset() -> None:
    points = np.array([[0, 0, 0], [1, 0, 2], [0, 1, 1]], dtype=float)

    placed = place(
        points, card(target_height_m=30), scene(offset_m=(5, 0, 10)), FLEET, FlightSettings(), "-Y"
    )

    world = placed.to_world(placed.local)
    assert np.ptp(world[:, 2]) == pytest.approx(30)
    assert world[:, 2].min() == pytest.approx(25)


def test_figure_turns_to_the_audience() -> None:
    front = np.array([[0, -1, 0], [0, 1, 0], [1, 0, 0]], dtype=float)  # [0] is the front tip

    placed = place(front, card(), scene(), FLEET, FlightSettings(), "+X")

    world = placed.to_world(placed.local)
    assert world[0, 0] > world[1, 0]  # the front now points to +X


def test_regions() -> None:
    local = np.array([[-1, 0, 0], [1, 0, 0], [0, 0, 3]], dtype=float)

    assert region_mask(RegionPlaneSplit(normal=(1, 0, 0)), local).tolist() == [False, True, True]
    box = RegionBox(min=(-2, -1, -1), max=(0, 1, 1))
    assert region_mask(box, local).tolist() == [True, False, False]


def test_easing_ends() -> None:
    p = np.array([0.0, 0.5, 1.0])

    for kind in ("linear", "ease_in_out", "sine"):
        assert np.allclose(ease(kind, p)[[0, 2]], [0, 1])  # type: ignore[arg-type]


def test_grid_is_centred_and_spaced() -> None:
    grid = grid_positions(10, 3.0, (0, 0, 0))

    assert len(grid) == 10
    assert nearest_spacing(grid) == pytest.approx(3.0)
    assert np.allclose(grid[:, 2], 0)


def test_vertical_move_respects_limits() -> None:
    start = grid_positions(4, 3.0, (0, 0, 0))

    climb = vertical_move(start, 6.0, 1.6, 2.0, 30)

    v = np.diff(climb[:, 0, 2]) * 30
    assert v.max() <= 1.6
    assert np.diff(v).max() * 30 <= 2.0
    assert climb[-1, 0, 2] == pytest.approx(6.0)


def test_glb_import_turns_y_up_into_z_up(fixture_files: dict[str, Path]) -> None:
    mesh = load_mesh_file(fixture_files["torus"])

    extent = np.ptp(mesh.vertices, axis=0)
    assert extent[0] == pytest.approx(2.6, abs=0.05)  # ring diameter along X
    assert extent[2] == pytest.approx(2.6, abs=0.05)  # standing up along Z
    assert extent[1] == pytest.approx(0.6, abs=0.05)  # thickness towards the audience
    assert mesh.vertex_colors is not None


def test_star_keeps_its_gold(fixture_files: dict[str, Path]) -> None:
    mesh = load_mesh_file(fixture_files["star"])

    assert mesh.vertex_colors is not None
    assert np.allclose(mesh.vertex_colors[0], [1.0, 200 / 255, 40 / 255], atol=1e-3)


def test_audio_probe(fixture_files: dict[str, Path]) -> None:
    duration, rate = probe_audio(fixture_files["music"])

    assert duration == pytest.approx(60.0)
    assert rate == 8000
