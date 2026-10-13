---
name: image-edit
description: Редактирование изображений — правка по reference, inpainting, удаление фона, upscale, ресайз и конвертация, наложение текста/логотипа/CTA по шаблону. Использовать, когда пользователь просит изменить, доработать картинку, вставить товар в новое окружение, увеличить разрешение или собрать баннер из готового изображения. Use for reference-edit, inpaint, background removal, upscale, resize/convert and template compositing.
---

# image-edit

Статус: реализован (P3: edit/inpaint/remove_background и upscale; P4: compose).
Реальные правки и генерация требуют `FAL_KEY`; compose детерминирован и работает
офлайн.

Работаем только в активном проекте (`project.py use <p>`): входы (`--mask`,
`--template`, `--data`) — из `projects/<p>/`; шаблон — свой
`projects/<p>/templates/<name>.html` или общий `templates/<name>.html`
(читается). Соседние проекты не открываем.

- **Редактирование по reference** (товар/стиль/персонаж, новое окружение):
  `uv run python scripts/edit_image.py --project <p> --asset <id> --mode edit`
  `--prompt "..." [--ref path ...]`. Товар с реального фото подавать как
  `--ref` из `references/`; при необходимости точной формы/цвета — генерировать
  окружение и композитить товар, не просить модель его нарисовать.
- **Inpainting / правка областей**: `--mode inpaint [--mask path]`.
- **Удаление фона**: `--mode remove_background`.
- **Upscale**: `uv run python scripts/upscale.py --project <p> --asset <id>`
  `[--scale 2] [--model esrgan]`.
- **Ресайз/кроп/конвертация и пресеты площадок** (P1+): `export.py`.
- **Наложение текста, логотипа, CTA** (только так, модель текст не рисует):
  `uv run python scripts/compose.py --project <p> --asset <id>`
  `--template projects/<p>/templates/<name>.html --data projects/<p>/data.json`
  `--ratio 1x1,4x5,9x16`.

Результат скрипт кладёт в `working/` (или `exports/` для финала) и пишет в
манифест с `parents: [<исходный ассет>]`. После — `qc_image.py` и визуальная
проверка (Read). Ошибки и лимиты ретраев — как в AGENTS.md («Бюджет»);
тексты/CTA берутся из брифа или `data.json`, не выдумываются.
