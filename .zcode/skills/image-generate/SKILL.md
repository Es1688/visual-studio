---
name: image-generate
description: Сгенерировать изображение по брифу (text→image) через scripts/gen_image.py. Использовать, когда пользователь просит создать или сгенерировать картинку, изображение, рекламный креатив, превью или основу баннера. Use when the user asks to generate an image from a brief or prompt.
---

# image-generate

Статус: реализован (P1). Реальный запуск требует `FAL_KEY`; без ключа работает
`--dry-run` (оценка стоимости + запись в манифест без вызова API).

1. Прочитай бриф: `projects/<project>/brief.md` и `references/` (если есть).
   Если в брифе нет цели, площадки или формата — уточни у пользователя.
2. Составь промпт по брифу. Текст, логотипы, цены и CTA в промпт не включать —
   их накладывает `compose.py` (правила — в AGENTS.md).
3. Выбери модель по `config/capabilities.yaml` (операция, aspect, цена) и оцени
   стоимость: `uv run python scripts/cost.py estimate --model <id> --count N`.
   Оценка выше `confirm_threshold` из `config/budget.yaml` → сначала покажи
   пользователю оценку и получи подтверждение.
4. Генерация: `uv run python scripts/gen_image.py --project <p> --prompt "..."`
   `--model <id> --aspect <ratio> [--seed N] [--ref path ...] [--count N]`.
   Скрипт сам проверит бюджет, сохранит файл в `generated/` и запишет манифест.
5. Проверка: `uv run python scripts/qc_image.py --project <p>`, затем прочитай
   изображение (Read) и сверь с брифом.
6. Отчитайся: путь к файлу, id ассета, фактическая стоимость, статус QC.

Ошибки: бюджет превышен — не обходить, доложить; ошибка API — не больше
`max_retries_per_asset` попыток (каждая через оценку), дальше — эскалация
пользователю. Правила проекта — в AGENTS.md, стадии — в PLAN.md §10.
