import importlib.util
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atelier_api.app import create_app
from atelier_jobs import JobQueue
from atelier_plugin_host import PluginRegistry
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.service import ShowService
from atelier_show_engine.store import COMMIT_LISTENERS
from atelier_worker.worker import Worker

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def fixture_files(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    spec = importlib.util.spec_from_file_location(
        "mf", REPO / "tests" / "fixtures" / "make_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files: dict[str, Path] = module.make_all(tmp_path_factory.mktemp("fixtures"))
    return files


@pytest.fixture(scope="session")
def registry() -> Iterator[PluginRegistry]:
    with PluginRegistry.discover([REPO / "plugins"], use_entry_points=False) as reg:
        yield reg


@pytest.fixture
def env(tmp_path: Path, registry: PluginRegistry) -> Iterator[tuple[TestClient, Worker]]:
    config = AtelierConfig(root=tmp_path)
    queue = JobQueue(config.cache_dir / "atelier.db")
    service = ShowService(config, registry, author="ui")
    app = create_app(config, service=service, queue=queue, web_dist=tmp_path / "no-dist")
    worker = Worker(ShowService(config, registry, author="worker"), queue)
    with TestClient(app) as client:
        yield client, worker
    COMMIT_LISTENERS.clear()
