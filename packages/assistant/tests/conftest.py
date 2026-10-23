from collections.abc import Iterator
from pathlib import Path

import pytest

from atelier_jobs import ChatStore, JobQueue
from atelier_plugin_host import PluginRegistry
from atelier_show_engine.config import AtelierConfig
from atelier_show_engine.service import ShowService

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def registry() -> Iterator[PluginRegistry]:
    # The core's own plugins are enough here; Pygmalion would start a separate Python.
    with PluginRegistry.discover(
        [REPO / "plugins"], use_entry_points=False, disabled={"pygmalion"}
    ) as reg:
        yield reg


@pytest.fixture
def service(tmp_path: Path, registry: PluginRegistry) -> ShowService:
    return ShowService(AtelierConfig(root=tmp_path), registry, author="ui")


@pytest.fixture
def chat(tmp_path: Path) -> ChatStore:
    return ChatStore(JobQueue(tmp_path / "atelier.db"))
