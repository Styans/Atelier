import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

from atelier_plugin_host import PluginRegistry
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.service import ShowService

REPO = Path(__file__).resolve().parents[3]


def _fixtures_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "make_fixtures", REPO / "tests" / "fixtures" / "make_fixtures.py"
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
def registry() -> Iterator[PluginRegistry]:
    with PluginRegistry.discover([REPO / "plugins"], use_entry_points=False) as reg:
        yield reg


@pytest.fixture
def service(tmp_path: Path, registry: PluginRegistry) -> ShowService:
    config = AtelierConfig(root=tmp_path)
    return ShowService(config, registry)
