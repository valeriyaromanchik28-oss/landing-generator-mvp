"""MVP-сервис генерации лендингов вебинаров Zerocoder.

Архитектура: маленький вызов Claude Code только для извлечения переменных
из ТЗ в компактный JSON, затем детерминированная сборка HTML в Python
из параметризованного шаблона (без участия модели)."""

import datetime
import html
import json
import random
import re
import subprocess
from pathlib import Path

from flask import Flask, render_template, request, send_from_directory, url_for

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "templates" / "landing-template.html"
GENERATED_DIR = BASE_DIR / "generated"
PLACEHOLDER_IMG = "../../shared-assets/placeholder.svg"
ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}

app = Flask(__name__)


# ---------------------------------------------------------------------------
# Шаг 1. Извлечение переменных из ТЗ через маленький вызов Claude (только текст)
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """Извлеки из текста ТЗ вебинара структурированные данные и верни ТОЛЬКО валидный JSON — без markdown-обёртки, без пояснений до или после.

Формат:
{{
  "slug": "латиницей-через-дефис, по теме или имени спикера",
  "title": "название вебинара ЗАГЛАВНЫМИ; чтобы выделить акцентное слово — оберни его в *звёздочки*; перенос строки обозначь \\n",
  "subtitle": "подзаголовок — вопрос или конкретный результат",
  "event_date": "дата и время эфира, например '22 мая · 13:00 МСК · Прямой эфир'",
  "speakers": [{{
    "name": "имя спикера",
    "role": "роль/должность",
    "tags": ["короткий тег экспертизы", "..."],
    "facts": ["факт об экспертизе или проектах", "..."]
  }}, "... второй объект, только если в ТЗ явно два спикера эфира — иначе массив из одного элемента"],
  "gifts": [{{"name": "название подарка", "desc": "краткое описание"}}, "... столько объектов, сколько подарков явно перечислено в ТЗ (обычно 1, но может быть 2-3) — не выдумывай лишние"],
  "agenda": {{"intro": "вводная фраза перед списком программы (можно пустую строку)", "items": [{{"title": "краткая суть пункта программы", "desc": "более развёрнутое пояснение к пункту, если оно есть в ТЗ — иначе пустая строка"}}, "..."]}},
  "tariff_paid": {{"price": "цена, например '499 ₽'", "old_price": "фраза про обычную цену, например 'Стратегическая сессия — обычно 10 000 ₽'", "bonuses": ["бонус платного тарифа", "..."]}},
  "audience": [{{"who": "название роли с запятой в конце, например 'Руководителей и предпринимателей,'", "desc": "их боль", "result": "что они получат"}}, ...],
  "glossary": [{{"term": "термин", "def": "определение"}}, ...]
}}

ГЛАВНОЕ ПРАВИЛО: используй только тексты из ТЗ, не придумывай и не дополняй.
Если для блока "gifts", "agenda", "tariff_paid" нет информации в ТЗ — поставь null.
Если "audience" или "glossary" не указаны явно — поставь null.

Не используй инструменты, не читай и не записывай файлы — просто верни JSON в текстовом ответе.

ТЗ вебинара:
\"\"\"
{tz_text}
\"\"\"
"""


def run_extraction(tz_text):
    prompt = EXTRACTION_PROMPT.format(tz_text=tz_text.strip())
    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--model", "sonnet",
                "--mcp-config", '{"mcpServers": {}}',
                "--strict-mcp-config",
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
    except subprocess.TimeoutExpired:
        return None, "Claude не ответил за отведённое время. Попробуй сократить ТЗ или повторить."

    if result.returncode != 0:
        return None, "Claude завершился с ошибкой:\n" + (result.stderr.strip() or result.stdout.strip())

    raw = result.stdout.strip()
    # на случай если модель всё же обернула ответ в ```json ... ```
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    (BASE_DIR / "last_extraction_raw.txt").write_text(raw, encoding="utf-8")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"Не удалось разобрать ответ модели как JSON ({e}):\n\n" + raw

    return data, None


# ---------------------------------------------------------------------------
# Шаг 1.5 (опционально). Пожелания по дизайну → дополнительный CSS поверх шаблона
# ---------------------------------------------------------------------------

