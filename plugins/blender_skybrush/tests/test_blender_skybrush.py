import json
import os
from pathlib import Path

import pytest

from atelier_blender_skybrush import Blend, BlendParams, storyboard
from atelier_plugin_sdk import CompiledShowRef, PluginError, RunContext
from atelier_plugin_sdk.testing import reference_compiled_show, reference_fleet

BLENDER = Path(
    os.environ.get(
        "ATELIER_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe"
    )
)


def compiled_ref(tmp_path: Path) -> CompiledShowRef:
    show = reference_compiled_show(drones=9, frames=91)
    show.meta["fleet"] = reference_fleet(9).model_dump(mode="json")
    return CompiledShowRef(path=show.save(tmp_path / "show.npz"))


def test_storyboard_has_takeoff_holds_and_landing(tmp_path: Path) -> None:
    entries, formations = storyboard(compiled_ref(tmp_path).load())

    assert [e["purpose"] for e in entries] == ["TAKEOFF", "SHOW", "LANDING"]
    assert set(formations) == {e["positions_key"] for e in entries}


def test_missing_blender_is_a_clear_error(tmp_path: Path) -> None:
    ctx = RunContext(seed=0, workdir=tmp_path / "w")

    with pytest.raises(PluginError, match="blender_exe"):
        Blend().export(ctx, compiled_ref(tmp_path), tmp_path / "out", BlendParams())


@pytest.mark.blender
@pytest.mark.skipif(not BLENDER.is_file(), reason="Blender not installed")
def test_builds_a_skybrush_blend(tmp_path: Path) -> None:
    ctx = RunContext(seed=0, workdir=tmp_path / "w", config={"blender_exe": str(BLENDER)})

    result = Blend().export(ctx, compiled_ref(tmp_path), tmp_path / "out", BlendParams())

    report = json.loads(result.files[1].read_text(encoding="utf-8"))
    assert result.files[0].stat().st_size > 0
    assert report["drones"] == 9
    assert [e["purpose"] for e in report["storyboard"]] == ["TAKEOFF", "SHOW", "LANDING"]
    assert report["max_error_m"] < 0.05
    assert all(v["skybrush_min_distance_m"] >= 4.0 - 1e-3 for v in report["verify"][1:])
