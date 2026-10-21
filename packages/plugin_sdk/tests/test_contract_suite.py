import importlib
import sys
from pathlib import Path

import pytest

from atelier_plugin_sdk import Plugin
from atelier_plugin_sdk.template import TEMPLATE_ROLES, create_plugin
from atelier_plugin_sdk.testing import LocalInvoker, run_contract_suite


def errors(results: list) -> list:  # type: ignore[type-arg]
    return [r for r in results if not r.ok and r.level == "error"]


def test_suite_passes_for_a_good_component(tmp_path: Path, fixture_plugin: Plugin) -> None:
    component = fixture_plugin.component("point_distributor", "random")

    results = run_contract_suite(LocalInvoker(component), tmp_path)

    assert not errors(results)
    assert any(r.name == "deterministic for a seed" and r.ok for r in results)


def test_suite_fails_a_component_that_breaks_the_contract(
    tmp_path: Path, fixture_plugin: Plugin
) -> None:
    component = fixture_plugin.component("point_distributor", "short")

    results = run_contract_suite(LocalInvoker(component), tmp_path)

    assert errors(results)
    assert "ContractViolation" in errors(results)[0].detail


@pytest.mark.parametrize("role", TEMPLATE_ROLES)
def test_generated_plugins_pass_their_contract(tmp_path: Path, role: str) -> None:
    plugin_id = f"gen_{role}"
    root = create_plugin(tmp_path, plugin_id, role)  # type: ignore[arg-type]
    sys.path.insert(0, str(root / "src"))
    try:
        plugin = importlib.import_module(plugin_id).plugin
        for component in plugin.components:
            assert not errors(run_contract_suite(LocalInvoker(component), tmp_path / "w"))
    finally:
        sys.path.remove(str(root / "src"))
        sys.modules.pop(plugin_id, None)
    assert (root / "atelier-plugin.toml").is_file()
    assert (root / "tests" / f"test_{plugin_id}.py").is_file()


def test_template_refuses_bad_ids_and_existing_folders(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="snake_case"):
        create_plugin(tmp_path, "Bad-Name", "point_distributor")
    create_plugin(tmp_path, "twice", "point_distributor")
    with pytest.raises(FileExistsError):
        create_plugin(tmp_path, "twice", "point_distributor")
