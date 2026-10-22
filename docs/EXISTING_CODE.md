# Существующий код: что оборачиваем как есть, что дописываем, что требует правки

Разведка 2026-10-21, только чтение (в чужих репозиториях ничего не менялось).
Правило SPEC §2: не переписывать; оборачивать в пакеты с интерфейсами §8; переписывать только
мешающее интеграции и только с согласия автора.

## Pointille — `C:\Users\Beacon\Repo\Pointille` (MIT)

**Состояние репозитория.**
- ⚠ **Python-ядро `pointille/` (`core/`, `io/`) не закоммичено.** На `main` 3 коммита
  (HEAD `5ae4ba2`, add-on 0.3.0). Последняя версия (0.9.0) — незакоммиченные изменения в worktree
  `vertex-groups-edit-mode` (совпадает с `pointille_blender-0.9.0.zip`).
  Установленное расширение Blender — 0.8.0.
- В `pyproject.toml` объявлен `pointille = "pointille.cli:main"`, модуля `pointille/cli.py` нет
  нигде; `tests/test_cli.py` падает на сборке.

**Что есть (берём как есть).** `pointille.core` и `pointille.io` — numpy + stdlib, без bpy/scipy.
- `distribute(mesh: MeshData, options: DistributeOptions) -> DroneResult` (`core/api.py:97`).
  Алгоритм: плотные кандидаты по площади → Weighted Sample Elimination (Yuksel 2015) до ровно N →
  релаксация отталкиванием с проекцией на поверхность (20 итераций по умолчанию).
- Ровно N гарантировано (или `ValueError`); один `np.random.default_rng(seed)` → детерминизм.
- `DroneResult`: `positions`, `normals` (плоские), `triangle_ids`, `barycentrics`,
  `colors_linear`, `min_distance`, `mean_distance`, `area`, `scale`, `pivot`, `visibility`.
- Цвета: base color × vertex color × билинейная выборка текстуры; режимы `area`/`point`;
  `glow(min_brightness)`.
- Facade-режим (плотность по видимости со стороны зрителя), `scale_factor("min_distance", ...)`.
- `MeshData`: суп треугольников `triangles (T,3,3)`, `uvs`, `material_ids`, `vertex_colors`,
  `materials` с `Texture`.

**Что дописываем в адаптере `plugins/pointille` (без правок Pointille).**
| Цель (SPEC §8.2) | Как |
|---|---|
| `distribute(mesh, n, method, seed)` | обёртка; `method: Literal["blue_noise"]` (WSE+relax), параметры `oversample`, `relax_iterations`, `color_mode`, facade |
| GLB/OBJ → `MeshData` | конвертер trimesh → суп `V[F]` в порядке граней (тогда `triangle_ids` = индекс грани), UV, vertex colors, текстура (pillow) |
| `PointSet` с `tri_index`/`barycentric` | переупаковка `DroneResult`; V/F храним рядом в артефакте меша |
| `evaluate(point_set, deformed_vertices)` | `einsum(bary, V'[F[tri]])` + пересчёт нормалей граней |
| `min_safe_scale(point_set, d)` | `d / min_nn_distance` (как `stats.scale_factor`) + pivot |
| Метрики | min/mean NN, CV расстояний, min/ideal, mean/ideal (scipy `cKDTree`) |

**Чего нет и что отложено.** 2D-контур (сэмплер вдоль полилиний), SVG, текст — роль
`formation_source` / `builtin_formations`, фаза 4 (заливка уже работает через плоский меш).
Палитра/квантизация цветов — в ядре платформы.

**Сделано (2026-10-22, с согласия автора):** состояние worktree 0.9.0 закоммичено в новую ветку
`atelier-integration` (коммит `468fa98`) через временный индекс — worktree, его ветка и `main` не
тронуты. На этом коммите проходят 34 теста ядра Pointille. Atelier зависит от этой ветки (ADR-013).
Остаётся по желанию: влить ветку в `main`; убрать или дописать `pointille.cli`.