STYLE_PROMPT = """Ты помогаешь стилизовать HTML-лендинг по пожеланию дизайнера. Сгенерируй ДОПОЛНИТЕЛЬНЫЕ CSS-правила, которые лягут поверх готового шаблона и применят пожелание, не сломав вёрстку.

Пожелание дизайнера:
\"\"\"
{wishes}
\"\"\"

КОНТЕКСТ ШАБЛОНА — используй точные селекторы из этого списка, не выдумывай новые:
- .hero, .hero h1, .hero-sub, .live-chip — главный экран (заголовок, подзаголовок, плашка с датой)
- .btn, .btn-lime — кнопки
- .dark-h, .sec-dark — секция со спикерами (тёмный фон)
- .sec h2 — заголовки остальных секций
- .agenda-item, .a-num — блок программы эфира
- .pcard, .pcard-paid — карточки тарифов
- .acard — карточки целевой аудитории
- body — общий шрифт и фон сайта

Доступные CSS-переменные (используй var(--имя), не переопределяй сами значения):
--lime/--lime2/--lime3 (основной акцент), --red/--red2/--red3 (второй акцент),
--dark/--dark2 (тёмные фоны), --white/--off (светлые фоны), --text/--sub (цвета текста)

Базовый шрифт сайта — 'Manrope'.

ПРАВИЛА:
1. Меняй ТОЛЬКО то, что прямо просит пожелание (например, "шрифт на главном экране" — трогай только селекторы внутри .hero, остальное не задевай).
2. Разрешено менять только типографику и декоративные свойства: font-family, font-size, font-weight, letter-spacing, text-transform, color, text-shadow, border-radius, background (в рамках переменных выше). ЗАПРЕЩЕНО трогать layout-свойства: display, position, flex, grid, width, height, margin, padding, top/left/right/bottom — это сломает адаптивную вёрстку.
3. Если для пожелания нужен нестандартный шрифт (например, "пиксельный шрифт") — подбери подходящий реальный шрифт из Google Fonts и верни готовый тег подключения в поле "font_link". Если свой шрифт не нужен — оставь "font_link" пустой строкой. Размер заголовка .hero h1 уже подбирается отдельно по длине текста — можешь не беспокоиться о его величине при смене шрифта.
4. Если пожелание невозможно выполнить через CSS (просьба поменять текст, структуру, картинки) — верни пустые строки в обоих полях.
4a. ОСОБЫЙ СЛУЧАЙ — просьба сменить общую тему или палитру сайта целиком
("сделай светлую тему", "сделай тёмную тему", "перекрась весь сайт", "поменяй цветовую гамму на ...").
Это СТРУКТУРНАЯ переделка, а не точечная правка: фоны тёмных и светлых секций (.hero, .dark-h, .sec-dark и т.д.)
заданы базовыми стилями шаблона отдельно от цвета текста и переопределению не подлежат (см. правило 2).
Если попытаться выполнить такую просьбу только через цвет текста — получится тёмный текст на тёмном фоне
или наоборот, и сайт станет нечитаемым. Поэтому при любой просьбе сменить ОБЩУЮ тему/палитру сайта —
считай её невыполнимой и возвращай пустые строки в обоих полях, как в правиле 4.
Можно менять оттенок ОТДЕЛЬНЫХ перечисленных в контексте элементов (например, "сделай кнопки бордовыми"),
но не тему/палитру целиком.
4b. ОСОБЫЙ СЛУЧАЙ — просьба изменить форму углов ПО ВСЕМУ САЙТУ
("сделай углы острыми/прямыми", "убери скругления", "сделай сайт более круглым/мягким", "скругли всё").
НЕ пиши под неё точечный CSS — правильно перебрать вообще все элементы с border-radius через селекторы
невозможно, получится половинчатый разнобой. Вместо этого верни в поле "corners" одно из значений:
"sharp" — если просят острые/прямые углы и убрать скругления, "soft" — если просят более скруглённые/мягкие
углы, или "" — если такой просьбы нет. Это применится ко всему сайту автоматически на уровне кода.
Точечные просьбы про форму ОТДЕЛЬНЫХ перечисленных элементов (например, "сделай кнопки квадратными")
по-прежнему оформляй обычным CSS по правилу 1, а поле "corners" оставляй пустым.
5. Верни ТОЛЬКО валидный JSON, без markdown-обёртки и БЕЗ ЛЮБЫХ пояснений на естественном языке — ни до, ни после JSON. Это касается и случаев, когда правило 4 или 4а сработало и поля пустые: всё равно верни ТОЛЬКО JSON-объект, не объясняя почему.
{{"font_link": "<link ...> или пустая строка", "css": "CSS-правила текстом без тега <style>, или пустая строка", "corners": "sharp, soft или пустая строка"}}
"""


_CORNER_OVERRIDES = {
    # var(--r-xl/lg/md) разъезжаются почти по всем карточкам и блокам шаблона —
    # переопределяя сами переменные, получаем единый стиль углов сразу везде,
    # не перечисляя десятки селекторов и не трогая круглые элементы (50%, точки, иконки).
    "sharp": (
        ":root { --r-xl: 0px; --r-lg: 0px; --r-md: 0px; }\n"
        ".btn, .btn-lime, .live-chip, .p-badge, .sf-tag, .sf-tag-red { border-radius: 0; }"
    ),
    "soft": ":root { --r-xl: 36px; --r-lg: 28px; --r-md: 20px; }",
}


def build_corner_override_css(corners):
    return _CORNER_OVERRIDES.get((corners or "").strip(), "")


def run_style_generation(wishes_text):
    wishes_text = (wishes_text or "").strip()
    if not wishes_text:
        return ""

    prompt = STYLE_PROMPT.format(wishes=wishes_text)
    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--model", "sonnet",
                "--mcp-config", '{"mcpServers": {}}',
                "--strict-mcp-config",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("Пожелания по дизайну: модель не ответила вовремя — лендинг соберётся без них.")
        return ""

    if result.returncode != 0:
        print("Пожелания по дизайну: модель завершилась с ошибкой — лендинг соберётся без них.")
        return ""

    raw = result.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # модель иногда добавляет пояснения текстом вокруг JSON — выдёргиваем сам объект
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        raw = match.group(0)

    try:
        style = json.loads(raw)
    except json.JSONDecodeError:
        print("Пожелания по дизайну: не удалось разобрать ответ модели — лендинг соберётся без них.")
        return ""

    font_link = (style.get("font_link") or "").strip()
    css = (style.get("css") or "").strip()
    corner_css = build_corner_override_css(style.get("corners"))

    style_blocks = [block for block in (css, corner_css) if block]

    parts = []
    if font_link:
        parts.append(font_link)
    if style_blocks:
        parts.append(f"<style>\n{chr(10).join(style_blocks)}\n</style>")
    return "\n  ".join(parts)


