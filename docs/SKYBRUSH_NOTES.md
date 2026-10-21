# Skybrush Studio for Blender — заметки по интеграции

Источник: чтение установленного аддона (2026-10-21) + headless-проба
`packages/blender_bridge/src/atelier_blender_bridge/scripts/probe_skybrush.py` + публичные
страницы Skybrush. Номера строк — для установленной копии (докстринги в ней вырезаны, со
строками upstream не совпадают).

Пути:
- `<ADDONS>` = `C:\Users\Beacon\AppData\Roaming\Blender Foundation\Blender\5.2\scripts\addons`
- точка входа: `<ADDONS>\ui_skybrush_studio.py` (legacy-аддон, модуль `ui_skybrush_studio`)
- пакет: `<SB>` = `<ADDONS>\vendor\skybrush\sbstudio` (+ `vendor\skybrush\natsort`)

## Главное

| Факт | Следствие для Atelier |
|---|---|
| Версия 5.0.4, `bl_info.blender = (4,4,0)`; в Blender 5.2.1 работает headless (проба: 12/12 шагов) | Целимся в Blender 5.2 + этот аддон |
| **Все экспорты** (`.skyc`, Skybrush CSV, PDF, DSS/Litebee/EVSKY/...) идут через Skybrush Studio Server (`POST operations/render`). Локального писателя `.skyc` нет | Свои траектории, свой валидатор, свой CSV. Blender только собирает `.blend` |
| Community-сервер (по умолчанию, `https://studio.skybrush.io/api/v1`) с 01.03.2025 ограничен **64 дронами (8×8)**; LOCAL/CLOUD режимы — по лицензии | 150/500 дронов в `.skyc` без лицензии не экспортировать. Отправка на сервер — только явной командой пользователя |
| Safety check считается **локально**, но только для **текущего кадра** (handler на `frame_change_post`, кэш 5 кадров для скоростей) | Источник правды — наш валидатор; в Blender сверяем выборку кадров, шагая по порядку |
| Цвет дрона = keyframe `Object.color` (RGBA), не материал | Пишем ключи `color` напрямую |
| Recalculate transitions добавляет COPY_LOCATION-констрейнты, перебивающие ключи | Никогда не вызывать; все записи сторибординга `MANUAL` |
| Для `.skyc` аудио берётся **только из MP3**-стрипа VSE (`plugin/utils/audio.py`) | Музыку в `.blend` кладём как MP3 (WAV оставляем в экспорт-пакете) |

## Модель данных

**Дроны** — mesh-объекты в коллекции «Drones» (указатель `scene.skybrush.settings.drone_collection`,
иначе `bpy.data.collections["Drones"]`; `plugin/constants.py:66-101`). Создаются копированием
скрытого шаблона «Drone template» из коллекции «Templates» (`Templates.find_drone()`,
`operators/create_takeoff_grid.py:22` — `create_drone(location, *, name, template, collection)`).
Слот материала 0 — общий «Drone template material» (Object Info.Color → Emission), слот 1 — пиро.
Экспорт читает `matrix_world.translation`, сортировка дронов — natural sort по имени («Drone 1…N»).

**Цвет LED** — `Object.color`, data path `"color"`; световые эффекты накладываются поверх в
handler-е и пишутся обратно в `Object.color` (`plugin/model/led_control.py`). Держать
`scene.skybrush.light_effects.enabled = True`, иначе кэш цветов не обновляется (по коду).

**Формации** — дочерние коллекции «Formations» с empty-маркерами «<имя> - <i>» или mesh с
vertex group. `create_formation(name, points)` в `plugin/model/formation.py:44`.

**Сторибординг** — `scene.skybrush.storyboard` (`plugin/model/storyboard.py`):
`add_new_entry(name, frame_start, duration, *, purpose, formation)`; поля записи: `formation`,
`frame_start`, `duration`, `transition_type` (MANUAL/AUTO), `transition_schedule`,
`transition_velocity_profile`, `purpose` (TAKEOFF/SHOW/LANDING), задержки, `mapping`.
Валидация: записи не перекрываются, порядок назначений TAKEOFF → SHOW → LANDING.

**Пороги safety** — `scene.skybrush.safety_check` (`plugin/model/safety_check.py`):

| Свойство | По умолчанию |
|---|---|
| `proximity_warning_threshold` | 3.0 м |
| `altitude_warning_threshold` | 150 м |
| `min_navigation_altitude` | 2.5 м (дроны ниже исключаются из проверки дистанции) |
| `velocity_xy_warning_threshold` | 10 м/с |
| `velocity_z_warning_threshold` (+ отдельный up) | 2 м/с |
| `acceleration_warning_threshold` | 4 м/с² |
| `yaw_rate_warning_threshold` | 30 °/с |

Поля результата (пишет handler; **не** путать с порогами): `min_distance`, `max_altitude`,
`max_velocity_xy`, `max_velocity_z_up`, `max_velocity_z_down`, `max_acceleration`, ...
При экспорте `minNavAltitude` всегда уходит как 2.5 (`model/safety_check.py:21`).

## Сервер и лицензия

