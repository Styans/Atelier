"""The CLI end to end on a small show, with a private config and projects folder."""

import importlib.util
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atelier_cli import __version__
from atelier_cli.main import app, parse_track

REPO = Path(__file__).resolve().parents[3]
runner = CliRunner()


def load_fixtures(out: Path) -> dict[str, Path]:
    spec = importlib.util.spec_from_file_location(
        "mf", REPO / "tests" / "fixtures" / "make_fixtures.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    files: dict[str, Path] = module.make_all(out)
    return files


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    plugins = (REPO / "plugins").as_posix()
    (tmp_path / "plugins").mkdir()
    (tmp_path / "atelier.toml").write_text(
        f'[paths]\nprojects_dir = "projects"\nplugin_dirs = ["plugins", "{plugins}"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ATELIER_CONFIG", str(tmp_path / "atelier.toml"))
    monkeypatch.delenv("ATELIER_SHOW", raising=False)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run(*args: str, code: int = 0) -> str:
    result = runner.invoke(app, list(args))
    assert result.exit_code == code, result.output
    return result.output


def test_version_flag_prints_version() -> None:
    assert run("--version").strip() == f"atelier {__version__}"


def test_track_spec_parsing() -> None:
    track = parse_track("builtin.rotate angle_deg=90 axis=z easing=ease_in_out start=1 duration=5")

    assert track.primitive == "builtin.rotate"
    assert track.params == {"angle_deg": 90, "axis": "z"}
    assert (track.start_s, track.duration_s, track.easing) == (1, 5, "ease_in_out")


def test_a_show_from_the_command_line(env: Path) -> None:
    files = load_fixtures(env / "fixtures")

    run("new", "CLI demo", "--drones", "25", "--duration", "120")
    run("music", "set", str(files["music"]))
    assert "m_torus" in run("add-model", "--glb", str(files["torus"]), "--name", "Torus")
    run("add-model", "--glb", str(files["star"]), "--name", "Star")
    run("points", "m_torus")
    run("points", "m_star")
    run("scene", "add", "--model", "m_torus", "--hold", "6")
    run(
        "scene",
        "add",
        "--model",
        "m_star",
        "--hold",
        "8",
        "--track",
        "builtin.rotate angle_deg=60 easing=ease_in_out",
    )
    assert "s_2" in run("scene", "list")
    assert "compiled 25 drones" in run("compile")
    assert "OK" in run("validate")
    assert "skybrush_csv.zip" in run("export", "--exporter", "builtin.csv")
    assert "current" in run("status")
    assert "Compile" in run("history")


def test_errors_are_short_messages(env: Path) -> None:
    out = run("status", code=1)

    assert "no show selected" in out
    assert "Traceback" not in out


def test_stale_after_changing_the_distributor(env: Path) -> None:
    files = load_fixtures(env / "fixtures")
    run("new", "Stale", "--drones", "16")
    run("add-model", "--glb", str(files["star"]))
    run("points", "m_star")

    run("pipeline", "set", "point_distributor", "pointille", "--param", "relax_iterations=3")

    assert "parameters changed" in run("status")


def test_plugin_new_and_test(env: Path) -> None:
    out = run("plugin", "new", "demo_distributor", "--namespace", "demo")
    assert (env / "plugins" / "demo_distributor" / "atelier-plugin.toml").is_file(), out

    listing = run("plugin", "list")
    report = run("plugin", "test", "demo_distributor")

    assert "demo.random" in listing
    assert "[inprocess]" in report and "[subprocess]" in report
    assert "FAIL" not in report