AVATAR_PROMPT = """Ты помогаешь подготовить промты для Midjourney — портреты персонажей целевой аудитории вебинара, в едином визуальном стиле с лендингом.

Стиль и структура — строго по этому образцу (это реальный промт, который уже использовался в проекте):
"Hyper-realistic waist-up portrait of a CEO, age 45, sitting in a bright minimalist office. Looking directly at camera with neutral calm expression. Clean bright lighting with mint green and deep red accents. Realistic skin texture. Soft ambient mint light from laptop screen with subtle red elements in background. Shallow depth of field f/1.8, 8K, Hasselblad, corporate bright atmosphere."

Акцентные цвета этого лендинга — используй именно их вместо mint green / deep red из образца:
{accent_name_1} и {accent_name_2}

Сегменты целевой аудитории из ТЗ (на русском):
{segments_block}

ПРАВИЛА:
1. На каждый сегмент — один промт, ПОЛНОСТЬЮ на английском, по структуре образца: тип кадра, правдоподобный возраст и обстановка (придумай исходя из описания роли), выражение лица, акцентные цвета в свете и фоне, технические параметры камеры в конце.
2. Не переводи описание роли дословно — преврати его в живой визуальный образ человека и окружения.
3. Верни ТОЛЬКО валидный JSON-массив строк в том же порядке, что и сегменты, без markdown и пояснений:
["промт для сегмента 1", "промт для сегмента 2", ...]
"""


