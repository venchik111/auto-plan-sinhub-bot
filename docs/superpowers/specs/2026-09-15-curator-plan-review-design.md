# Проверка планирования первокурсников для куратора — дизайн

Дата: 2026-09-15

## Цель

Дать куратору раздел сайта, который проверяет недельные планы первокурсников из
Google Sheets по правилам из листа «Инструкция (куратор)» и выдаёт по каждому
студенту статус, замечания, переформулировки и вопросы для встречи. Формальные
правила проверяет код, смысловые — LLM через уже подключённый OpenCode CLI.

## Решения

| Вопрос | Решение |
|---|---|
| Пользователь | Куратор, на сайте. Таблица только читается, в неё ничего не пишется. |
| Источник | Одна таблица первокурсников: `FRESHMEN_SPREADSHEET_ID`. |
| Доступ | Пароль `CURATOR_PASSWORD` в `.env`, подписанная cookie куратора. |
| Правила | Формальные (код), качество целей (LLM), качество задач (LLM), итоги недели (код + LLM). |
| Запуск LLM | Вся группа в фоновой очереди и отдельный студент по кнопке; результаты кешируются. |
| Отчёт | Общий статус, замечания по правилам, вопросы для встречи, переформулировки. |
| Подход | Гибрид: правила — чистые функции, LLM получает одного студента за одну неделю. |

## Исходные данные

Таблица «Планирование Новочебоксарск. 1 курс».

- Служебные вкладки, которые пропускаются: «Инструкция (куратор)», «Инструкция (студент)»,
  «Дашборд куратора», «Список студентов». Вкладка «пример» не студент, но читается
  как эталон для правила `example_copy`.
- Остальные вкладки — студенты, название вкладки = ФИО.
- Колонки (7): `Неделя | Цели недели | Сфера | Задача | День | Статус | Рефлексия`.
  Колонки планового времени нет.
- Неделя в формате `14.09-20.09` (как `app/weeks.py:week_label`).
- Цели и рефлексия заполняются в первой строке недели; каждая строка недели — задача.
- Статусы: `✅` сделано, `⏳` в процессе, `❌` не сделано.
- Сферы: База, Профиль (академические); Коллектив, Спорт, Личное (неакадемические).

## Архитектура

```
app/
  opencode_cli.py      запуск `opencode run`: перебор моделей, разбор JSON-событий (вынесено из llm.py)
  llm.py               планировщик; вызывает opencode_cli, поведение не меняется
  review/
    __init__.py
    sheet.py           чтение таблицы → StudentWeek
    rules.py           формальные правила → list[Finding]
    analyzer.py        промпт и разбор ответа LLM → LlmReview
    service.py         сборка отчёта, хеш данных, кеш, итоговый статус
    queue.py           фоновая asyncio-очередь анализа
  curator_web.py       APIRouter /api/curator/*, вход по паролю, страница /curator
web/
  curator.html
  static/curator.js    стили переиспользуются из static/style.css
opencode/opencode.json агент "reviewer" с теми же запретами, что у planner
```

`web.py` только подключает роутер: `app.include_router(curator_router)`. Если не
заданы `FRESHMEN_SPREADSHEET_ID` или `CURATOR_PASSWORD`, роутер не подключается,
студенческий сайт работает как раньше.

Границы модулей:

- `rules.py` не зависит от Google и LLM: `evaluate(week: StudentWeek, example: StudentWeek | None, today: date) -> list[Finding]`.
- `analyzer.py` не знает про Sheets и SQLite: `async analyze(week, formal_findings) -> LlmReview`.
- `sheet.py` не знает про правила: `read_group() -> Group` (студенты → недели).
- `service.py` связывает всё и работает с `Database`.

### Конфигурация

Новые переменные в `Settings` и `.env.example`:

- `FRESHMEN_SPREADSHEET_ID` — ID таблицы первокурсников (по умолчанию пусто).
- `CURATOR_PASSWORD` — пароль куратора (по умолчанию пусто).
- `OPENCODE_REVIEW_AGENT` — агент OpenCode для анализа, по умолчанию `reviewer`.

Модели и таймаут берутся из существующих `OPENCODE_MODELS` и `OPENCODE_TIMEOUT_SECONDS`.
Для чтения используется тот же сервисный аккаунт с областью `spreadsheets.readonly`.

## Модель данных

```python
@dataclass(frozen=True)
class ReviewTask:
    index: int            # 1-based порядок в неделе
    sphere: str           # как в таблице, может быть пустым
    text: str
    day: str
    status: str           # "✅" | "⏳" | "❌" | ""

@dataclass(frozen=True)
class StudentWeek:
    student: str          # название вкладки
    week_label: str
    goals: tuple[str, ...]  # разбитые по строкам/нумерации, без номеров
    goals_raw: str
    tasks: tuple[ReviewTask, ...]
    reflection: str

@dataclass(frozen=True)
class Finding:
    source: str           # "rules" | "llm"
    rule: str             # код правила
    severity: str         # "blocker" | "warning" | "advice"
    target: str           # "week" | "goal:N" | "task:N" | "day:Пн" | "reflection"
    message: str

@dataclass(frozen=True)
class Rewrite:
    target: str
    original: str
    suggestion: str

@dataclass(frozen=True)
class LlmReview:
    findings: tuple[Finding, ...]
    rewrites: tuple[Rewrite, ...]
    questions: tuple[str, ...]
    summary: str
    model: str
```

