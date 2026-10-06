# media-studio — Image Studio (этап 1)

Студия рекламных изображений над облачным провайдером fal.ai: генерация по брифу,
редактирование по reference, inpainting, upscale, удаление фона, детерминированное
наложение текста/логотипа/CTA, QC и экспорт под площадки. Каждый созданный файл
фиксируется в манифесте с происхождением и стоимостью; расходы ограничены бюджетом
до каждого платного вызова.

- План и стадии: [PLAN.md](PLAN.md) · Правила агента: [AGENTS.md](AGENTS.md) · Решения: [docs/adr/](docs/adr/)

## Возможности

- **text → image** — изображение по текстовому промпту (FLUX);
- **image → image / reference** — правка готовой картинки, перенос товара в новое
  окружение (FLUX Kontext);
- **inpainting** — правка области по маске (FLUX Fill), **удаление фона** (BiRefNet);
- **upscale** — увеличение разрешения (Clarity Upscaler);
- **ресайз/кроп/конвертация** — пресеты площадок 1:1, 4:5, 9:16, 16:9;
- **compose** — наложение текста, логотипа и CTA по HTML-шаблону (модель текст
  не рисует — только детерминированный рендер);
- **batch** — серия из CSV с бюджетом на партию и лимитом конкурентности.

Видео — этап 2, в этом репозитории не предусмотрено.

## Как это устроено

```
brief.md + references/        config/ (budget, capabilities, providers)
        │                              │
        ▼                              ▼
  ┌─────────── ZCode-агент (скиллы image-generate / image-edit / image-qc) ──────────┐
  │  1. оценка стоимости (cost.py estimate)                                          │
  │  2. бюджетный гейт (cost.py / budget_check)                                      │
  │  3. вызов скрипта ──► scripts/*.py ──► fal.ai (queue API, ретраи)                │
  │  4. QC: qc_image.py (детерминированный) + визуальный (агент) + человек           │
  └──────────────────────────────────────────────────────────────────────────────────┘
        │                              │
        ▼                              ▼
  projects/<p>/generated|working/   projects/<p>/manifest.json  ← каждый файл = запись
        │
        ▼ после утверждения человеком
  projects/<p>/exports/
```

Разделение ролей: **AGENTS.md** — правила (бюджет, секреты, принципы);
**скиллы** — что вызывать и когда; **скрипты** — детерминированный слой;
**hook** (`.zcode/config.json`) — принудительный запрет доступа к секретам.

## Установка

