"""The subprocess server, driven over real pipes."""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from atelier_plugin_sdk.testing import reference_mesh

HERE = Path(__file__).parent


class Client:
    def __init__(self, tmp_path: Path) -> None:
        self.stderr = (tmp_path / "stderr.log").open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "atelier_plugin_sdk.serve",
                "--entry",
                "fixture_plugin:plugin",
                "--path",
                str(HERE),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.stderr,
            text=True,
            encoding="utf-8",
        )
        self.next_id = 0
        self.notifications: list[dict[str, Any]] = []

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.proc.stdin and self.proc.stdout
        self.next_id += 1
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params})
            + "\n"
        )
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            assert line, "server closed the pipe"
            message = json.loads(line)  # any stray print would fail here
            if message.get("id") == self.next_id:
                return message
            self.notifications.append(message)

    def close(self) -> None:
        if self.proc.poll() is None:
            self.call("atelier.shutdown", {})
            self.proc.wait(timeout=10)
        self.stderr.close()


@pytest.fixture
def client(tmp_path: Path) -> Iterator[Client]:
    c = Client(tmp_path)
    yield c
    c.close()


def test_initialize_lists_components_with_schemas(client: Client) -> None:
    result = client.call("atelier.initialize", {"api_version": "1", "host_version": "test"})[
        "result"
    ]

    names = {(c["role"], c["name"]) for c in result["components"]}
    assert ("point_distributor", "random") in names
    shift = next(c for c in result["components"] if c["name"] == "shift")
    assert shift["params_schema"]["properties"]["dz"]["default"] == 1.0


def test_wrong_api_version_is_refused(client: Client) -> None:
    response = client.call("atelier.initialize", {"api_version": "99"})

    assert response["error"]["code"] == 1001


def test_invoke_streams_progress_and_survives_stdout_noise(client: Client, tmp_path: Path) -> None:
    mesh_path = reference_mesh().save(tmp_path / "mesh.npz")
    response = client.call(
        "atelier.invoke",
        {
            "call_id": "c1",
            "role": "point_distributor",
            "component": "random",
            "args": {"source": {"$ref": "mesh", "path": str(mesh_path), "meta": {}}, "n": 12},
            "params": {},
            "ctx": {"seed": 5, "workdir": str(tmp_path / "work")},
        },
    )

    assert response["result"]["$ref"] == "point_set"
    assert Path(response["result"]["path"]).exists()
    assert any(n["method"] == "atelier.progress" for n in client.notifications)
    assert "this line must not reach the protocol" in (tmp_path / "stderr.log").read_text(
        encoding="utf-8"
    )


def test_errors_keep_their_codes(client: Client, tmp_path: Path) -> None:
    mesh_path = reference_mesh().save(tmp_path / "mesh.npz")
    base = {
        "role": "point_distributor",
        "args": {"source": {"$ref": "mesh", "path": str(mesh_path), "meta": {}}, "n": 8},
        "params": {},
        "ctx": {"seed": 1, "workdir": str(tmp_path / "work")},
    }

    oom = client.call("atelier.invoke", {**base, "call_id": "a", "component": "greedy"})
    short = client.call("atelier.invoke", {**base, "call_id": "b", "component": "short"})
    bad = client.call(
        "atelier.invoke",
        {**base, "call_id": "c", "component": "random", "params": {"area_weighted": "?"}},
    )

    assert oom["error"]["code"] == 1002
    assert oom["error"]["data"]["hint"]
    assert short["error"]["code"] == 1004
    assert bad["error"]["code"] == -32602


def test_health_and_normalize(client: Client) -> None:
    assert client.call("atelier.health", {})["result"] == {
        "status": "ok",
        "message": "fixture ready",
    }
    normalized = client.call(
        "atelier.normalize_params",
        {"role": "animation_primitive", "component": "shift", "params": {}},
    )
    assert normalized["result"] == {"dz": 1.0}