def run_avatar_prompts(audience, accent_hex, accent2_hex):
    audience = audience or []
    if not audience:
        return []

    accent_name_1 = nearest_color_name(accent_hex)
    accent_name_2 = nearest_color_name(accent2_hex or accent_hex)

    segments_block = "\n".join(
        f'{i + 1}. {seg.get("who", "")} — {seg.get("desc", "")}'
        for i, seg in enumerate(audience)
    )
    prompt = AVATAR_PROMPT.format(
        accent_name_1=accent_name_1,
        accent_name_2=accent_name_2,
        segments_block=segments_block,
    )

    try:
        result = subprocess.run(
            [
                "claude", "-p", prompt,
                "--model", "sonnet",
                "--mcp-config", '{"mcpServers": {}}',
                "--strict-mcp-config",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        print("Промты для аватаров ЦА: модель не ответила вовремя — лендинг соберётся без них.")
        return []

    if result.returncode != 0:
        print("Промты для аватаров ЦА: модель завершилась с ошибкой — лендинг соберётся без них.")
        return []

    raw = result.stdout.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        prompts = json.loads(raw)
    except json.JSONDecodeError:
        print("Промты для аватаров ЦА: не удалось разобрать ответ модели — лендинг соберётся без них.")
        return []

    if not isinstance(prompts, list):
        return []
    return [str(p).strip() for p in prompts if str(p).strip()]


# ---------------------------------------------------------------------------
# Цвета: из одного HEX — три производных оттенка (по формуле из SKILL.md)
# ---------------------------------------------------------------------------

def _clamp(value):
    return max(0, min(255, int(round(value))))


def _shade(hex_color, factor):
    """factor < 0 — затемнить к чёрному, factor > 0 — осветлить к белому."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    if factor < 0:
        r, g, b = (c * (1 + factor) for c in (r, g, b))
    else:
        r, g, b = (c + (255 - c) * factor for c in (r, g, b))
    return "#{:02X}{:02X}{:02X}".format(_clamp(r), _clamp(g), _clamp(b))


def derive_accent_shades(hex_color):
    base = "#" + hex_color.lstrip("#").upper()
    darker = _shade(base, -0.15)   # ACCENT_2 — темнее ~15%
    lighter = _shade(base, 0.20)   # ACCENT_3 — светлее ~20%
    return base, darker, lighter


_COLOR_NAMES = [
    ("mint green", "#8FE3C0"),
    ("lime green", "#AADF32"),
    ("emerald green", "#2ECC71"),
    ("deep red", "#A6271F"),
    ("coral red", "#FF6F61"),
    ("crimson", "#DC143C"),
    ("soft blue", "#6CA0DC"),
    ("royal blue", "#4169E1"),
    ("teal", "#2C7873"),
    ("golden yellow", "#FFD700"),
    ("warm orange", "#FF8C42"),
    ("lavender purple", "#B57EDC"),
    ("deep purple", "#5D3FD3"),
    ("soft pink", "#F4A6C0"),
    ("warm beige", "#E8D5B7"),
    ("charcoal gray", "#4A4A4A"),
]


def nearest_color_name(hex_color):
    """Подбирает ближайшее по RGB-расстоянию описательное название цвета
    из небольшой курируемой палитры — нужно, чтобы промты для аватаров
    в Midjourney визуально совпадали с акцентными цветами лендинга."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    best_name, best_dist = "", float("inf")
    for name, sample_hex in _COLOR_NAMES:
        sr, sg, sb = (int(sample_hex.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
        dist = (r - sr) ** 2 + (g - sg) ** 2 + (b - sb) ** 2
        if dist < best_dist:
            best_dist = dist
            best_name = name
    return best_name


def accent_rgb_triplet(hex_color):
    """RGB-тройка для rgba(...) — нужна для свечений, теней и подсветок,
    которые в эталонном лендинге захардкожены как rgba(R,G,B,alpha)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    return f"{r},{g},{b}"


# ---------------------------------------------------------------------------
# Текстовая разметка: *слово* -> <span class="accent">, перенос строки -> <br>
# ---------------------------------------------------------------------------

_HANGING_WORD_RE = re.compile(r"(?<![а-яА-ЯёЁ\d])([а-яА-ЯёЁ]{1,3})[ \t]+(?=[а-яА-ЯёЁ])")


def glue_short_words(text):
    """Короткие предлоги, союзы и частицы (в, на, и, для, что...) не должны
    повисать в конце строки — меняем пробел после них на неразрывный (U+00A0),
    чтобы слово приклеилось к следующему. Стандартный приём типографики, без
    разбора частей речи — по длине слова (1-3 буквы), как делают типограф-скрипты."""
    if not text:
        return text
    return _HANGING_WORD_RE.sub(r"\1 ", text)


def format_accent_html(text):
    escaped = html.escape(glue_short_words(text or ""), quote=False)
    escaped = re.sub(r"\*(.+?)\*", r'<span class="accent">\1</span>', escaped)
    return escaped.replace("\n", "<br>\n          ")


def hero_title_font_size(title):
    """Заголовки из разных ТЗ сильно отличаются по длине — базовый clamp()
    рассчитан на короткие варианты и при длинном тексте даёт перенос на
    6-7 строк. Подбираем clamp() по длине текста (без учёта *акцентной* разметки),
    чтобы заголовок укладывался примерно в 5 строк при любой длине."""
    plain = re.sub(r"\*(.+?)\*", r"\1", title or "").replace("\n", " ")
    length = len(plain.strip())
    if length <= 45:
        return "clamp(32px, 4.2vw, 62px)"
    if length <= 75:
        return "clamp(28px, 3.6vw, 50px)"
    if length <= 110:
        return "clamp(24px, 3vw, 40px)"
    return "clamp(20px, 2.5vw, 32px)"


def esc(text):
    return html.escape(glue_short_words(text or ""), quote=False)


# ---------------------------------------------------------------------------
# Сборка HTML-фрагментов для повторяющихся блоков
# ---------------------------------------------------------------------------

def build_tags_html(tags):
    return "\n            ".join(f'<span class="sf-tag">{esc(t)}</span>' for t in tags or [])


def build_facts_html(facts):
    return "\n            ".join(f'<div class="sf-fact">{esc(f)}</div>' for f in facts or [])


def build_creds_html(facts):
    """Короткий список регалий под подписью спикера в hero (используется
    только для двух-спикерского варианта — там не помещается полный sf-facts)."""
    return "\n                ".join(f"<li>{esc(f)}</li>" for f in (facts or [])[:4])


_HERO_CHIP_HTML = (
    '<div class="live-chip r">\n'
    '          <span class="dot-live"></span>\n'
    '          <span id="event-date">{{EVENT_DATE}}</span>\n'
    '        </div>'
)

_HERO_TITLE_HTML = (
    '<h1 class="r" style="font-size: {{HERO_TITLE_FONT_SIZE}}">\n'
    '          {{TITLE_HTML}}\n'
    '        </h1>'
)

_HERO_SUB_HTML = (
    '<p class="hero-sub r">\n'
    '          {{SUBTITLE}}\n'
    '        </p>'
)

_HERO_CTA_HTML = (
    '<div class="hero-cta r">\n'
    '          <a href="#register" class="btn btn-lime">\n'
    '            Зарегистрироваться\n'
    '            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M5 12h14M12 5l7 7-7 7"/></svg>\n'
    '          </a>\n'
    '        </div>'
)


def hero_variant(speakers):
    """Компоновка главного экрана. Два спикера -> всегда оба фото внахлёст
    (в этом и смысл такого хедера). Один спикер -> рандомно: классический
    портрет или центрированный текст без фото — для разнообразия."""
    if len(speakers or []) >= 2:
        return "duo"
    return random.choice(("solo", "center"))


def hero_deco_variant():
    """Для центрированного хедера без фото — два равноценных стиля декора:
    'ИИ-шная' сетка с частицами или мягкие глоу-пятна по бокам."""
    return random.choice(("grid", "glow"))


def build_hero_center_deco_html():
    if hero_deco_variant() == "grid":
        return (
            '<div class="hero-persp-grid"></div>\n'
            '  <div class="hero-deco-dots"></div>\n'
            '  <div class="hp-ring hr-1"></div>\n'
            '  <div class="hp-ring hr-2"></div>\n'
            '  <div class="hero-particle hp-1"></div>\n'
            '  <div class="hero-particle hp-2"></div>\n'
            '  <div class="hero-particle hp-3"></div>\n'
            '  <div class="hero-particle hp-4"></div>\n'
            '  <div class="hero-particle hp-5"></div>'
        )
    return (
        '<div class="hero-persp-grid"></div>\n'
        '  <div class="hero-deco-glow-l"></div>\n'
        '  <div class="hero-deco-glow-r"></div>'
    )


def speaker_photo_src(speaker_photos, index):
    """Возвращает имя загруженного фото спикера по порядковому номеру
    (0 — первый спикер из ТЗ, 1 — второй), либо плейсхолдер, если фото нет."""
    if speaker_photos and index < len(speaker_photos) and speaker_photos[index]:
        return speaker_photos[index]
    return PLACEHOLDER_IMG


def build_hero_layout_html(speakers, speaker_photos=None):
    """Собирает содержимое главного экрана целиком — декор, заголовок и
    либо фото спикера(ов), либо центрированный текстовый блок без фото."""
    if hero_variant(speakers) == "center":
        return (
            build_hero_center_deco_html() + '\n\n'
            '  <div class="hero-center">\n'
            '    ' + _HERO_CHIP_HTML + '\n\n'
            '    ' + _HERO_TITLE_HTML + '\n\n'
            '    ' + _HERO_SUB_HTML + '\n\n'
            '    ' + _HERO_CTA_HTML + '\n'
            '  </div>'
        )

    return (
        '<div class="hero-persp-grid"></div>\n'
        '  <div class="hero-red-glow"></div>\n'
        '  <div class="hero-mint-glow"></div>\n\n'
        '  <div class="hero-layout">\n\n'
        '    <div class="hero-left">\n'
        '      ' + _HERO_CHIP_HTML + '\n\n'
        '      ' + _HERO_TITLE_HTML + '\n\n'
        '      ' + _HERO_SUB_HTML + '\n\n'
        '      ' + _HERO_CTA_HTML + '\n'
        '    </div>\n\n'
        '    <div class="hero-right">\n'
        '      ' + build_hero_speakers_html(speakers, speaker_photos) + '\n'
        '    </div>\n'
        '  </div>'
    )


def build_hero_speakers_html(speakers, speaker_photos=None):
    """Главный экран: один спикер -> крупный портрет, как и раньше; два
    спикера -> два фото внахлёст с подписями (имя/роль/регалии), по образцу
    реальных лендингов Zerocoder с двумя ведущими."""
    speakers = speakers or []
    if len(speakers) < 2:
        spk = speakers[0] if speakers else {}
        name = esc(spk.get("name", ""))
        return (
            f'<div class="hero-photo-frame r">\n'
            f'          <img src="{speaker_photo_src(speaker_photos, 0)}" alt="{name}">\n'
            f'        </div>'
        )

    cards = []
    for i, (spk, cls) in enumerate(zip(speakers[:2], ("spk-a", "spk-b"))):
        name = esc(spk.get("name", ""))
        role = esc(spk.get("role", ""))
        creds = build_creds_html(spk.get("facts"))
        creds_html = f'\n              <ul class="sc-creds">\n                {creds}\n              </ul>' if creds else ""
        cards.append(
            f'          <div class="spk-photo-wrap {cls}">\n'
            f'            <img src="{speaker_photo_src(speaker_photos, i)}" alt="{name}">\n'
            f'            <div class="spk-grad"></div>\n'
            f'            <div class="spk-caption">\n'
            f'              <div class="sc-name">{name}</div>\n'
            f'              <div class="sc-role">{role}</div>{creds_html}\n'
            f'            </div>\n'
            f'          </div>'
        )

    return (
        '<div class="hero-speakers-flex r">\n' + "\n".join(cards) + '\n        </div>'
    )


def build_speakers_heading(speakers):
    speakers = speakers or []
    if len(speakers) < 2:
        return esc(speakers[0].get("name", "")) if speakers else ""
    return "Спикеры"


def build_speakers_cards_html(speakers, speaker_photos=None):
    cards = []
    for i, spk in enumerate(speakers or []):
        name = esc(spk.get("name", ""))
        cards.append(
            f'      <div class="spk-full r">\n'
            f'        <div class="spk-photo-side">\n'
            f'          <img src="{speaker_photo_src(speaker_photos, i)}" alt="{name}">\n'
            f'        </div>\n'
            f'        <div class="spk-full-content">\n'
            f'          <div class="sf-role">{esc(spk.get("role", ""))}</div>\n'
            f'          <div class="sf-name">{name}</div>\n'
            f'          <div class="sf-tags">\n'
            f'            {build_tags_html(spk.get("tags"))}\n'
            f'          </div>\n'
            f'          <div class="sf-facts sf-facts-group">\n'
            f'            {build_facts_html(spk.get("facts"))}\n'
            f'          </div>\n'
            f'        </div>\n'
            f'      </div>'
        )
    return "\n".join(cards)


def _agenda_item_parts(item):
    """Пункт программы может прийти как объект {title, desc} или (по старой схеме
    / на случай отступления модели от формата) простой строкой."""
    if isinstance(item, dict):
        return (item.get("title") or "").strip(), (item.get("desc") or "").strip()
    return str(item or "").strip(), ""


def build_agenda_items_html(items):
    lis = []
    for item in items or []:
        title, desc = _agenda_item_parts(item)
        text = f"{title}. {desc}" if title and desc else (title or desc)
        lis.append(f"<li>{esc(text)}</li>")
    return "\n        ".join(lis)


def agenda_variant(items):
    """Подбирает вёрстку блока 'Что будет в эфире'. Ровно 3 пункта -> карточки
    в ряд (как в реальных лендингах Zerocoder, тут это смотрится лучше всего).
    Остальные случаи — и плашка с картинкой, и список на всю ширину одинаково
    хорошо работают с любым количеством пунктов, поэтому выбор между ними
    случайный — так лендинги меньше похожи друг на друга."""
    if len(items) == 3:
        return 2
    return random.choice((1, 3))


def agenda_illus_height(count):
    """Чем меньше пунктов программы, тем компактнее картинка рядом с плашкой."""
    return min(420, max(220, 160 + count * 50))


def build_agenda_body_html(agenda):
    items = agenda.get("items") or []
    intro = (agenda.get("intro") or "").strip()
    intro_html = (
        f'<p class="r" style="font-size:16px;color:var(--sub);margin-top:20px;'
        f'margin-bottom:8px;max-width:700px;line-height:1.7;">{esc(intro)}</p>'
        if intro else ""
    )

    variant = agenda_variant(items)

    if variant == 1:
        height = agenda_illus_height(len(items))
        return (
            f'    <div class="agenda-split r">\n'
            f'      <div class="ocard ocard-2" style="margin-top:0;">\n'
            f'        <div class="ocard-pre">{esc(intro)}</div>\n'
            f'        <ul class="ocard-list">\n          {build_agenda_items_html(items)}\n        </ul>\n'
            f'        <div class="ocard-answer-badge">\n'
            f'          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" style="flex-shrink:0;opacity:0.85;"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4M12 8h.01"/></svg>\n'
            f'          Ответим на все твои вопросы!\n'
            f'        </div>\n'
            f'      </div>\n'
            f'      <div class="agenda-illus" style="height:{height}px">\n'
            f'        <img src="{PLACEHOLDER_IMG}" alt="Программа эфира">\n'
            f'      </div>\n'
            f'    </div>'
        )

    if variant == 2:
        cards = []
        for i, item in enumerate(items, start=1):
            title, desc = _agenda_item_parts(item)
            desc_html = f'\n        <p>{esc(desc)}</p>' if desc else ""
            cards.append(
                f'      <div class="agenda-card r">\n'
                f'        <div class="agenda-card-n">{i:02d}</div>\n'
                f'        <h3>{esc(title)}</h3>{desc_html}\n'
                f'      </div>'
            )
        return f'{intro_html}\n    <div class="agenda-cards">\n' + "\n".join(cards) + '\n    </div>'

    rows = []
    for i, item in enumerate(items, start=1):
        title, desc = _agenda_item_parts(item)
        desc_html = f'<small>{esc(desc)}</small>' if desc else ""
        rows.append(
            f'      <div class="agenda-row r">\n'
            f'        <span class="agenda-row-num">{i:02d}</span>\n'
            f'        <span class="agenda-row-text">{esc(title)}{desc_html}</span>\n'
            f'      </div>'
        )
    return f'{intro_html}\n    <div class="agenda-rows">\n' + "\n".join(rows) + '\n    </div>'


def build_bonuses_html(bonuses):
    return "\n          ".join(f'<li class="plus">{esc(b)}</li>' for b in bonuses or [])


def audience_grid_cols(count, max_cols=4):
    """Сколько карточек ставить в ряд, чтобы строки были сбалансированы
    (например, для 5 карточек — 3 и 2, а не 4 и одна-сирота)."""
    if count <= 0:
        return max_cols
    if count <= max_cols:
        return count
    rows = -(-count // max_cols)  # ceil
    return -(-count // rows)      # ceil


def build_audience_cards_html(audience):
    cards = []
    for i, seg in enumerate(audience or []):
        cls = "acard acard-red" if i % 2 == 1 else "acard"
        result = (seg.get("result") or "").strip()
        result_html = f'\n        <div class="a-result">{esc(result)}</div>' if result else ""
        cards.append(
            f'      <div class="{cls} r">\n'
            f'        <div class="a-photo-banner"><img src="{PLACEHOLDER_IMG}" alt="{esc(seg.get("who", ""))}"></div>\n'
            f'        <div class="a-who">{esc(seg.get("who", ""))}</div>\n'
            f'        <div class="a-desc">{esc(seg.get("desc", ""))}</div>{result_html}\n'
            f'      </div>'
        )
    return "\n".join(cards)


def build_gift_badges_html(gifts):
    """Карточки подарков внутри .gift-v2-badges. Раскладка (1 в полную ширину,
    2 рядом, 3 — два сверху и один на всю ширину) задаётся чистым CSS через
    :last-child:nth-child(odd) — здесь просто рендерим нужное число карточек."""
    badges = []
    for g in (gifts or [])[:3]:
        name = esc(g.get("name", ""))
        desc = (g.get("desc") or "").strip()
        desc_html = f'\n              <div class="gift-v2-badge-desc">{esc(desc)}</div>' if desc else ""
        badges.append(
            '            <div class="gift-v2-badge">\n'
            f'              <div class="gift-v2-badge-name">{name}</div>{desc_html}\n'
            '            </div>'
        )
    return "\n".join(badges)


def build_glossary_items_html(glossary):
    items = []
    for term in glossary or []:
        items.append(
            f'      <div class="gitem r">\n'
            f'        <div class="g-header">\n'
            f'          <div class="g-term">{esc(term.get("term", ""))}</div>\n'
            f'          <div class="g-arrow">▼</div>\n'
            f'        </div>\n'
            f'        <div class="g-body">\n'
            f'          <div class="g-def">{esc(term.get("def", ""))}</div>\n'
            f'        </div>\n'
            f'      </div>'
        )
    return "\n".join(items)


# ---------------------------------------------------------------------------
# Опциональные блоки шаблона — вырезаются, если данных нет
# ---------------------------------------------------------------------------

def keep_block(html_text, name):
    return re.sub(rf"<!--BLOCK:{name}-->(.*?)<!--/BLOCK:{name}-->", r"\1", html_text, flags=re.S)


def drop_block(html_text, name):
    return re.sub(rf"<!--BLOCK:{name}-->.*?<!--/BLOCK:{name}-->", "", html_text, flags=re.S)


# ---------------------------------------------------------------------------
# Шаг 2. Детерминированная сборка готового HTML
# ---------------------------------------------------------------------------

def assemble_html(data, accent_hex, accent2_hex="", custom_style_html="", speaker_photos=None):
    tpl = TEMPLATE_PATH.read_text(encoding="utf-8")

    accent_1, accent_2, accent_3 = derive_accent_shades(accent_hex)
    accent_rgb = accent_rgb_triplet(accent_1)

    # второй акцент — для контрастных элементов (альтернативные карточки,
    # бейджи, финальный блок). Если не задан, используем первый — тогда
    # вся палитра остаётся монохромной, как и раньше.
    if accent2_hex:
        accent2_1, accent2_2, accent2_3 = derive_accent_shades(accent2_hex)
    else:
        accent2_1, accent2_2, accent2_3 = accent_1, accent_2, accent_3
    accent2_rgb = accent_rgb_triplet(accent2_1)

    speakers = data.get("speakers") or []
    gifts = data.get("gifts") or []
    agenda = data.get("agenda")
    tariff = data.get("tariff_paid")
    audience = data.get("audience")
    glossary = data.get("glossary")

    title_html = format_accent_html(data.get("title", ""))

    replacements = {
        "{{HERO_LAYOUT_HTML}}": build_hero_layout_html(speakers, speaker_photos),
        "{{TITLE}}": esc(data.get("title", "")),
        "{{TITLE_HTML}}": title_html,
        "{{HERO_TITLE_FONT_SIZE}}": hero_title_font_size(data.get("title", "")),
        "{{SUBTITLE}}": esc(data.get("subtitle", "")),
        "{{EVENT_DATE}}": esc(data.get("event_date", "")),
        "{{SPEAKER_NAME}}": esc(speakers[0].get("name", "")) if speakers else "",
        "{{SPEAKER_PHOTO}}": speaker_photo_src(speaker_photos, 0),
        "{{SPEAKERS_HEADING}}": build_speakers_heading(speakers),
        "{{SPEAKERS_CARDS_HTML}}": build_speakers_cards_html(speakers, speaker_photos),
        "{{ACCENT_1}}": accent_1,
        "{{ACCENT_2}}": accent_2,
        "{{ACCENT_3}}": accent_3,
        "{{ACCENT_RGB}}": accent_rgb,
        "{{ACCENT2_1}}": accent2_1,
        "{{ACCENT2_2}}": accent2_2,
        "{{ACCENT2_3}}": accent2_3,
        "{{ACCENT2_RGB}}": accent2_rgb,
        "{{ASSET_PRESENT}}": PLACEHOLDER_IMG,
        "{{CUSTOM_STYLE_HTML}}": custom_style_html,
        "{{GIFT_BADGES_HTML}}": build_gift_badges_html(gifts),
    }

    if gifts:
        for name in ("GIFT", "GIFT2", "GIFT3"):
            tpl = keep_block(tpl, name)
    else:
        for name in ("GIFT", "GIFT2", "GIFT3"):
            tpl = drop_block(tpl, name)

    if agenda and agenda.get("items"):
        replacements["{{AGENDA_BODY_HTML}}"] = build_agenda_body_html(agenda)
        tpl = keep_block(tpl, "AGENDA")
    else:
        tpl = drop_block(tpl, "AGENDA")

    if tariff:
        replacements["{{TARIFF_PAID_PRICE}}"] = esc(tariff.get("price", ""))
        replacements["{{TARIFF_PAID_OLD_PRICE}}"] = esc(tariff.get("old_price", ""))
        replacements["{{TARIFF_PAID_BONUSES_HTML}}"] = build_bonuses_html(tariff.get("bonuses"))
        tpl = keep_block(tpl, "PRICING")
        tpl = drop_block(tpl, "NO_PRICING")
    else:
        tpl = drop_block(tpl, "PRICING")
        tpl = keep_block(tpl, "NO_PRICING")

    if audience:
        replacements["{{AUDIENCE_CARDS_HTML}}"] = build_audience_cards_html(audience)
        replacements["{{AUD_COLS}}"] = str(audience_grid_cols(len(audience)))
        tpl = keep_block(tpl, "AUDIENCE")
    else:
        tpl = drop_block(tpl, "AUDIENCE")

    if glossary:
        replacements["{{GLOSSARY_ITEMS_HTML}}"] = build_glossary_items_html(glossary)
        tpl = keep_block(tpl, "GLOSSARY")
    else:
        tpl = drop_block(tpl, "GLOSSARY")

    for placeholder, value in replacements.items():
        tpl = tpl.replace(placeholder, value)

    return tpl


def build_followup_checklist(data, speakers, gifts, audience, speaker_photos, avatar_prompts):
    """Список того, что разумно доделать руками после генерации — собирается
    по факту того, что реально есть в лендинге, а не одинаков для всех."""
    items = []

    if gifts:
        items.append({"text": "Добавь иконку или фото подарка вместо плейсхолдера", "prompts": []})

    if audience:
        items.append({
            "text": "Сделай аватары для целевой аудитории в Midjourney — вот готовые промты под каждый сегмент:",
            "prompts": avatar_prompts,
        })

    missing = [i + 1 for i, spk in enumerate(speakers) if i >= len(speaker_photos) or not speaker_photos[i]]
    if missing:
        who = "спикера" if len(missing) == 1 else "спикеров"
        nums = ", ".join(str(n) for n in missing)
        items.append({"text": f"Загрузи фото {who} ({nums}) — сейчас вместо них плейсхолдер", "prompts": []})

    items.append({"text": "Задеплой сайт", "prompts": []})
    return items


def slugify_fallback(data):
    slug = (data.get("slug") or "").strip().lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug).strip("-")
    return slug or "landing"


def save_speaker_photo(file_storage, output_dir, index):
    """Сохраняет загруженное фото спикера прямо в папку лендинга — так оно
    никогда не потеряется при скачивании (в отличие от shared-assets,
    путь к которым завязан на структуру папок). Возвращает имя файла для
    src или None, если фото не загружено / формат не подходит."""
    if not file_storage or not file_storage.filename:
        return None
    ext = Path(file_storage.filename).suffix.lower()
    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        return None
    filename = f"speaker-{index}{ext}"
    file_storage.save(output_dir / filename)
    return filename


# ---------------------------------------------------------------------------
# Маршруты
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/generate", methods=["POST"])
def generate():
    tz_text = request.form.get("tz", "").strip()
    accent_hex = request.form.get("accent", "#AADF32").strip()
    accent2_hex = request.form.get("accent2", "").strip()
    wishes_text = request.form.get("wishes", "").strip()

    if not tz_text:
        return render_template("index.html", error="Вставь текст ТЗ — без него генерировать нечего.")

    if not re.fullmatch(r"#?[0-9A-Fa-f]{6}", accent_hex):
        return render_template("index.html", error="Акцентный цвет должен быть в формате HEX, например #AADF32.", tz=tz_text, wishes=wishes_text)

    if accent2_hex and not re.fullmatch(r"#?[0-9A-Fa-f]{6}", accent2_hex):
        return render_template("index.html", error="Второй акцентный цвет должен быть в формате HEX, например #DF3D3D.", tz=tz_text, accent=accent_hex, wishes=wishes_text)

    data, err = run_extraction(tz_text)
    if err:
        return render_template("index.html", error=err, tz=tz_text, wishes=wishes_text)

    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify_fallback(data)
    output_dir = GENERATED_DIR / f"landing-{timestamp}-{slug}"
    output_path = output_dir / "index.html"
    output_dir.mkdir(parents=True, exist_ok=True)

    speaker_photos = [
        save_speaker_photo(request.files.get("speaker_photo_1"), output_dir, 1),
        save_speaker_photo(request.files.get("speaker_photo_2"), output_dir, 2),
    ]

    custom_style_html = run_style_generation(wishes_text)
    html_output = assemble_html(data, accent_hex, accent2_hex, custom_style_html, speaker_photos)
    output_path.write_text(html_output, encoding="utf-8")

    speakers = data.get("speakers") or []
    gifts = data.get("gifts") or []
    audience = data.get("audience") or []
    avatar_prompts = run_avatar_prompts(audience, accent_hex, accent2_hex)
    checklist = build_followup_checklist(data, speakers, gifts, audience, speaker_photos, avatar_prompts)

    relative_path = str(output_path.relative_to(GENERATED_DIR))
    return render_template("result.html", relative_path=relative_path, checklist=checklist)


@app.route("/preview/<path:subpath>")
def preview(subpath):
    return send_from_directory(GENERATED_DIR, subpath)


@app.route("/shared-assets/<path:subpath>")
def shared_assets(subpath):
    # сгенерированный HTML лежит в generated/landing-xxx/index.html и ссылается
    # на общие картинки через "../../shared-assets/..." — при раздаче через
    # /preview/landing-xxx/ браузер резолвит это в /shared-assets/...
    return send_from_directory(BASE_DIR / "shared-assets", subpath)


if __name__ == "__main__":
    GENERATED_DIR.mkdir(exist_ok=True)
    app.run(debug=True, port=5050)