- Режимы (`plugin/model/global_settings.py:73-104`): COMMUNITY (по умолчанию; «Limited drone
  count, no guaranteed uptime»), LOCAL (`http://localhost:8000`, лицензия), CLOUD (через gateway
  `localhost:7999`, лицензия), ADVANCED (любой URL + API key).
- Лимит дронов клиент не проверяет — сервер отвечает HTTP 413 с сообщением о лимитах community.
- Все вызовы API блокируются при `bpy.app.online_access == False` (в т.ч. к localhost).
  Мы запускаем Blender с `--offline-mode` — сетевых вызовов из наших скриптов нет.
- `urlopen` без таймаута, SSL-проверка выключена (`plugin/api.py:51`) — ещё одна причина не
  дёргать сервер из фоновых задач.
- Серверные операции: все экспорты, `match-points` (AUTO-переходы), `plan-transition`,
  `plan-takeoff`, `plan-landing`, `plan-smart-rth`, импорт SVG и DSS, `validate_trajectories`
  (+ Skybrush Viewer). Takeoff/Land/RTH/recalculate сначала пингуют версию сервера.
- Локально работает: сетка взлёта, формации, сторибординг через модель, световые эффекты,
  ключи цветов, импорт static/zipped CSV и QR, safety-check handler, модульная функция
  `recalculate_transitions()` при всех записях MANUAL.
- Формат zipped CSV, который принимает импорт (`add_markers_from_zipped_csv.py:79-109`):
  по файлу на дрона, заголовок `Time [msec]` (или `time_msec`), затем `x,y,z,r,g,b`.
  Этот же формат пишет наш `builtin.csv`.

## Результаты headless-пробы (Blender 5.2.1 LTS, Python 3.13.13)

Команда:
`blender -b --offline-mode --python-exit-code 1 --python probe_skybrush.py -- --out projects/_probe`

| Шаг | Результат |
|---|---|
| Аддон | уже включён в prefs, загружен; prefs не изменялись (`use_preferences_save = False`, файл `userpref.blend` не тронут) |
| `bpy.ops.skybrush.prepare()` | создаёт Drones / Drone Groups / Formations / Templates |
| `create_drone` ×16 из шаблона | ок |
| Ключи `location`×3 + `color`×4 через `action.fcurve_ensure_for_datablock` + `keyframe_points.foreach_set("co"/"interpolation")` | 13 440 ключей за 3.6 мс; одна slot на action |
| Сторибординг TAKEOFF/SHOW/LANDING + формации | ок |
| Звук: `scene.sequence_editor_create().strips.new_sound(...)` | API `strips` (Blender 5); `frame_final_duration` deprecated до 6.0 |
| save → reopen | fps 30, диапазон кадров, 16 дронов, 3 записи, 1 стрип, `skybrush.version = 2` |
| Сверка позиций/цветов на 5 кадрах | ошибка ≤ 1.9e-7 м / 3e-8 |
| Safety-check handler при `frame_set` | `min_distance` 3.0, `max_velocity_z_up` 3.0 м/с при подъёме 6 м за 2 с, `max_velocity_xy` 3.33 м/с при вращении 30°/с — совпадает с ожидаемым |

Шум, не мешающий работе: в `load_post` handler-е аддона падает
`gpu.shader.from_builtin` («GPU functions for drawing requires the gpu module to be initialized»)
— это оверлеи, результаты safety-check пишутся до этого. При чтении пустого homefile
срабатывает миграция 1→2; ставим `scene.skybrush.version = 2` при сборке.

Оценка размера: ~11 байт на ключ в сжатом `.blend` → 500 дронов × 18 000 кадров × 7 каналов
≈ 700 MB без прореживания. В фазе 1 — прореживание ключей (RDP с допуском) перед сборкой.

## Что не работает в `-b` (по коду)
- Операторы, вызывающие `redraw_all_3d_views()` (нужен `context.screen`): `run_full_proximity_check`
  (после расчёта), `remove_light_effect`, `invalidate_light_effect_pixel_cache`, `update_pyro_params`.
- Экспорт с `redraw="ALWAYS"`/`"AUTO"` (вызывает `wm.redraw_timer`) — передавать `redraw="NEVER"`.
- `validate_trajectories` — нужен Skybrush Viewer.
- Оператор экспорта ловит все ошибки и возвращает `{'CANCELLED'}` — проверять результат.

## Открытые вопросы
- Как получить `.skyc` для Skybrush Live без лицензии: (а) лицензия локального сервера;
  (б) конвертер CSV→.skyc в аккаунте Skybrush (лимиты неизвестны); (в) свой писатель `.skyc` по
  открытым исходникам libskybrush/skybrush-server — отдельное исследование после фазы 1.

## Источники
- [Skybrush Store, 2025-02-04: community-сервер ≤ 8×8 = 64 дрона с 01.03.2025](https://skybrush.io/blog/2025-02-04-skybrush-store/)
- [Skybrush Studio Server (лицензии)](https://skybrush.io/shop/studio)
- [Документация Skybrush Studio for Blender](https://doc.collmot.com/public/skybrush-studio-for-blender/latest/)
