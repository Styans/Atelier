import importlib.util
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest

from atelier_jobs import JobQueue
from atelier_plugin_host import PluginRegistry
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.service import ShowService
from atelier_worker.worker import Worker

REPO = Path(__file__).resolve().parents[3]

# Stand-in generators: concept PNGs and a GLB mesh, no GPU needed.
FAKE_GENERATORS = textwrap.dedent(
    '''
    import numpy as np
    import trimesh
    from PIL import Image
    from pydantic import BaseModel

    from atelier_plugin_sdk import (
        Health, ImageGenerator, ImageSetRef, MeshGenerator, MeshRef, Plugin,
    )

    class Params(BaseModel):
        pass

    class Concepts(ImageGenerator[Params]):
        """Flat colour squares."""

        name = "concepts"
        Params = Params

        def generate(self, ctx, prompt, negative, n, params):
            folder = ctx.output_path("images")
            folder.mkdir(parents=True, exist_ok=True)
            names = []
            for i in range(n):
                name = f"concept_{i}.png"
                Image.new("RGB", (16, 16), (40 * i, 120, 200)).save(folder / name)
                names.append(name)
            return ImageSetRef(path=folder, meta={"files": names})

    class Sculptor(MeshGenerator[Params]):
        """Always an icosphere."""

        name = "sculptor"
        Params = Params

        def generate(self, ctx, source, params):
            mesh = trimesh.creation.icosphere(subdivisions=2)
            path = ctx.output_path("mesh.glb")
            mesh.export(path, file_type="glb")
            return MeshRef(path=path)

    plugin = Plugin(components=[Concepts(), Sculptor()], health_check=Health.ok)
    '''
)


def _fixtures_module():  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(
        "mf", REPO / "tests" / "fixtures" / "make_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def fixture_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    files: dict[str, Path] = _fixtures_module().make_all(tmp_path_factory.mktemp("fixtures"))
    return files


@pytest.fixture(scope="session")
def plugin_dirs(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    extra = tmp_path_factory.mktemp("plugins")
    root = extra / "fake_generators"
    (root / "src" / "fake_generators").mkdir(parents=True)
    (root / "src" / "fake_generators" / "__init__.py").write_text(FAKE_GENERATORS, encoding="utf-8")
    (root / "atelier-plugin.toml").write_text(
        'id = "fake_generators"\nname = "Fake"\nversion = "0.1.0"\napi_version = "1"\n'
        'namespace = "fake"\nroles = ["image_generator", "mesh_generator"]\n'
        'entry = "fake_generators:plugin"\n',
        encoding="utf-8",
    )
    return [extra, REPO / "plugins"]


@pytest.fixture(scope="session")
def registry(plugin_dirs: list[Path]) -> Iterator[PluginRegistry]:
    with PluginRegistry.discover(plugin_dirs, use_entry_points=False) as reg:
        yield reg


@pytest.fixture
def service(tmp_path: Path, registry: PluginRegistry) -> ShowService:
    return ShowService(AtelierConfig(root=tmp_path), registry, author="worker")


@pytest.fixture
def queue(tmp_path: Path) -> JobQueue:
    return JobQueue(tmp_path / ".atelier" / "atelier.db")


@pytest.fixture
def worker(service: ShowService, queue: JobQueue) -> Worker:
    return Worker(service, queue)
