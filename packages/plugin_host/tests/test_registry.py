import os
import shutil
import textwrap
import uuid
from pathlib import Path

import pytest

from atelier_plugin_host import ComponentNotFound, PluginRegistry, SubprocessRunner
from atelier_plugin_sdk import MeshRef, PluginError, PointSetRef
from atelier_plugin_sdk.template import create_plugin
from atelier_plugin_sdk.testing import reference_mesh, run_contract_suite

REPO = Path(__file__).resolve().parents[3]

RANDOM_SOURCE = textwrap.dedent(
    """
    import os
    import time

    import numpy as np
    from pydantic import BaseModel

    from atelier_plugin_sdk import Health, Plugin, PointDistributor, PointSet

    class Params(BaseModel):
        sleep_s: float = 0.0

    class Random(PointDistributor[Params]):
        \"\"\"Random points; can be told to sleep.\"\"\"

        name = "random"
        Params = Params

        def distribute(self, ctx, source, n, params):
            time.sleep(params.sleep_s)
            mesh = source.load()
            rng = ctx.rng()
            tri = rng.integers(0, len(mesh.faces), n)
            bary = np.tile([1 / 3, 1 / 3, 1 / 3], (n, 1))
            points = PointSet(
                positions=np.einsum("nk,nkd->nd", bary, mesh.triangles()[tri]),
                normals=mesh.face_normals()[tri],
                colors=np.ones((n, 3)),
                tri_index=tri,
                barycentric=bary,
                meta={"pid": os.getpid()},
            )
            return ctx.save(points, "points.npz")

    plugin = Plugin(components=[Random()], health_check=HEALTH)
    """
)


def uid() -> str:
    return f"p{uuid.uuid4().hex[:8]}"


