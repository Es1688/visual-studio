---
name: marketplace-card
description: Карточка товара для маркетплейса (WB/Ozon/Яндекс Маркет) по методологии
docs/methodology/marketplace-card.md — бриф селлера, план карточки (блоки A–F),
серия слайдов 3:4, QC площадок. Работает ТОЛЬКО в projects/marketplace (двойной
гейт: имя проекта + profile.yaml type: marketplace). Use when the user asks to
make a marketplace product card in the marketplace project.
---

# marketplace-card

Статус: P7. Специализированный скилл (ADR-0004): активен только в каталоге
`projects/marketplace`. Студия в остальном универсальна — базовые правила в
AGENTS.md, методология в docs/methodology/marketplace-card.md (здесь не
дублируется), профиль площадки — `projects/marketplace/profile.yaml`.

## Шаг 0. Гейт (проверять первым)

Активный проект должен быть `marketplace` (`uv run python scripts/project.py status`)
и содержать `profile.yaml` с `type: marketplace`. Иначе — отказ с подсказкой:
`project.py use marketplace`; в других каталогах функционал карточек недоступен.

## Workflow (Части 1–2 методологии)

1. **Бриф заказа.** Заполни Часть 1 методологии вместе с пользователем →
   `projects/marketplace/brief-<order>.md` (`<order>` — короткое имя товара,
   латиницей). Пустые поля брифа = «нет», не выдумывать. Существующий
   `brief.md` (заказ «кабель») не трогать. Фото товара — в `references/`.

2. **План карточки.** По Части 2 методологии (блоки A–F, данные только из
   брифа, `[УТОЧНИТЬ]` вместо додумывания) составь и покажи пользователю:
   - `card-plan-<order>.md` — название ×3, раскадровка 7–10 слайдов
     (цель/возражение/кадр/заголовок 2–4 слова/тезисы), описание,
     характеристики + поисковые запросы, самопроверка (блок F);
   - `card-theme-<order>.json` — палитра 2+1 (например `{"accent": "#ff7a00"}`),
     словарь для data.json слайдов.
   Дождись утверждения плана пользователем.

3. **Бюджет.** Скоуп серии: запомни id последней записи манифеста
   (`marketplace-…-<NNNN>`, для `--since-id` на шаге QC). Пре-оценка:
   `uv run python scripts/cost.py estimate --model flux-dev --count <число
   генераций>` (каждый фон/сценарий = 1 генерация; вырезка, compose и QC
   бесплатны). Выше `confirm_threshold` ($0.50) → подтверждение пользователя
   (AskUserQuestion).

4. **Производство слайдов** (по утверждённой раскадровке; правило AGENTS.md —
   текст/лого/CTA модель не рисует):
   - Слайды 1–3 (обложка/деталь/в использовании): генерация сцены
     `uv run python scripts/gen_image.py --project marketplace
     --prompt "..." --aspect portrait_3_4 [--ref references/<фото>]`
     — товар с реального фото как reference; для слоёв —
     `edit_image.py --mode remove_background`, композит через `{{logo}}`-слот.
   - Слайды 4+ (инфографика): фон генерацией (`--aspect portrait_3_4`), затем
     `uv run python scripts/compose.py --project marketplace
     --asset <фон-id> --template projects/marketplace/templates/<mp-*.html>
     --data working/<order>-slide-NN.json --ratio 3x4`.
   - Шаблоны: `mp-cover.html` (сл.1), `mp-benefits.html` (сл.4),
     `mp-specs.html` (сл.5), `mp-scenario.html` (сл.6), `mp-trust.html`
     (сл.7–8); bespoke-шаблон заказа — в `projects/marketplace/templates/`
     по контракту `banner.html` (все значения слайдов — из data.json).
   - Данные слайда: `working/<order>-slide-NN.json`, тексты — из утверждённого
     card-plan (не выдумывать на месте). Banned-фразы из профиля не использовать.

5. **QC.** Детерминированный:
   `uv run python scripts/qc_image.py --project marketplace --marketplace <ozon|wb|yandex|universal>
   --since-id <id из шага 3>` — разрешение/вес/формат/целевое ratio 3:4
   (deliverable-ассеты)/banned-фразы. Визуальный (скилл image-qc, чек-лист
   блока F методологии): артефакты, соответствие плану, читаемость в
   миниатюре, доля товара в кадре ≥70% на обложке. Вердикты — `--set-status`.
   FAIL → правка новой версией с `parents` (≤ `max_retries_per_asset`).

6. **Экспорт.** Покажи слайды пользователю; после явного утверждения —
   `export.py --preset portrait_3x4` при необходимости и копирование в
   `exports/` (человек) + запись `operation: "export"` с `qc.status:
   "approved"`. Отчёт: id ассетов, стоимость, что осталось `[УТОЧНИТЬ]`.

## Чего не делать

- Не запускать пайплайн карточек вне `projects/marketplace` (изоляция — ADR-0004).
- Не менять схему манифеста и профиль площадки без задачи; параметры площадок
  (Часть 3 методологии) сверять по актуальной справке площадки перед загрузкой.
- Бюджет и ретраи — по AGENTS.md («Бюджет»); каждая генерация — через оценку.
