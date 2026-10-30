# Atelier

Локальная платформа производства дрон-шоу: от идеи до файла для флота.

Автор описывает шоу, получает бриф и программу сцен, собирает фигуры, раскладывает по ним
дронов, компилирует безопасные траектории, проверяет их и экспортирует шоу в Skybrush Studio
через Blender. Всё работает на одной машине, без облака.

## Возможности

- **Шоу как документ.** Единственный источник правды — `show.json` в папке проекта. Каждое
  изменение даёт новую ревизию; любую ревизию можно вернуть, артефакты не перезаписываются.
- **Модели и точки.** Импорт GLB или генерация фигуры (концепт-картинки и 3D-модель) через
  плагин Pygmalion; раскладка дронов по фигуре — плагин Pointille.
- **Компиляция и проверка.** Переходы между фигурами с учётом минимальной дистанции, скоростей и
  ускорений флота; валидаторы дистанций, скоростей и высот; детерминированный результат при
  одинаковом `show.json` и seed.
- **Экспорт.** Headless Blender собирает `.blend` для Skybrush Studio; есть CSV для импорта.
- **Веб-интерфейс.** Материалы шоу, 3D-просмотр модели, точек и проигрывание шоу, таймлайн с
  бюджетом времени, pipeline, ревизии с diff и откатом, очередь задач.
- **Ассистент в чате.** Локальная LLM (llama-server) заполняет бриф, предлагает программу сцен и
  карточки фигур, правит шоу по просьбе. Модель только предлагает: длительности, геометрию и
  проверки считает код, дорогие действия выполняются только по кнопке «Применить», каждый ответ
  можно откатить.
- **Плагины.** Ядро знает только роли и типы артефактов; распределители точек, переходы,
  анимация, валидаторы, экспортёры и генераторы — плагины (в процессе или отдельным процессом по
  JSON-RPC).

## Требования

- Windows 10/11 (основная платформа; пути и процессы рассчитаны на неё).
- Python 3.12 и [uv](https://docs.astral.sh/uv/).
- Node.js 20+ — для сборки веб-интерфейса.
- Blender 5.2 с аддоном Skybrush Studio — для экспорта.
- Для генерации фигур и ассистента — установленный
  [Pygmalion](https://github.com/Styans/Pygmalion) (его venv, модели и llama-server) и видеокарта
  NVIDIA.
- Плагин раскладки точек берёт [Pointille](https://github.com/Styans/Pointille), ветку
  `atelier-integration`. Путь к его локальной копии задан в `plugins/pointille/pyproject.toml`.

## Установка

```bash
uv sync
npm --prefix apps/web install
npm --prefix apps/web run build
```

Затем скопируйте `atelier.example.toml` в `atelier.toml` и поправьте пути: папка проектов,
`blender.exe`, Pygmalion, llama-server и веса модели. Проверка окружения:

```bash
uv run atelier doctor
```

## Запуск

Веб-интерфейс (API и worker), http://127.0.0.1:8710:

```bash
uv run atelier ui
```

Тот же путь из командной строки:

```bash
uv run atelier new "Юбилей" --drones 150 --duration 240
uv run atelier add-model --glb eagle.glb --name eagle
uv run atelier points
uv run atelier scene add --model m_eagle --hold 20
uv run atelier compile
uv run atelier validate
uv run atelier export
```

Другие команды — `uv run atelier --help`: `status`, `history`, `rollback`, `fleet`, `music`,
`pipeline`, `generate`, `chat`, `plugin`.

## Устройство

```
packages/   ядро: domain, plugin_sdk, plugin_host, show_engine, blender_bridge;
            jobs (SQLite-очередь и чат), assistant (LLM-клиент, инструменты, ход ассистента)
plugins/    реализации ролей: pointille, builtin_*, blender_skybrush, pygmalion
apps/       cli, api (FastAPI), worker, web (React + three.js)
tests/      архитектурные и сквозные тесты
docs/       архитектура, решения, заметки
```

Слои: `domain ← plugin_sdk ← plugin_host ← show_engine ← apps`; ядро никогда не импортирует
плагины, это проверяет `tests/test_architecture.py`. Подробно — [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
обоснования решений — [docs/DECISIONS.md](docs/DECISIONS.md), заметки об ассистенте —
[docs/ASSISTANT_NOTES.md](docs/ASSISTANT_NOTES.md), о Skybrush Studio —
[docs/SKYBRUSH_NOTES.md](docs/SKYBRUSH_NOTES.md).

Соглашения: Z-up, метры, правая система координат, зритель по умолчанию на `-Y`; цвета внутри —
линейные float, на экспорт — sRGB 8 бит; 30 кадров в секунду.

## Разработка

```bash
uv run pytest
uv run pytest -m slow
uv run ruff check .
uv run ruff format .
uv run mypy
npm --prefix apps/web run lint
npm --prefix apps/web run test
```

Живые тесты ассистента запускаются с реальной моделью: `uv run pytest -m llm` при заданных
`ATELIER_LLAMA_EXE` и `ATELIER_LLAMA_MODEL`. Эталон шоу обновляется осознанно:
`ATELIER_UPDATE_GOLDEN=1 uv run pytest tests/e2e`.

## Лицензия

[MIT](LICENSE)
