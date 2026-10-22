"""Pygmalion through the real host in its own venv, on its mock backends (no GPU).

Skipped when Pygmalion's runtime is not found. Paths come from ATELIER_PYGMALION_ROOT
(default C:\\Users\\Beacon\\Repo\\Pygmalion). The real GPU run is marked ``slow``.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from atelier_plugin_host import PluginRegistry
from atelier_plugin_sdk import ImageRef, ImageSetRef, MeshRef

ROOT = Path(os.environ.get("ATELIER_PYGMALION_ROOT", r"C:\Users\Beacon\Repo\Pygmalion"))
PYTHON = ROOT / "runtime" / "venv" / "Scripts" / "python.exe"
PLUGINS = Path(__file__).resolve().parents[2]

pytestmark = [
    pytest.mark.pygmalion,
    pytest.mark.skipif(not PYTHON.is_file(), reason="Pygmalion runtime not found"),
]


def settings() -> dict[str, dict[str, str]]:
    return {
        "pygmalion": {
            "python": str(PYTHON),
            "home": str(ROOT / "runtime"),
            "server_dir": str(ROOT / "server"),
        }
    }


@pytest.fixture(scope="module")
def registry() -> Iterator[PluginRegistry]:
    with PluginRegistry.discover(
        [PLUGINS], use_entry_points=False, plugin_settings=settings()
    ) as reg:
        yield reg


def test_plugin_starts_in_its_own_venv(registry: PluginRegistry) -> None:
    plugin = registry.plugin("pygmalion")

    assert plugin.usable, plugin.error
    assert {c.id for c in plugin.components} == {"pygmalion.images", "pygmalion.mesh"}
    assert plugin.health is not None and "txt2img/mock: ok" in plugin.health.message


def test_mock_images_and_mesh(registry: PluginRegistry, tmp_path: Path) -> None:
    images = registry.resolve("image_generator", "pygmalion.images")
    mesh = registry.resolve("mesh_generator", "pygmalion.mesh")

    concepts = images.invoke(
        {"prompt": "a gold star", "negative": None, "n": 2},
        {"backend": "mock", "width": 256, "height": 256},
        seed=7,
        workdir=tmp_path / "a",
    )
    assert isinstance(concepts, ImageSetRef) and concepts.meta["files"] == [
        "concept_01.png",
        "concept_02.png",
    ]
    with Image.open(concepts.path / "concept_01.png") as image:
        assert image.size == (256, 256)

    result = mesh.invoke(
        {"source": ImageRef(path=concepts.path / "concept_01.png")},
        {"backend": "mock"},
        seed=7,
        workdir=tmp_path / "b",
    )
    assert isinstance(result, MeshRef) and result.path.read_bytes()[:4] == b"glTF"


def test_pygmalion_errors_are_readable(registry: PluginRegistry, tmp_path: Path) -> None:
    images = registry.resolve("image_generator", "pygmalion.images")

    with pytest.raises(Exception, match="промпт"):
        images.invoke(
            {"prompt": " ", "negative": None, "n": 1}, {"backend": "mock"}, seed=1, workdir=tmp_path
        )


@pytest.mark.slow
def test_real_gpu_generation(registry: PluginRegistry, tmp_path: Path) -> None:
    """Z-Image-Turbo + Hunyuan3D-2mini on the GPU: minutes, run by hand (-m slow)."""
    images = registry.resolve("image_generator", "pygmalion.images")
    mesh = registry.resolve("mesh_generator", "pygmalion.mesh")

    concepts = images.invoke(
        {
            "prompt": "a golden eagle with spread wings, flat colours, white background",
            "negative": None,
            "n": 1,
        },
        {"backend": "zimage_turbo"},
        seed=35,
        workdir=tmp_path / "a",
    )
    result = mesh.invoke(
        {"source": ImageRef(path=concepts.path / concepts.meta["files"][0])},
        {"backend": "hunyuan"},
        seed=35,
        workdir=tmp_path / "b",
    )
    assert result.path.stat().st_size > 10_000
