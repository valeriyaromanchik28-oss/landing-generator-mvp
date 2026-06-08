---
name: landing-generator
description: Используй этот скилл при любой работе с проектом landing-generator-mvp — генератором лендингов для вебинаров Zerocoder. Активируй когда пользователь упоминает "генератор лендингов", "landing-generator", "запустить генератор", "изменить шаблон", "добавить блок в лендинг", "сломалась генерация", "обновить README генератора", "запушить генератор", или работает с файлами в projects/_other/landing-generator-mvp/.
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash
---

# Landing Generator MVP — контекст проекта

## Что это

Flask-сервис, который превращает текст ТЗ вебинара в готовый HTML-лендинг в фирменном стиле Zerocoder. Основной сценарий — горящие сроки, когда нет времени собирать лендинг руками в Tilda.

Репозиторий: https://github.com/valeriyaromanchik28-oss/landing-generator-mvp

## Пути

```
Корень проекта:  /Users/leraromancik/Claude/projects/_other/landing-generator-mvp/
Главный файл:    app.py
Шаблон лендинга: templates/landing-template.html
Форма ввода:     templates/index.html
Страница результата: templates/result.html
Общие ассеты:    shared-assets/
Готовые лендинги: generated/  (в .gitignore — содержат клиентские данные)
Скриншоты для README: screenshots/
GitHub CLI:      ~/bin/gh
```

## Запуск сервиса

```bash
cd /Users/leraromancik/Claude/projects/_other/landing-generator-mvp
python3 app.py
# Сервис поднимается на http://localhost:5050
```

Чтобы остановить зависший сервер:
```bash
pkill -f "python3 app.py"
```

Для тестирования отдельного лендинга без запуска сервера — статический HTTP-сервер:
```bash
python3 -m http.server 5099 --directory /tmp
```

## Архитектура app.py — ключевые части

### Шаг 1: Извлечение данных из ТЗ
`run_extraction(tz_text)` — вызывает Claude Code CLI с `EXTRACTION_PROMPT`, возвращает JSON со всеми полями лендинга. Результат пишется в `last_extraction_raw.txt` (для отладки, в .gitignore).

### Шаг 1.5: Пожелания по дизайну
`run_style_generation(wishes_text)` — отдельный вызов модели с `STYLE_PROMPT`. Возвращает JSON `{font_link, css, corners}`. Поле `corners` обрабатывается кодом через `build_corner_override_css()` — переопределяет CSS-переменные `--r-xl/--r-lg/--r-md` на `:root`, а не правит точечные селекторы.

### Шаг 2: Промпты для аватаров
`run_avatar_prompts(audience, accent_hex, accent2_hex)` — генерирует промпты для Midjourney/ChatGPT если фото спикеров не загружены.

### Шаг 3: Сборка HTML
`assemble_html(data, accent_hex, accent2_hex, custom_style_html, speaker_photos)` — детерминированная подстановка данных в шаблон через словарь `replacements`. **Критично**: плейсхолдеры, которые сами содержат другие плейсхолдеры, должны стоять в словаре РАНЬШЕ — Python 3.7+ обходит dict по порядку вставки.

### Типографика
`glue_short_words(text)` — меняет пробел после коротких (1-3 буквы) слов на неразрывный ` `, чтобы не было висящих предлогов. Подключена в `esc()` и `format_accent_html()` — покрывает весь текстовый контент лендинга.

`hero_title_font_size(title)` — выбирает `clamp()` для `font-size` заголовка hero в зависимости от длины текста (≤45 / ≤75 / ≤110 / длиннее символов).

### Маршруты Flask
- `GET /` — форма ввода ТЗ
- `POST /generate` — генерация лендинга
- `GET /preview/<path>` — просмотр готового лендинга
- `GET /shared-assets/<path>` — общие ассеты (логотипы, иконки)

## Как вносить изменения

### Добавить новый блок в шаблон
1. Добавить HTML-блок в `templates/landing-template.html` с уникальным плейсхолдером вида `{{BLOCK_NAME}}`
2. Добавить функцию-сборщик в `app.py`
3. Добавить запись в словарь `replacements` внутри `assemble_html()` — выше тех плейсхолдеров, которые не зависят от нового
4. Если блок опциональный — использовать `keep_block()` / `drop_block()` (уже есть в коде)

### Изменить промпт извлечения
Промпт — строка `EXTRACTION_PROMPT` в начале `app.py`. После изменения обязательно проверить, что модель возвращает валидный JSON нужной структуры — вставить тестовое ТЗ через форму и проверить `last_extraction_raw.txt`.

### Изменить промпт стилей
Строка `STYLE_PROMPT`. Следить за длиной строк в triple-quoted строках: очень длинные строки с кириллицей (800+ символов) могут вызывать ложный `SyntaxError` в CPython при запуске (`Non-UTF-8 code starting with '\xd0'`). Лечится переносом строк внутри промпта.

## Проверка ошибок

При ошибке `SyntaxError: Non-UTF-8 code` — это не проблема с кодировкой файла, а баг CPython с длинными строками. Решение: разбить длинную строку в промпте на несколько коротких через `\n`.

При ошибке генерации (модель вернула не JSON) — смотреть `last_extraction_raw.txt`, там сырой ответ модели.

Быстрая проверка после изменений:
```bash
cd /Users/leraromancik/Claude/projects/_other/landing-generator-mvp
python3 -c "import app; print('OK')"
```

## Публикация на GitHub

GitHub CLI: `~/bin/gh` (авторизован, аккаунт valeriyaromanchik28-oss).

Перед любым git-действием показать команду и дождаться подтверждения.

```bash
cd /Users/leraromancik/Claude/projects/_other/landing-generator-mvp
git add <файлы>
git commit -m "описание изменений"
git push
```

Что НЕ коммитить: `generated/`, `last_extraction_raw.txt`, `.DS_Store`, `__pycache__/` — всё покрыто `.gitignore`.

## Обновление README

Файл: `/Users/leraromancik/Claude/projects/_other/landing-generator-mvp/README.md`
Скриншоты: папка `screenshots/` в корне проекта, нейминг — латиница, строчные, дефисы.

При добавлении скриншотов в README использовать относительный путь:
```markdown
![Описание](screenshots/filename.png)
```

После любых изменений в README — закоммитить и запушить.
