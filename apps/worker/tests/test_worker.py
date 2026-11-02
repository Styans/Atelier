from pathlib import Path

from atelier_jobs import JobQueue
from atelier_show_engine.service import ShowService
from atelier_show_engine.store import ShowStore
from atelier_worker.worker import Worker


def small_show(service: ShowService, files: dict[str, Path]) -> ShowStore:
    store, _ = service.create_show("Worker", drones=16, duration_s=120)
    service.add_model(store, files["torus"], name="Torus")
    service.add_scene(store, "m_torus", hold_s=4)
    return store


def run(worker: Worker, queue: JobQueue, show_id: str, kind: str, **args: object) -> dict:  # type: ignore[type-arg]
    job = queue.submit(show_id, kind, dict(args))
    finished = worker.run_once()
    assert finished is not None and finished.id == job.id
    return finished.public()


def test_jobs_run_the_whole_pipeline(
    worker: Worker, queue: JobQueue, service: ShowService, fixture_files: dict[str, Path]
) -> None:
    store = small_show(service, fixture_files)
    show_id = store.load().id

    points = run(worker, queue, show_id, "points", model_id="m_torus")
    compiled = run(worker, queue, show_id, "compile")
    validated = run(worker, queue, show_id, "validate")
    exported = run(worker, queue, show_id, "export", exporters=["builtin.csv"])

    assert [j["status"] for j in (points, compiled, validated, exported)] == ["done"] * 4, [
        j["error"] for j in (points, compiled, validated, exported)
    ]
    assert points["result"]["component"] == "pointille.blue_noise"
    assert compiled["result"]["frames"] > 0 and validated["result"]["ok"]
    assert exported["result"][0]["files"] == ["skybrush_csv.zip"]
    assert Path(compiled["log_path"]).read_text(encoding="utf-8").count("done") >= 1
    assert store.history()[-1].author == "worker"


def test_a_failing_job_keeps_its_message(
    worker: Worker, queue: JobQueue, service: ShowService, fixture_files: dict[str, Path]
) -> None:
    store = small_show(service, fixture_files)

    job = run(worker, queue, store.load().id, "points", model_id="m_missing")

    assert job["status"] == "failed" and "no model 'm_missing'" in job["error"]


def test_cancel_stops_a_running_job(
    worker: Worker, queue: JobQueue, service: ShowService, fixture_files: dict[str, Path]
) -> None:
    store = small_show(service, fixture_files)
    service.compute_points(store, "m_torus")
    job = queue.submit(store.load().id, "compile")
    claimed = queue.claim(worker.pid)
    assert claimed is not None
    queue.request_cancel(job.id)  # the user presses "stop" while it runs

    worker.execute(claimed)

    assert queue.get(job.id).status == "cancelled"  # type: ignore[union-attr]
    assert store.load().outputs.compiled is None


def test_generation_jobs(
    worker: Worker, queue: JobQueue, service: ShowService, fixture_files: dict[str, Path]
) -> None:
    store = small_show(service, fixture_files)
    service.set_stage(store, "image_generator", "fake.concepts", {})
    service.set_stage(store, "mesh_generator", "fake.sculptor", {})
    show_id = store.load().id

    concepts = run(
        worker, queue, show_id, "generate_images", model_id="m_torus", prompt="a ring", n=3
    )
    mesh = run(worker, queue, show_id, "generate_mesh", model_id="m_torus")

    assert concepts["status"] == "done", concepts["error"]
    assert mesh["status"] == "done", mesh["error"]
    card = store.load().models["m_torus"]
    assert card.image_prompt == "a ring" and len(service.concept_images(store, "m_torus")) == 3
    assert card.assets.selected_concept == service.concept_images(store, "m_torus")[0]
    assert service.stale_report(store)["models.m_torus.mesh"] is None
    points = run(worker, queue, show_id, "points", model_id="m_torus")
    assert points["status"] == "done"


def test_a_show_without_generators_gets_the_default_ones(
    worker: Worker, queue: JobQueue, service: ShowService, fixture_files: dict[str, Path]
) -> None:
    store = small_show(service, fixture_files)
    assert store.load().pipeline.image_generator is not None  # new shows have them already
    old = store.load()
    old.pipeline.image_generator = None  # a show made before that
    store.commit(old, "Show from before default generators", "cli")
    show_id = old.id

    concepts = run(
        worker, queue, show_id, "generate_images", model_id="m_torus", prompt="a ring", n=1
    )

    assert concepts["status"] == "done", concepts["error"]
    pipeline = store.load().pipeline
    assert pipeline.image_generator is not None
    assert pipeline.image_generator.component == "fake.concepts"
    assert "image_generator = fake.concepts (default)" in store.history()[-2].message
