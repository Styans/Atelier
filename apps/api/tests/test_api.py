import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
from fastapi.testclient import TestClient

from atelier_api.binary import unpack
from atelier_worker.worker import Worker

Env = tuple[TestClient, Worker]


def ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def new_show(client: TestClient, drones: int = 16) -> dict[str, Any]:
    data = ok(
        client.post("/api/shows", json={"title": "API", "drones": drones, "duration_s": 120}), 201
    )
    return data["show"]


def upload(client: TestClient, show_id: str, path: Path, name: str) -> dict[str, Any]:
    with path.open("rb") as fh:
        return ok(
            client.post(
                f"/api/shows/{show_id}/models", files={"file": (path.name, fh)}, data={"name": name}
            ),
            201,
        )


def run_job(
    client: TestClient, worker: Worker, show_id: str, kind: str, **args: Any
) -> dict[str, Any]:
    job = ok(client.post(f"/api/shows/{show_id}/jobs", json={"kind": kind, "args": args}), 202)
    worker.run_once()
    done = ok(client.get(f"/api/jobs/{job['id']}"))
    assert done["status"] == "done", done["error"]
    return done


def test_health_and_meta(env: Env) -> None:
    client, _ = env

    assert ok(client.get("/api/health"))["ok"]
    meta = ok(client.get("/api/meta"))
    assert "conservative" in meta["presets"] and "compile" in meta["job_kinds"]


def test_the_phase_one_path_over_http(env: Env, fixture_files: dict[str, Path]) -> None:
    client, worker = env
    show = new_show(client)
    sid = show["id"]
    with fixture_files["music"].open("rb") as fh:
        ok(client.post(f"/api/shows/{sid}/music", files={"file": ("music.wav", fh)}))
    upload(client, sid, fixture_files["torus"], "Torus")
    upload(client, sid, fixture_files["star"], "Star")
    glb = client.get(f"/api/shows/{sid}/models/m_torus/mesh.glb")
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"

    run_job(client, worker, sid, "points", model_id="m_torus")
    run_job(client, worker, sid, "points", model_id="m_star")
    header, arrays = unpack(client.get(f"/api/shows/{sid}/models/m_torus/points").content)
    assert arrays["positions"].shape == (16, 3) and header["stale"] is None
    assert float(arrays["nearest"].min()) >= 2.0

    ok(client.post(f"/api/shows/{sid}/scenes", json={"model_id": "m_torus", "hold_s": 4}), 201)
    spin = {"primitive": "builtin.rotate", "easing": "ease_in_out", "params": {"angle_deg": 60}}
    star = {"model_id": "m_star", "hold_s": 5, "rotation_deg": [0, 0, 45], "tracks": [spin]}
    added = ok(client.post(f"/api/shows/{sid}/scenes", json=star), 201)
    assert added["show"]["program"][1]["rotation_deg"] == [0, 0, 45]
    planned = ok(client.get(f"/api/shows/{sid}/timeline"))
    assert not planned["compiled"] and planned["segments"][0]["kind"] == "takeoff"
    compiled = run_job(client, worker, sid, "compile")
    assert compiled["result"]["frames"] > 0

    summary = ok(client.get(f"/api/shows/{sid}/compiled"))
    exact = ok(client.get(f"/api/shows/{sid}/timeline"))
    assert exact["compiled"] and np.isclose(exact["total_s"], summary["duration_s"])
    header, arrays = unpack(client.get(f"/api/shows/{sid}/compiled/frames?fps=10").content)
    assert arrays["positions"].shape[1:] == (16, 3)
    assert np.isclose(arrays["times"][-1], summary["duration_s"])
    assert [m["kind"] for m in header["marks"]][:2] == ["ground", "takeoff"]

    run_job(client, worker, sid, "validate")
    assert ok(client.get(f"/api/shows/{sid}/validation"))["ok"]
    run_job(client, worker, sid, "export", exporters=["builtin.csv"])
    exports = ok(client.get(f"/api/shows/{sid}/exports"))
    assert exports[0]["files"] == ["music.wav", "skybrush_csv.zip"]
    archive = client.get(f"/api/shows/{sid}/files/{exports[0]['folder']}/skybrush_csv.zip")
    assert archive.status_code == 200 and archive.content[:2] == b"PK"

    stale = ok(client.get(f"/api/shows/{sid}"))["stale"]
    assert all(reason is None for reason in stale.values()), stale