**Соответствие интерфейсам SPEC §8.2.** `distribute` → компонент `pointille.blue_noise`;
`PointSet` → `atelier_plugin_sdk.PointSet`; `evaluate` → `PointSet.evaluate(faces, vertices)` (SDK);
`min_safe_scale` → `show_engine.geometry.min_safe_scale`; метрики → `PointSet.meta["metrics"]`
(min/mean NN, CV, отношения к идеальной гексагональной упаковке).

**Замечания.** Расстояния 3D-евклидовы (тонкие части модели «чувствуют» обе стороны);
`up_axis=2` (Z-up) по умолчанию совпадает с нашей конвенцией; основной цикл WSE на чистом Python
(`heapq`) — для N ≤ 1000 приемлемо, мерить на 500.

## Pygmalion — `C:\Users\Beacon\Repo\Pygmalion` (пакет `dronegen_server` 0.1.0)

**Состояние.** ⚠ В репозитории нет ни одного коммита (всё untracked). Установленный в
`runtime\venv` сервер — обычная (не editable) копия от 2026-10-07, расходится с `server\` в 7 файлах (в
частности, там включена перекраска `postprocess.led_colors`). Runtime ~51 GB (37 GB весов).

**Что есть (берём как есть).**
- FastAPI + uvicorn, порт 8188; одна очередь GPU-задач (`JobManager`, один worker-поток),
  выгрузка моделей при смене бэкенда/по таймауту/`/unload`, фолбэки по памяти, понятные ошибки OOM.
- txt2img: **Z-Image-Turbo** (по умолчанию; GGUF Q4; *игнорирует negative*), **SDXL** (фолбэк).
- img2mesh: **Hunyuan3D-2mini**, **Hunyuan3D-2mv** (multi-view), **TripoSR**, **silhouette** (CPU),
  + параметрические шаблоны (домбра, шанырак, юрта, Байтерек).
- Замеры на RTX 3050 8 GB: Z-Image 768² ~30 с / 5.6 GB; Hunyuan-mini 9 с / 4.7 GB; TripoSR 4 с.
- `JobManager` + бэкенды работают как библиотека без HTTP (так устроены GPU-тесты и `bench.py`).
- Выход меша: GLB (+OBJ), **только vertex colors** (без UV/текстур), оси glTF (+Y вверх, лицо
  на +Z), центрирован, наибольшая сторона = 1.

**План интеграции (фаза 2): stdio-shim в venv Pygmalion.**
```python
settings = Settings.load(home, backends=[...]); settings.apply_env()  # до импорта torch/HF
manager = JobManager(settings, Registry(settings)); manager.start()
job = manager.submit("txt2img", "zimage_turbo", {"prompt": ..., "seed": ..., "width": 768, "height": 768})
```
Shim = наш `atelier_plugin_sdk.serve` (роли `image_generator` + `mesh_generator`), запускаемый
`runtime\venv\Scripts\python.exe` Pygmalion-а. Учитываем: нет параметра `n` (цикл по `seed+i`),
Hunyuan по умолчанию `seed=1` (передавать явно), логи сервера идут в stdout (SDK уводит fd 1 в
stderr), первая задача платит загрузку модели 15–35 с, сообщения об ошибках на русском.

**Просьбы к автору Pygmalion (до фазы 2):** закоммитить репозиторий; переустановить сервер с
`--dev` (editable), чтобы запускался актуальный код.

## Прочие аддоны в Blender (не переиспользуем)
- `scripts\addons\ai_drone_show` («AI Drone Show Generator» 1.1.0): Ollama/LM Studio → меши →
  сэмплинг → назначение → ключи. Есть полезные идеи (`quintic_smoothstep`, auction-фолбэк), но
  нет лимитов скорости/ускорения, сторибординг без времён, `validate_safety` всегда «safe».
- `scripts\addons\drone_show_ai.py` («Drone Show AI Studio» 0.1.0): бриф → формации →
  recalculate transitions (нужен сервер Skybrush); `push_limits` пишет в поля результата вместо
  порогов.
- `scripts\addons\build_blender_addon.py` — скрипт сборки Pointille, случайно попавший в папку
  аддонов; не аддон.
- `extensions\user_default\dronegen` — Blender-клиент Pygmalion (0.5.0), траекторий нет.
