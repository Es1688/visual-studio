# ADR-0002: формат манифеста ассета

Статус: accepted (2026-10-06)

## Контекст

Нужны воспроизводимость (промпт, параметры, родители) и полный учёт расходов на
каждый файл. Этап 2 добавит видео — формат не должен меняться.

## Решение

Один `manifest.json` на проект (`projects/<p>/manifest.json`), схема —
`schema/manifest.schema.json`, валидация — `qc_image.py --manifest`. Запись:
`id, path, type, provider, model, api_version, operation, prompt, seed, params,
parents, cost_usd, created_at, qc{status}, dry_run?`. Этап 2 добавит значение
`type: "video"` без изменения формы записи. Новые поля — только через изменение
схемы + ADR. Dry-run записи: `cost_usd: 0`, планируемая сумма — в
`params.planned_cost_usd`.

## Последствия

- Учёт расходов (`cost.py spend/check`) читает манифест без отдельной бухгалтерии.
- Происхождение любого файла восстанавливается по цепочке `parents`.
- Дисциплина: каждый созданный файл обязан иметь запись (AGENTS.md, «Манифест»).
