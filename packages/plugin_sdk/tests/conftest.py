import importlib.util
import sys
from pathlib import Path

import pytest

from atelier_plugin_sdk import Plugin

HERE = Path(__file__).parent


@pytest.fixture
def fixture_plugin() -> Plugin:
    """The test plugin from ``fixture_plugin.py``, imported in-process."""
    spec = importlib.util.spec_from_file_location("fixture_plugin", HERE / "fixture_plugin.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["fixture_plugin"] = module
    spec.loader.exec_module(module)
    plugin: Plugin = module.plugin
    return plugin