Разбор целей: ячейка делится по переводам строк; ведущие `1.`, `1)`, `-`, `•`
удаляются; пустые строки отбрасываются. Если перевода строки нет, но есть
нумерация `1. … 2. …` внутри строки, делим по ней.

## Формальные правила (`rules.py`)

| Код | Условие | Уровень | Target |
|---|---|---|---|
| `no_plan` | На неделю нет ни одной строки | blocker | week |
| `example_copy` | Нормализованные цели и тексты задач совпадают с листом «пример» за ту же неделю | blocker | week |
| `goals_count` | Целей < 2 или > 4 | warning | week |
| `spheres_count` | Различных сфер > 4 → warning; ровно 4 → advice | warning / advice | week |
| `tasks_per_day` | В один день > 3 задач | warning | day:X |
| `missing_fields` | У задачи пустые сфера, день или текст | warning | task:N |
| `no_balance` | Все задачи академические или все неакадемические (при ≥ 2 задачах) | advice | week |
| `statuses_missing` | Неделя закончилась, у части задач нет статуса | warning | week (одно замечание, в тексте номера задач) |
| `reflection_missing` | Неделя закончилась, рефлексия пустая | warning | reflection |

«Неделя закончилась» — `today` позже воскресенья недели в `APP_TIMEZONE`.
Нормализация текста — как `SheetsRepository._normalized`.
При `no_plan` / `example_copy` остальные правила не выполняются.

## Смысловой анализ (`analyzer.py`)

Системный промпт содержит выдержку правил из «Инструкции (куратор)»: 4 признака
выполнимой цели, проверочный вопрос «как в пятницу пойму, что выполнено на 100%»,
задача — физическое действие, частые ошибки по сферам, тон наставника («эксперимент»,
«план Б»). Данные студента помечены как данные, а не инструкции.

Пользовательская часть: неделя, закончилась ли она, пронумерованные цели,
пронумерованные задачи со сферой, днём и статусом, рефлексия (только для
закончившейся недели), список уже найденных формальных замечаний («не повторяй»).

Ответ — только JSON:

```json
{
  "findings": [{"target": "goal:2", "rule": "measurable", "severity": "warning", "message": "…"}],
  "rewrites": [{"target": "goal:2", "original": "…", "suggestion": "…"}],
  "questions": ["…"],
  "summary": "…"
}
```

Допустимые `rule`: `measurable`, `weekly_scope`, `self_dependent`,
`positive_wording`, `physical_action`, `task_goal_link`, `sphere_mismatch`,
`vague_task`, `reflection_quality`. Допустимые `severity`: `warning`, `advice`.
`target` должен ссылаться на существующую цель или задачу, либо быть `week`/`reflection`.

Валидация: JSON извлекается как в `OpenAICompatibleLLM._extract_json`; элементы с
неизвестным `rule`, `severity` или несуществующим `target` отбрасываются; `questions`
обрезаются до 4, `summary` до 500 символов. Если после разбора нет объекта верхнего
уровня — ошибка разбора, `opencode_cli` пробует следующую модель.

## `opencode_cli.py`

Выносится из `llm.py` без изменения поведения:

```python
class OpenCodeRunner:
    def __init__(self, settings: Settings, agent: str): ...
    async def run_with_fallback(self, prompt: str, parse: Callable[[str], T]) -> tuple[T, str]:
        """Перебирает модели; возвращает результат parse и имя модели.
        LLMError, если ни одна модель не дала разбираемый ответ."""
```

`OpenAICompatibleLLM._generate_with_opencode` переходит на `OpenCodeRunner(settings, settings.opencode_agent)`.
`LLMError` переезжает в `opencode_cli.py` и реэкспортируется из `llm.py`.

## Итоговый статус (`service.py`)

- `empty` («не заполнено») — есть blocker. LLM не вызывается.
- `discuss` («нужно обсудить») — warning ≥ 3 (формальные + LLM).
- `remarks` («есть замечания») — warning 1–2.
- `ok` («всё хорошо») — warning нет.

К статусу прилагается `llm_state`: `none` (не запускался), `fresh`, `stale`
(хеш данных изменился), `queued`, `running`, `error`.

## Кеш

Таблица SQLite `review_results`, создаётся в `Database.init()`:

```sql
CREATE TABLE IF NOT EXISTS review_results (
    student TEXT NOT NULL,
    week_label TEXT NOT NULL,
    data_hash TEXT NOT NULL,
    llm_json TEXT,
    model TEXT,
    error TEXT,
    analyzed_at TEXT NOT NULL,
    PRIMARY KEY (student, week_label)
)
```

`data_hash` — sha256 от канонического JSON `StudentWeek` (цели, задачи, статусы,
рефлексия). Формальные правила в кеш не пишутся: пересчитываются при каждом запросе.
Если сохранённый `data_hash` не совпадает с текущим — `llm_state = stale`, старый
результат показывается с пометкой «данные изменились».

## Очередь (`queue.py`)

- Одна `asyncio.Queue`-подобная структура (deque + `asyncio.Event`) и один воркер,
  стартует при запуске приложения (`lifespan`/`startup`).
- `enqueue_group(week)` — все студенты недели со статусом не `empty` и
  `llm_state ∈ {none, stale, error}`; в конец очереди.
- `enqueue_student(student, week)` — в начало очереди, анализ даже при `fresh`.
- Дедупликация по `(student, week)`: уже стоящий в очереди не добавляется повторно
  (запрос одного студента переносит его в начало).
- Перед анализом воркер заново читает данные студента, чтобы хеш соответствовал
  проанализированным данным.
- Ошибка одного студента пишется в `review_results.error`, очередь продолжается.
- Прогресс в памяти: `{week, total, done, failed, current}`. После перезапуска
  сервера очередь теряется, сохранённые результаты остаются.

## API (`curator_web.py`)

Все маршруты, кроме входа и самой страницы, требуют cookie `auto_plan_curator`.

| Метод | Путь | Описание |
|---|---|---|
| GET | `/curator` | Страница куратора (форма входа показывается, если нет cookie) |
| POST | `/api/curator/login` | `{password}` → cookie; сравнение `hmac.compare_digest` |
| POST | `/api/curator/logout` | удалить cookie |
| GET | `/api/curator/review?week=` | список студентов: статус, `llm_state`, число warning/advice |
| GET | `/api/curator/review/student?name=&week=` | полный отчёт: findings, rewrites, questions, summary, model, analyzed_at, error |
| POST | `/api/curator/analyze` | `{week, student?}` → поставить в очередь |
| GET | `/api/curator/queue` | прогресс очереди |

Cookie: значение `HMAC-SHA256(CURATOR_PASSWORD, "curator")` в hex, `httponly`,
`samesite=lax`, 30 дней. Смена пароля инвалидирует cookie. Неверный пароль — 401
и задержка 1 с.

Имя студента в запросах проверяется по списку вкладок; неизвестное — 404.

## Экран `/curator`

- Шапка: выбор недели (как на сайте студента: календарь и стрелки), кнопка
  «Проанализировать группу», полоса прогресса «Проанализировано N из M».
- Левая колонка: студенты, отсортированные `discuss → remarks → ok → empty`,
  цветная метка статуса, счётчики замечаний, значок состояния LLM.
- Правая колонка: отчёт выбранного студента — сводка LLM, замечания по группам
  (неделя, цели, задачи, рефлексия), переформулировки «было → стало», вопросы
  для встречи, кнопка «Проанализировать заново», модель и время анализа, ошибка.
- При ширине < 768px колонки идут друг под другом.
- Пока очередь не пуста, страница опрашивает `/api/curator/queue` каждые 3 с и
  обновляет список.

## Ошибки

- Google Sheets недоступен / нет доступа → 502 с текстом, страница показывает сообщение.
- OpenCode не справился ни одной моделью / таймаут → `llm_state = error`, текст
  ошибки в отчёте, статус считается по формальным правилам.
- Не настроен раздел куратора → маршруты `/curator*` отдают 404.

## Тесты

Без сети, pytest:

- `tests/test_review_rules.py` — кейсы на каждое правило, включая границы
  (2 и 4 цели, 3 и 4 задачи в день, неделя идёт / закончилась).
- `tests/test_review_sheet.py` — разбор фейковых `values`: группировка по неделям,
  цели из первой строки, разбор нумерации целей, пропуск служебных вкладок, пустые строки.
- `tests/test_review_analyzer.py` — разбор корректного JSON, JSON в markdown,
  лишнего текста, битого JSON; отбрасывание неизвестных `rule`/`target`.
- `tests/test_review_service.py` — итоговый статус, хеш и `stale`.
- `tests/test_review_queue.py` — порядок, приоритет одного студента, дедупликация,
  продолжение после ошибки (фейковый анализатор).
- `tests/test_curator_web.py` — вход по паролю, 401 без cookie (TestClient, фейковые зависимости).
- Существующие тесты остаются зелёными после выноса `opencode_cli.py`.

## Вне рамок

- Запись результатов в Google Sheets.
- Несколько групп/таблиц.
- Уведомления студентам.
- Проверка планового времени (нет колонки в таблице).