def test_edits_and_conflicts(env: Env, fixture_files: dict[str, Path]) -> None:
    client, _ = env
    show = new_show(client)
    sid, rev = show["id"], show["revision"]

    edited = ok(
        client.patch(
            f"/api/shows/{sid}/brief", json={"base_revision": rev, "changes": {"title": "Шоу"}}
        )
    )
    conflict = client.patch(
        f"/api/shows/{sid}/brief", json={"base_revision": rev, "changes": {"title": "X"}}
    )
    invalid = client.patch(f"/api/shows/{sid}/brief", json={"changes": {"palette": ["red"]}})

    assert edited["show"]["brief"]["title"] == "Шоу"
    assert conflict.status_code == 409 and "changed meanwhile" in conflict.json()["detail"]
    assert invalid.status_code == 400 and "palette" in invalid.json()["detail"]
    assert client.get("/api/shows/nope").status_code == 404


def test_scenes_revisions_and_rollback(env: Env, fixture_files: dict[str, Path]) -> None:
    client, _ = env
    sid = new_show(client)["id"]
    upload(client, sid, fixture_files["torus"], "Torus")
    ok(client.post(f"/api/shows/{sid}/scenes", json={"model_id": "m_torus", "hold_s": 4}), 201)
    ok(client.post(f"/api/shows/{sid}/scenes", json={"model_id": "m_torus", "hold_s": 6}), 201)
    before = ok(client.get(f"/api/shows/{sid}"))["show"]["revision"]

    ok(client.patch(f"/api/shows/{sid}/scenes/s_1", json={"changes": {"hold_s": 9}}))
    ok(client.post(f"/api/shows/{sid}/scenes/reorder", json={"order": ["s_2", "s_1"]}))
    diff = ok(client.get(f"/api/shows/{sid}/revisions/{before}/diff"))
    back = ok(client.post(f"/api/shows/{sid}/rollback/{before}", json={}))

    paths = {d["path"] for d in diff}
    assert {"program.s_1.hold_s", "program order"} <= paths
    assert [s["id"] for s in back["show"]["program"]] == ["s_1", "s_2"]
    history = ok(client.get(f"/api/shows/{sid}/revisions"))
    assert history[-1]["message"] == f"Rollback to {before}" and history[-1]["author"] == "ui"


def test_plugins_screen(env: Env) -> None:
    client, _ = env

    plugins = {p["id"]: p for p in ok(client.get("/api/plugins"))}
    distributors = ok(client.get("/api/components/point_distributor"))
    union = ok(client.get("/api/schemas/animation_primitive"))
    disabled = ok(client.post("/api/plugins/builtin_animation/disable"))
    enabled = ok(client.post("/api/plugins/builtin_animation/enable"))

    assert plugins["pointille"]["status"] == "ok"
    assert distributors[0]["id"] == "pointille.blue_noise"
    assert "oversample" in distributors[0]["params_schema"]["properties"]
    assert {v["title"] for v in union["oneOf"]} == {"builtin.rotate", "builtin.scale_pulse"}
    assert disabled["status"] == "disabled" and enabled["status"] == "ok"


def test_failed_and_cancelled_jobs(env: Env) -> None:
    client, worker = env
    sid = new_show(client)["id"]

    failing = ok(
        client.post(f"/api/shows/{sid}/jobs", json={"kind": "points", "args": {"model_id": "m_x"}}),
        202,
    )
    worker.run_once()
    queued = ok(client.post(f"/api/shows/{sid}/jobs", json={"kind": "compile"}), 202)
    cancelled = ok(client.post(f"/api/jobs/{queued['id']}/cancel"))

    assert ok(client.get(f"/api/jobs/{failing['id']}"))["status"] == "failed"
    assert "no model 'm_x'" in client.get(f"/api/jobs/{failing['id']}/log").text
    assert cancelled["status"] == "cancelled"
    assert client.post(f"/api/shows/{sid}/jobs", json={"kind": "dance"}).status_code == 400


def test_files_stay_inside_the_project(env: Env) -> None:
    client, _ = env
    sid = new_show(client)["id"]

    assert client.get(f"/api/shows/{sid}/files/show.json").status_code == 200
    assert client.get(f"/api/shows/{sid}/files/../../atelier.toml").status_code == 404


def test_events_stream_reports_jobs(env: Env) -> None:
    client, _ = env
    sid = new_show(client)["id"]

    def submit_later() -> None:
        time.sleep(0.5)
        client.post(f"/api/shows/{sid}/jobs", json={"kind": "compile"})

    threading.Thread(target=submit_later, daemon=True).start()
    seen = []
    with client.stream("GET", "/api/events") as stream:
        deadline = time.monotonic() + 10
        for line in stream.iter_lines():
            seen.append(line)
            if line == "event: job" or time.monotonic() > deadline:
                break

    assert "event: job" in seen