def write_plugin(
    plugins: Path,
    plugin_id: str,
    *,
    source: str = RANDOM_SOURCE,
    health: str = "Health.ok",
    runtime: str = "inprocess",
    namespace: str | None = None,
    api_version: str = "1",
    command: list[str] | None = None,
) -> Path:
    root = plugins / plugin_id
    (root / "src" / plugin_id).mkdir(parents=True)
    (root / "src" / plugin_id / "__init__.py").write_text(
        source.replace("HEALTH", health), encoding="utf-8"
    )
    lines = [
        f'id = "{plugin_id}"',
        f'name = "{plugin_id}"',
        'version = "0.1.0"',
        f'api_version = "{api_version}"',
        'roles = ["point_distributor"]',
        f'runtime = "{runtime}"',
        f'entry = "{plugin_id}:plugin"',
    ]
    if namespace:
        lines.append(f'namespace = "{namespace}"')
    if command:
        lines.append("command = [" + ", ".join(f'"{part}"' for part in command) + "]")
    (root / "atelier-plugin.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return root


@pytest.fixture
def mesh_ref(tmp_path: Path) -> MeshRef:
    return MeshRef(path=reference_mesh().save(tmp_path / "mesh.npz"))


def discover(plugins: Path, **kwargs: object) -> PluginRegistry:
    return PluginRegistry.discover([plugins], use_entry_points=False, **kwargs)  # type: ignore[arg-type]


def test_template_plugin_is_found_and_resolved(tmp_path: Path, mesh_ref: MeshRef) -> None:
    plugin_id = uid()
    create_plugin(tmp_path / "plugins", plugin_id, "point_distributor", namespace=plugin_id)

    with discover(tmp_path / "plugins") as registry:
        by_id = registry.resolve("point_distributor", f"{plugin_id}.random")
        by_plugin = registry.resolve("point_distributor", plugin_id)
        result = by_id.invoke({"source": mesh_ref, "n": 25}, {}, seed=1, workdir=tmp_path / "w")

    assert by_plugin.info == by_id.info
    assert isinstance(result, PointSetRef)
    assert len(result.load()) == 25


def test_a_broken_plugin_does_not_break_the_others(tmp_path: Path) -> None:
    good, bad = uid(), uid()
    write_plugin(tmp_path, good)
    write_plugin(tmp_path, bad, source="raise ImportError('torch is missing')\n")

    with discover(tmp_path) as registry:
        assert registry.plugin(good).usable
        assert registry.plugin(bad).status == "error"
        assert "torch is missing" in (registry.plugin(bad).error or "")
        with pytest.raises(ComponentNotFound, match="torch is missing"):
            registry.resolve("point_distributor", bad)


def test_failing_health_check_disables_the_plugin(tmp_path: Path) -> None:
    plugin_id = uid()
    write_plugin(tmp_path, plugin_id, health="lambda: Health.error('no GPU')")

    with discover(tmp_path) as registry:
        assert registry.plugin(plugin_id).status == "error"
        assert "no GPU" in (registry.plugin(plugin_id).error or "")
        assert not registry.components("point_distributor")


def test_duplicate_component_ids_are_rejected(tmp_path: Path) -> None:
    first, second = sorted([uid(), uid()])
    write_plugin(tmp_path, first, namespace="shared")
    write_plugin(tmp_path, second, namespace="shared")

    with discover(tmp_path) as registry:
        assert registry.plugin(first).usable
        assert "already provided" in (registry.plugin(second).error or "")


def test_unsupported_api_version(tmp_path: Path) -> None:
    plugin_id = uid()
    write_plugin(tmp_path, plugin_id, api_version="2")

    with discover(tmp_path) as registry:
        assert "api_version 2" in (registry.plugin(plugin_id).error or "")


def test_unknown_component_lists_alternatives(tmp_path: Path) -> None:
    plugin_id = uid()
    write_plugin(tmp_path, plugin_id)

    with (
        discover(tmp_path) as registry,
        pytest.raises(ComponentNotFound, match=f"{plugin_id}.random"),
    ):
        registry.resolve("point_distributor", "nope")


def test_subprocess_plugin_runs_in_another_process(tmp_path: Path, mesh_ref: MeshRef) -> None:
    plugin_id = uid()
    write_plugin(tmp_path / "plugins", plugin_id, runtime="subprocess")

    with discover(tmp_path / "plugins") as registry:
        handle = registry.resolve("point_distributor", plugin_id)
        result = handle.invoke({"source": mesh_ref, "n": 10}, {}, seed=2, workdir=tmp_path / "w")
        checks = run_contract_suite(handle, tmp_path / "contract")

    assert result.load().meta["pid"] != os.getpid()
    assert not [c for c in checks if not c.ok and c.level == "error"]


def test_subprocess_timeout_kills_the_process(tmp_path: Path, mesh_ref: MeshRef) -> None:
    plugin_id = uid()
    write_plugin(tmp_path / "plugins", plugin_id, runtime="subprocess")

    with discover(tmp_path / "plugins") as registry:
        handle = registry.resolve("point_distributor", plugin_id)
        runner = handle.runner
        assert isinstance(runner, SubprocessRunner)
        runner.call_timeout_s = 1.0
        with pytest.raises(PluginError, match="timed out"):
            handle.invoke({"source": mesh_ref, "n": 5}, {"sleep_s": 30}, seed=1, workdir=tmp_path)
        assert not runner.started
        result = handle.invoke({"source": mesh_ref, "n": 5}, {}, seed=1, workdir=tmp_path / "w")
        assert len(result.load()) == 5  # restarted on demand


def test_cache_lists_subprocess_plugins_without_starting_them(
    tmp_path: Path, mesh_ref: MeshRef
) -> None:
    plugin_id = uid()
    write_plugin(tmp_path / "plugins", plugin_id, runtime="subprocess")
    cache = tmp_path / "cache.json"
    discover(tmp_path / "plugins", cache_path=cache).close()

    with discover(tmp_path / "plugins", cache_path=cache) as registry:
        runner = registry.plugin(plugin_id).runner
        assert isinstance(runner, SubprocessRunner)
        assert [c.name for c in registry.components("point_distributor")] == ["random"]
        assert not runner.started
        registry.resolve("point_distributor", plugin_id).invoke(
            {"source": mesh_ref, "n": 3}, {}, seed=1, workdir=tmp_path / "w"
        )
        assert runner.started


def test_native_import_in_a_call_does_not_wait_for_stdin(tmp_path: Path, mesh_ref: MeshRef) -> None:
    # Started from the cache, the plugin's first scipy import happens in the call thread. scipy's
    # BLAS DLL queries the standard input while loading; on Windows that waited for the server's
    # pending read of the protocol pipe: a deadlock that hung real Pygmalion runs.
    plugin_id = uid()
    source = RANDOM_SOURCE.replace(
        "time.sleep(params.sleep_s)", "time.sleep(params.sleep_s)\n        import scipy.linalg"
    )
    write_plugin(tmp_path / "plugins", plugin_id, source=source, runtime="subprocess")
    cache = tmp_path / "cache.json"
    discover(tmp_path / "plugins", cache_path=cache).close()

    with discover(tmp_path / "plugins", cache_path=cache) as registry:
        handle = registry.resolve("point_distributor", plugin_id)
        runner = handle.runner
        assert isinstance(runner, SubprocessRunner)
        runner.call_timeout_s = 60.0
        result = handle.invoke({"source": mesh_ref, "n": 4}, {}, seed=1, workdir=tmp_path / "w")

    assert len(result.load()) == 4


def test_union_schema_embeds_each_component(tmp_path: Path) -> None:
    plugin_id = uid()
    create_plugin(tmp_path, plugin_id, "animation_primitive", namespace=plugin_id)

    with discover(tmp_path) as registry:
        schema = registry.union_schema("animation_primitive", "primitive")

    variant = schema["oneOf"][0]
    assert variant["properties"]["primitive"] == {"const": f"{plugin_id}.lift"}
    assert "height_m" in variant["properties"]["params"]["properties"]
    assert schema["discriminator"] == {"propertyName": "primitive"}


@pytest.mark.slow
@pytest.mark.skipif(shutil.which("uv") is None and "UV" not in os.environ, reason="needs uv")
def test_plugin_in_its_own_venv(tmp_path: Path, mesh_ref: MeshRef) -> None:
    """Prototype of the Pygmalion setup: the plugin runs in a separate uv environment."""
    plugin_id = uid()
    root = write_plugin(
        tmp_path / "plugins",
        plugin_id,
        runtime="subprocess",
        command=[
            "{uv}",
            "run",
            "--project",
            "{plugin_dir}",
            "--quiet",
            "python",
            "-m",
            "atelier_plugin_sdk.serve",
            "--entry",
            "{entry}",
            "--path",
            "{src}",
        ],
    )
    sdk = (REPO / "packages" / "plugin_sdk").as_posix()
    domain = (REPO / "packages" / "domain").as_posix()
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{plugin_id}"
            version = "0.1.0"
            requires-python = ">=3.12"
            dependencies = ["atelier-plugin-sdk", "atelier-domain"]

            [tool.uv]
            package = false

            [tool.uv.sources]
            atelier-plugin-sdk = {{ path = "{sdk}", editable = true }}
            atelier-domain = {{ path = "{domain}", editable = true }}
            """
        ),
        encoding="utf-8",
    )

    with discover(tmp_path / "plugins") as registry:
        plugin = registry.plugin(plugin_id)
        assert plugin.usable, plugin.error
        result = registry.resolve("point_distributor", plugin_id).invoke(
            {"source": mesh_ref, "n": 7}, {}, seed=1, workdir=tmp_path / "w"
        )

    assert len(result.load()) == 7
    assert (root / ".venv").is_dir()


SETTINGS_SOURCE = textwrap.dedent(
    """
    import json
    import os

    from pydantic import BaseModel

    from atelier_plugin_sdk import Health, Plugin, PointDistributor

    class Params(BaseModel):
        pass

    class Echo(PointDistributor[Params]):
        name = "echo"
        Params = Params

        def distribute(self, ctx, source, n, params):
            raise NotImplementedError

    def health():
        settings = json.loads(os.environ.get("ATELIER_PLUGIN_SETTINGS", "{}"))
        paths = os.environ.get("ATELIER_SDK_PATHS", "")
        return Health.ok(f"{settings.get('home')}|{bool(paths)}")

    plugin = Plugin(components=[Echo()], health_check=health)
    """
)


def test_settings_reach_the_subprocess(tmp_path: Path) -> None:
    plugin_id = uid()
    write_plugin(
        tmp_path / "plugins",
        plugin_id,
        source=SETTINGS_SOURCE,
        runtime="subprocess",
        command=[
            "{python}",
            "-m",
            "atelier_plugin_sdk.serve",
            "--entry",
            "{entry}",
            "--path",
            "{setting:src_dir}",
        ],
    )
    src = str(tmp_path / "plugins" / plugin_id / "src")
    settings = {plugin_id: {"home": r"C:\Some {odd} path", "src_dir": src}}

    with discover(tmp_path / "plugins", plugin_settings=settings) as registry:
        health = registry.plugin(plugin_id).health

    assert health is not None and health.message == r"C:\Some {odd} path|True"


def test_missing_setting_is_explained(tmp_path: Path) -> None:
    plugin_id = uid()
    write_plugin(
        tmp_path / "plugins", plugin_id, runtime="subprocess", command=["{setting:python}", "x.py"]
    )

    with discover(tmp_path / "plugins") as registry:
        error = registry.plugin(plugin_id).error or ""

    assert f"[plugin_settings.{plugin_id}]" in error and "'python'" in error


def test_disable_and_enable_at_runtime(tmp_path: Path) -> None:
    plugin_id = uid()
    write_plugin(tmp_path, plugin_id)

    with discover(tmp_path) as registry:
        registry.set_enabled(plugin_id, False)
        with pytest.raises(ComponentNotFound, match="disabled"):
            registry.resolve("point_distributor", plugin_id)
        registry.set_enabled(plugin_id, True)
        assert registry.resolve("point_distributor", plugin_id).info.name == "random"


def test_health_on_request_starts_a_cached_subprocess(tmp_path: Path) -> None:
    plugin_id = uid()
    write_plugin(tmp_path / "plugins", plugin_id, runtime="subprocess")
    cache = tmp_path / "cache.json"
    discover(tmp_path / "plugins", cache_path=cache).close()

    with discover(tmp_path / "plugins", cache_path=cache) as registry:
        assert registry.plugin(plugin_id).health is None
        plugin = registry.check_health(plugin_id)

    assert plugin.health is not None and plugin.health.status == "ok"