Требования: Linux/macOS, Python ≥ 3.11, [uv](https://docs.astral.sh/uv/), git.

```bash
cd media-studio            # каталог репозитория
uv sync                    # зависимости (httpx, pillow, jsonschema, pyyaml, playwright, pytest, ruff)
uv run playwright install chromium   # браузер для compose (~150 МБ, один раз)
```

Ключ провайдера — только через переменную окружения (`.env` скриптами не
подгружается сознательно: см. «Безопасность»):

```bash
export FAL_KEY="..."       # в текущей сессии или в ~/.bashrc
```

На стороне fal.ai включите spending limit на ключ. Перед первым платным запуском
полезно сверить цены/лимиты: `config/capabilities.yaml` (проверено 2026-10-06).

Проверка установки:

```bash
uv run python scripts/cost.py estimate --model flux-dev --aspect square   # ~0.0292 USD
uv run python scripts/gen_image.py --project demo --prompt "test" --dry-run
uv run pytest -q
```

## Быстрый старт

1. Создайте проект:

```bash
mkdir -p projects/myshop/references
cp /путь/к/фото/товара.jpg projects/myshop/references/product.jpg
```

2. Опишите бриф в `projects/myshop/brief.md` (продукт, цель, площадка, форматы,
   точный текст баннера и CTA — тексты пригодятся для compose).

3. Сгенерируйте фон (сначала всегда оценка):

```bash
uv run python scripts/cost.py estimate --model flux-dev --aspect portrait_4_5
uv run python scripts/gen_image.py --project myshop \
    --prompt "cozy autumn morning, wooden table, warm light, space for text" \
    --model flux-dev --aspect portrait_4_5 --count 1
```

Результат: `projects/myshop/generated/myshop-img-0001.png` + запись в манифесте.

4. Наложите текст и CTA:

```bash
cat > projects/myshop/working/banner-data.json <<'EOF'
{
  "headline": "Осенняя распродажа −20%",
  "sub": "Керамическая кружка ручной работы",
  "cta": "Подробнее",
  "logo": "projects/myshop/references/logo.png"
}
EOF
uv run python scripts/compose.py --project myshop --asset myshop-img-0001 \
    --template templates/banner.html \
    --data projects/myshop/working/banner-data.json --ratio 1x1,4x5,9x16
```

5. Проверьте и выгрузите:

```bash
uv run python scripts/qc_image.py --project myshop          # детерминированный QC
# визуальный QC — агент читает картинку; финал утверждаете вы
uv run python scripts/export.py --project myshop --asset myshop-cmp-0002 \
    --preset portrait_4x5 --format webp
cp projects/myshop/working/<файл> projects/myshop/exports/  # после вашего одобрения
```

## Структура репозитория

```
├── AGENTS.md                  # правила агента: границы, секреты, бюджет, принципы
├── PLAN.md                    # план этапа 1, статусы стадий
├── .zcode/
│   ├── config.json            # PreToolUse-hook: запрет доступа к .env/*.key/*.pem
│   └── skills/                # скиллы image-generate / image-edit / image-qc
├── config/
│   ├── providers.yaml         # fal.ai: URL, имя env-переменной, политика провайдера
│   ├── capabilities.yaml      # модели, эндпоинты, цены и единицы, пресеты размеров
│   └── budget.yaml            # лимиты: run/day/project, порог подтверждения, ретраи
├── schema/manifest.schema.json
├── templates/banner.html      # универсальный шаблон баннера (текст/CTA/лого)
├── scripts/                   # детерминированный слой (см. справочник ниже)
├── projects/<name>/
│   ├── brief.md               # бриф
│   ├── references/            # фото товара, логотипы — исходники заказчика
│   ├── generated/             # сырой результат API (в git не попадает)
│   ├── working/               # промежуточные версии (в git не попадает)
│   ├── exports/               # финал после QC и утверждения человеком
│   ├── golden.csv             # набор брифов для batch (в demo)
│   └── manifest.json          # происхождение и стоимость каждого файла
├── logs/                      # отчёты batch (в git не попадает)
├── docs/adr/                  # архитектурные решения
└── tests/                     # pytest, вся сеть замокана
```

## Справочник скриптов

Все команды — из корня репозитория, через `uv run python scripts/<имя>`.
Общие коды возврата: **0** — успех; **2** — конфигурационная ошибка (нет ключа,
ассета, маски, превышен бюджет, неверный аргумент); **4** — ошибка провайдера
после ретраев (только у сетевых скриптов).

### gen_image.py — генерация

```bash
uv run python scripts/gen_image.py --project <p> --prompt "..."
    [--model flux-dev] [--aspect square|landscape_16_9|portrait_9_16|portrait_4_5]
    [--seed N] [--count N] [--ref projects/<p>/references/x.png ...] [--dry-run]
```

- Без `FAL_KEY` реальный запуск невозможен (exit 2); `--dry-run` работает всегда:
  оценка + запись в манифест с `cost_usd: 0` (план — в `params.planned_cost_usd`).
- Бюджет проверяется до вызова API; превышение — exit 2, не обходится.
- Результат — `generated/<id>.png`; при `--count > 1` дополнительные — `<id>-02.png`…

### edit_image.py — редактирование

```bash
# правка по reference: перенести товар в новое окружение (FLUX Kontext)
uv run python scripts/edit_image.py --project <p> --asset <id> --mode edit \
    --prompt "put the mug on a marble table" [--aspect portrait_4_5]

# правка области по маске (FLUX Fill)
uv run python scripts/edit_image.py --project <p> --asset <id> --mode inpaint \
    --prompt "add steam" --mask projects/<p>/references/mask.png

# удаление фона (BiRefNet, бесплатно)
uv run python scripts/edit_image.py --project <p> --asset <id> --mode remove_background
```

- `--mode edit|inpaint` требует `--prompt`; `inpaint` — ещё и `--mask`.
- Результат — `working/<новый-id>.png`, в манифесте `parents: [<id>]`: исходник
  никогда не перезаписывается.

### upscale.py — увеличение разрешения

```bash
uv run python scripts/upscale.py --project <p> --asset <id> [--scale 2] [--model esrgan]
```

`--scale` валидируется по диапазону модели (1–4); стоимость считается от МП
выхода (вход × scale²).

### compose.py — наложение текста/логотипа/CTA

```bash
uv run python scripts/compose.py --project <p> --asset <id> \
    --template templates/banner.html --data data.json --ratio 1x1,4x5,9x16
```

- Рендерит HTML-шаблон в headless Chromium; один шаблон даёт все соотношения —
  раскладка на CSS. Выходы — `working/<id>-cmp-<ratio>.png` (+ записи в манифесте).
- `data.json`: `headline`, `sub`, `cta`, `logo` (путь к файлу или `null`).
- Если Chromium не установлен, скрипт подскажет: `uv run playwright install chromium`.

### export.py — пресеты площадок

```bash
uv run python scripts/export.py --project <p> --asset <id> \
    --preset square_1x1|portrait_4x5|portrait_9x16|landscape_16x9 \
    [--format png|jpg|webp] [--quality 85]
```

Cover-кроп под точный размер пресета (например 1080×1350); результат в `working/`.
В `exports/` файл попадает только вручную, после вашего утверждения.

### qc_image.py — контроль качества

```bash
uv run python scripts/qc_image.py --project <p>          # отчёт по всему проекту
uv run python scripts/qc_image.py --project <p> --min-side 512 --min-std 5
uv run python scripts/qc_image.py --project <p> --asset <id> --set-status pass \
    --note "visual QC ok"
```

Детерминированный уровень: манифест валиден по схеме; файл открывается; сторона
≥ `--min-side`; не однотонное (std яркости ≥ `--min-std`); файл > 1 КБ; aspect
соответствует `params.aspect` (±2%). Записи dry-run без файла помечаются
`planned`, а уже проверенные (`qc.status` проставлен) — `skipped`: они не
пересматриваются автоматически. `--set-status pass|fail|retry` фиксирует
решение (в т.ч. визуальное) в манифесте; чтобы перепроверить ассет, сбросьте
статус на `pending` правкой манифеста.

### cost.py — бюджет

```bash
uv run python scripts/cost.py estimate --model flux-dev --count 3 [--aspect portrait_9_16]
uv run python scripts/cost.py spend --project <p> [--today]
uv run python scripts/cost.py check --project <p> [--planned 0.30]
```

### batch.py — серия из CSV

```bash
uv run python scripts/batch.py --project demo --csv projects/demo/golden.csv \
    [--concurrency 2] [--dry-run] [--limit 3]
```

- CSV с заголовком `prompt,model,aspect,count` (count необязателен, по умолчанию 1);
  пустые строки и `#`-комментарии игнорируются; битая строка не валит партию.
- Сначала префлайт: валидация строк + суммарная оценка против бюджета — при
  превышении exit 2 **до любых вызовов API** с построчными ценами.
- Отчёт — `logs/batch-<timestamp>.json` (по строкам: статус, rc, оценка; итоги).
  Коды возврата: 0 — всё ok; 1 — есть упавшие строки; 2 — префлайт-отказ.

## Модели и цены

Проверено по документации fal.ai 2026-10-06 (`config/capabilities.yaml`):

| Модель (`--model`) | Операция | Цена | Seed |
|---|---|---|---|
| `flux-dev` | text→image | **$0.025 за МП** (square ≈ $0.029, 9:16 ≈ $0.052) | да |
| `flux-pro-kontext` | reference-редактирование | $0.04 за изображение | да |
| `flux-fill` | inpaint | $0.025 за МП входа | да |
| `esrgan` (clarity-upscaler) | upscale | $0.03 за МП **выхода** | — |
| `birefnet` | удаление фона | бесплатно (сверить первое списание) | — |

Цены за мегапиксель умножаются на фактический размер запрашиваемой картинки —
оценка `cost.py estimate --aspect ...` это учитывает. Нюансы: у flux/dev нет
готового 9:16/4:5 (отправляется кастомный размер); у kontext 4:5 достигается
генерацией 3:4 с последующим кропом в export. Политика провайдера (хранение
файлов 1/30 дней по тарифу, NSFW-фильтр по умолчанию, обучение на данных
клиентов не проводится по MSA, лимиты по конкурентности: free 3 / starter 20 /
developer 50) — в `config/providers.yaml`.

## Бюджет

`config/budget.yaml`: `per_run 1.00`, `per_day 5.00`, `per_project 20.00` USD;
`confirm_threshold 0.50`; `max_retries_per_asset 3`.

- Любой платный вызов начинается с оценки; выше порога подтверждения — явное
  «да» от вас (в ZCode агент спросит через AskUserQuestion).
- Дневной и проектный лимиты — жёсткие: скрипт завершается с ошибкой и не
  обходится; ретраи ограничены, каждый ретрай снова проходит бюджетную проверку.
- Dry-run записи не считаются расходом. Расходы видны всегда:
  `cost.py spend --project <p>`.

## Манифест

Каждый созданный файл — запись в `projects/<p>/manifest.json`
(схема: `schema/manifest.schema.json`, проверка: `qc_image.py --manifest ...`):

```json
{
  "id": "demo-cmp-0004",
  "path": "projects/demo/working/demo-img-0002-cmp-4x5.png",
  "type": "image",
  "provider": null,
  "model": null,
  "api_version": null,
  "operation": "compose",
  "prompt": null,
  "seed": null,
  "params": {"template": "templates/banner.html", "ratio": "4x5", "data": {"headline": "..."}},
  "parents": ["demo-img-0002"],
  "cost_usd": 0.0,
  "created_at": "2026-10-06T18:40:12+00:00",
  "qc": {"status": "pass", "checks": [{"check": "set-status", "note": "visual QC PASS"}]}
}
```

`parents` восстанавливает цепочку происхождения (генерация → правка → баннер →
экспорт); `cost_usd` — фактический учёт; формат рассчитан на этап 2
(видео добавится как `type: "video"` без изменения формы записи).

## QC: три уровня

1. **Детерминированный** — `qc_image.py` (схема, разрешение, aspect, однотонность).
2. **Визуальный** — агент читает изображение и проверяет артефакты, соответствие
   брифу и различимость наложенного текста; решение фиксируется
   `--set-status pass|fail|retry`.
3. **Человек** — финал утверждаете вы: файл копируется в `exports/` только после
   вашего одобрения.

Цикл FAIL → правка → повтор ограничен `max_retries_per_asset` и бюджетом;
каждая правка — новый ассет с `parents`, ничего не перезаписывается.

## Работа в ZCode

Скиллы запускаются из чата как слэш-команды или срабатывают по формулировке
запроса:

- `/image-generate` — «сгенерируй баннер …» → промпт → оценка → генерация → QC;
- `/image-edit` — «замени фон», «удали фон», «наложи текст»;
- `/image-qc` — «проверь качество», «прогони QC».

Агент соблюдает правила AGENTS.md: сам прогоняет оценку и спрашивает подтверждения
на дорогие запуски, не трогает `references/` и `exports/` без явной задачи,
каждый файл фиксирует в манифесте. Чтение `.env`/`*.key`/`*.pem` заблокировано
hook'ом на уровне инструментов — вы как человек можете открыть их сами, агент нет.
Долгий batch можно запустить фоновой командой и продолжить работу в сессии.

## Безопасность

- Ключ — только в env (`FAL_KEY`); `.env`, `*.key`, `*.pem` в `.gitignore` и под
  запретом hook'а (`.zcode/config.json`); `.env.example` — образец без значений.
- Spending limit на ключ включается у провайдера — это вторая линия после
  локального бюджета.
- Приватность: загружаемые файлы хранятся на CDN fal.ai 1–30 дней по тарифу —
  не загружайте чувствительное; для заказчика с чувствительными исходниками
  стоит перепроверить условия тарифа (`config/providers.yaml`).

## Разработка

```bash
uv run ruff check .    # линтер
uv run pytest -q       # 69 тестов; вся сеть замокана (httpx.MockTransport), ключ не нужен
```

Тесты независимы от состояния `projects/demo` (снимки «до/после»); новые скрипты
обязаны держать контракт: фикс CLI, запись в манифест по схеме, «пустой запуск»
не ломает манифест, офлайн-тесты без сети. Архитектурные решения — через ADR
(`docs/adr/`). Коммиты — Conventional Commits; push — только с явного разрешения.

## Неисправности

| Симптом | Причина и действие |
|---|---|
| `FAL_KEY is not set` (exit 2) | Ключ не в окружении: `export FAL_KEY=...`. Для оценки без ключа есть `--dry-run`. |
| `BUDGET EXCEEDED` (exit 2) | Лимит дня/проекта из `budget.yaml`. Проверьте `cost.py spend/check`; поднять лимит — осознанная правка конфига. |
| `Chromium is not installed` (exit 2) | `uv run playwright install chromium`. |
| QC: `FAIL monochrome` | Картинка-заглушка или битая генерация — перегенерируйте. |
| `fal.ai error ... HTTP 429` (exit 4) | Лимит конкурентности тарифа — уменьшите `--concurrency` в batch или подождите. |
| Batch упал частично (exit 1) | Подробности построчно в `logs/batch-*.json`; перезапустите — идентичности id не требуются, дубли исключены. |
| Hook отклонил операцию агента | Агент обратился к `.env`/`*.key`/`*.pem` — так и задумано. |

## Что дальше (этап 2)

Манифест, интерфейс провайдеров и QC-каркас готовы к расширению: видео добавится
как `type: "video"` с теми же механизмами. Выбор видеопровайдеров, проверка
Kinocut и лицензии Remotion — в конце этапа 1 (PLAN.md §12).
