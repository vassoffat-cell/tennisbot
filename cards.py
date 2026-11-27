"""🖼 Картинки отчётов (решение пользователя 27.11 13:19 «давай»: все сообщения, кроме служебных, — картинкой, цвета; фон светлый).

Вход — те же HTML-блоки, что уходили текстом (report.build, caprep.report, dp_report, блоки 🧠): содержание и порядок не меняются
(форматы закреплены — REPORT_STYLE §9), меняется только вид. Свёрнутые списки (<blockquote expandable>: матчи, прогнозы каналов) в картинку
не идут — они остаются текстом под spoiler отдельным сообщением.

render(blocks) → (pngs, text_blocks, title): pngs — список PNG (высокий отчёт режется на части ≤ MAX_H, Telegram сжимает фото по длинной стороне).
Сбой отрисовки — исключение; notify.send_blocks тогда шлёт обычный текст (ничего не теряется).
"""
from __future__ import annotations

import html
import io
import re
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
_F = ROOT / 'assets/fonts'
FONT = {'r': _F / 'Montserrat-Regular.ttf', 'm': _F / 'Montserrat-Medium.ttf', 'b': _F / 'Montserrat-SemiBold.ttf',
        'B': _F / 'Montserrat-Bold.ttf', 'i': _F / 'Montserrat-Medium.ttf',          # 27.11 13:34: hi-tech минимализм, Montserrat
        'fr': _F / 'DejaVuSans.ttf', 'fb': _F / 'DejaVuSans-Bold.ttf'}              # запасной — знаки, которых нет в Montserrat
EMOJI = ROOT / 'assets/emoji'

W, PAD, MAX_H = 1080, 48, 2400                       # Telegram показывает фото до 2560 по длинной стороне
# светлая тема (27.11 13:19) · hi-tech минимализм (27.11 13:34): белые карточки, тонкие линии, один акцент, цвет — только у чисел
BG, CARD, LINE = (246, 247, 249), (255, 255, 255), (230, 233, 238)
TXT, MUT = (16, 19, 26), (122, 130, 143)
GRN, RED, AMB, BLU = (0, 163, 115), (229, 72, 77), (226, 152, 20), (52, 86, 246)
ACC = BLU
# значки-украшения в картинке не рисуются (минимализм); смысловые (🔥 ⚔️ ⛔ 🎯 💰) — остаются
DECOR = set('⚽🎾🏒📈👥💼🏆🕒🔎🔏💵🧠✏➕📅👤📊🗂🚀🛠💡📘🔁🔄⏱⏰📥📢🧮🧪🧬⭐▸▶')
FLAG = {'🟢': GRN, '🟡': AMB, '🔴': RED}
MARK = {'✅': GRN, '❌': RED, '⏳': MUT, '⚪': MUT, '↩️': BLU, '↩': BLU}

_EMO = re.compile(r'(\U0001F1E6-\U0001F1FF{2}|[\U0001F000-\U0001FAFF☀-➿⬀-⯿⌚-⏿]️?)')


@lru_cache(maxsize=None)
def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT.get(kind, FONT['r'])), size)


@lru_cache(maxsize=None)
def emoji(ch: str, size: int) -> Image.Image | None:
    name = '-'.join(f'{ord(c):x}' for c in ch if c != '️')
    p = EMOJI / f'{name}.png'
    if not p.exists():
        return None
    return Image.open(p).convert('RGBA').resize((size, size), Image.LANCZOS)


@lru_cache(maxsize=None)
def _notdef(kind: str) -> tuple:
    m = font(kind, 24).getmask('\U0010FFFD')
    return m.size, bytes(m)


@lru_cache(maxsize=None)
def _glyph(ch: str, kind: str = 'r') -> bool:
    """Есть ли знак в шрифте: отсутствующий рисуется «пустым квадратом» (.notdef) — сравниваем с ним."""
    m = font(kind, 24).getmask(ch)
    return (m.size, bytes(m)) != _notdef(kind)


def _segs(seg: str, st: str) -> list[tuple[str, str]]:
    """Разбить текст по шрифтам: Montserrat, нет знака — DejaVu, нет и там — пропустить."""
    out = []
    for c in seg:
        k = st if c.isspace() or _glyph(c, st) else ('fb' if st in ('b', 'B') else 'fr') if _glyph(c, 'fr') else None
        if k is None:
            continue
        if out and out[-1][1] == k:
            out[-1] = (out[-1][0] + c, k)
        else:
            out.append((c, k))
    return out


def _has_glyph(ch: str) -> bool:
    try:
        return font('r', 20).getmask(ch).getbbox() is not None
    except Exception:
        return False


# ───────────────────────────── разбор HTML Telegram ─────────────────────────────
def runs(line: str) -> list[tuple[str, str]]:
    """Строка HTML → [(текст, стиль r/b/i)]; прочие теги снимаются, сущности раскрываются."""
    out, style = [], ['r']
    for part in re.split(r'(</?[a-zA-Z][^>]*>)', line):
        if not part:
            continue
        m = re.fullmatch(r'<(/?)([a-zA-Z\-]+)[^>]*>', part)
        if m:
            tag, close = m.group(2).lower(), m.group(1) == '/'
            if tag in ('b', 'strong'):
                style.append('b') if not close else (style.pop() if len(style) > 1 else None)
            elif tag in ('i', 'em'):
                style.append('i') if not close else (style.pop() if len(style) > 1 else None)
            continue
        out.append((html.unescape(part), style[-1]))
    return out


def plain(line: str) -> str:
    return ''.join(t for t, _ in runs(line))


def _num(t: str) -> str:
    return re.sub(r'⭐\ufe0f?(\d)', r'№\1', t)


def split(blocks: list[str]) -> tuple[list[str], list[str]]:
    """Блоки → (строки для картинки, текстовые блоки со свёрнутыми списками). Строка перед <blockquote expandable> — заголовок списка,
    уходит в текст вместе с ним."""
    body = '\n\n'.join(b for b in blocks if b)
    body = _num(re.sub(r'</?tg-spoiler>', '', body))
    texts, keep = [], []
    pos = 0
    for m in re.finditer(r'<blockquote expandable>.*?</blockquote>', body, re.S):
        k = m.start()
        while k > pos and body[k - 1] in ' \n':
            k -= 1
        head_start = body.rfind('\n', pos, k) + 1
        head = body[head_start:m.start()].strip()
        keep.append(body[pos:head_start])
        texts.append((head + '\n' if head else '') + m.group())
        pos = m.end()
    keep.append(body[pos:])
    lines = '\n'.join(keep).split('\n')
    return lines, texts


# ───────────────────────────── холст ─────────────────────────────
class Canvas:
    def __init__(self):
        self.img = Image.new('RGB', (W, 6000), BG)
        self.d = ImageDraw.Draw(self.img)
        self.y = PAD
        self.cuts = [0]                                  # где можно резать на части (между элементами)

    def text(self, x: int, y: int, parts: list[tuple[str, str]], size: int, color=TXT, maxw: int | None = None,
             colors: dict | None = None) -> int:
        """Нарисовать runs с эмодзи; возвращает x конца. maxw — обрезать с «…»."""
        for t, st in parts:
            for seg in _EMO.split(t):
                if not seg:
                    continue
                if _EMO.fullmatch(seg):
                    if seg.replace('\ufe0f', '') in DECOR:
                        continue
                    im = emoji(seg, int(size * 1.05))
                    if im is not None:
                        if maxw and x + im.width > maxw:
                            return x
                        self.img.paste(im, (int(x), int(y + size * 0.08)), im)
                        x += im.width + int(size * 0.18)
                        continue
                    if not _has_glyph(seg[0]):
                        continue
                col = (colors or {}).get(st, color)
                for sg, kk in _segs(seg, st):
                    f = font(kk, size)
                    if maxw and x + self.d.textlength(sg, font=f) > maxw:
                        while sg and x + self.d.textlength(sg + '…', font=f) > maxw:
                            sg = sg[:-1]
                        self.d.text((x, y), sg + '…', font=f, fill=col)
                        return maxw
                    self.d.text((x, y), sg, font=f, fill=col)
                    x += self.d.textlength(sg, font=f)
        return x

    def width(self, parts, size) -> float:
        w = 0.0
        for t, st in parts:
            for seg in _EMO.split(t):
                if not seg:
                    continue
                if _EMO.fullmatch(seg):
                    if seg.replace('\ufe0f', '') not in DECOR and emoji(seg, int(size * 1.05)) is not None:
                        w += int(size * 1.05) + int(size * 0.18)
                else:
                    w += sum(self.d.textlength(sg, font=font(kk, size)) for sg, kk in _segs(seg, st))
        return w

    def wrap(self, parts, size, maxw) -> list[list[tuple[str, str]]]:
        """Перенос по словам (runs сохраняют стиль)."""
        words = []
        for t, st in parts:
            for w in re.split(r'(\s+)', t):
                if w:
                    words.append((w, st))
        lines, cur = [], []
        for w in words:
            if cur and not w[0].isspace() and self.width(cur + [w], size) > maxw:
                lines.append(cur); cur = []
                if w[0].isspace():
                    continue
            if not cur and w[0].isspace():
                continue
            cur.append(w)
        if cur:
            lines.append(cur)
        return lines or [[('', 'r')]]

    def mark(self):
        self.cuts.append(self.y)

    def done(self) -> list[bytes]:
        """Обрезать и разрезать на части ≤ MAX_H по отметкам."""
        end = self.y + PAD
        out, top = [], 0
        cuts = [c for c in self.cuts if 0 < c < end] + [end]
        last = 0
        for c in cuts:
            if c - top > MAX_H and last > top:
                out.append((top, last)); top = last
            last = c
        if out and end - top < 360:                            # короткий хвост — к предыдущей части
            top = out.pop()[0]
        out.append((top, end))
        res = []
        for a, b in out:
            part = self.img.crop((0, max(0, a - (PAD // 2 if a else 0)), W, b))
            buf = io.BytesIO(); part.save(buf, 'PNG', optimize=True)
            res.append(buf.getvalue())
        return res


# ───────────────────────────── элементы ─────────────────────────────
_PICK = re.compile(r'^\s*(✅|❌|⏳|⚪|↩️)\s*(\S*?\d+)\s*(<b>.*?</b>)\s*·\s*(.+)$')


def caps(cv: Canvas, x: float, y: float, text: str, size: int = 22, color=ACC, track: float = 2.5) -> float:
    """Надстрочник: ПРОПИСНЫЕ с разрядкой (hi-tech)."""
    f = font('b', size)
    for ch in text.upper():
        cv.d.text((x, y), ch, font=f, fill=color)
        x += cv.d.textlength(ch, font=f) + track
    return x


def _clean(t: str) -> str:
    return ' '.join(w for w in _EMO.sub(lambda m: '' if m.group().replace('\ufe0f', '') in DECOR else m.group(), t).split())


def _title(cv: Canvas, line: str):
    """«📈 Футбол · отчёт · 26.11.2026 · 🟡 цели…» → надстрочник «ФУТБОЛ · ОТЧЁТ», крупно дата / период, ниже — остаток."""
    m = re.match(r'\s*(.*?)<b>(.*?)</b>(.*)$', line)
    pre, head, rest = (m.group(1), m.group(2), m.group(3)) if m else ('', plain(line), '')
    parts = [p.strip() for p in _clean(html.unescape(plain(pre) + head)).split('·') if p.strip()]
    big = next((p for p in parts if re.search(r'\d', p)), parts[-1] if parts else '')
    over = ' · '.join(p for p in parts if p != big)
    caps(cv, PAD, cv.y, over or 'отчёт')
    cv.y += 40
    cv.text(PAD, cv.y, [(big, 'B')], 50, maxw=W - PAD)
    cv.y += 70
    rest = plain(rest).strip(' ·—-')
    if rest:
        for ln in cv.wrap([(rest, 'm')], 28, W - 2 * PAD):
            cv.text(PAD, cv.y, ln, 28, MUT)
            cv.y += 40
    cv.d.line([PAD, cv.y + 8, PAD + 64, cv.y + 8], fill=ACC, width=4)      # акцентная черта под заголовком
    cv.y += 34
    cv.mark()


def _parse_row(r: str) -> dict | None:
    """Строка <pre>-таблицы → {ind, label, vals, flag, head}; пустая / линия — None."""
    s = html.unescape(re.sub(r'<[^>]+>', '', r)).rstrip()
    if not s.strip() or set(s.strip()) <= set('─-—='):
        return None
    ind = len(s) - len(s.lstrip(' '))
    flags = [f for f in FLAG if f in s]
    s2 = s
    for f in list(FLAG) + ['▶']:
        s2 = s2.replace(f, '')
    cells = re.split(r'\s{2,}', s2.strip())
    label, vals = cells[0], cells[1:]
    if len(vals) == 1 and ' ' in vals[0]:
        vals = vals[0].split()
    m = re.match(r'^(.*\S)\s+(\d+/\d+)$', label)              # 27.11: длинная метка слиплась с «240/343» (один пробел) → в колонку
    if m:
        label, vals = m.group(1), [m.group(2)] + vals
    label = re.sub(r'\bДвойной шан\b', 'Двойной шанс', label)   # в тексте обрезано под 35 знаков, на картинке место есть
    if any(re.search(r'\d', v) for v in vals):               # «⛔» и т. п. — к метке, не в колонку (шапку не трогать)
        extra = [v for v in vals if not re.search(r'\d', v)]
        vals = [v for v in vals if re.search(r'\d', v)]
        if extra:
            label = f"{label} {' '.join(extra)}"
    label = _clean(label)
    return (dict(ind=ind, label=label, vals=vals, flag=flags[0] if flags else '', head=(ind == 0)))


def _table(cv: Canvas, rows: list[str]):
    """<pre>-таблица: «Метка   n   %  🟢» → карточка с колонками; строки без отступа — заголовки групп."""
    parsed = [p for p in (_parse_row(r) for r in rows) if p]
    if not parsed:
        return
    hdr = parsed[0] if parsed[0]['head'] and not any(re.search(r'\d', v) for v in parsed[0]['vals']) and parsed[0]['vals'] else None
    body = parsed[1:] if hdr else parsed
    size = 30
    ncol = max((len(p['vals']) for p in parsed), default=0)
    colw = [0.0] * ncol                                       # ширины колонок справа налево
    for p in parsed:
        for k, v in enumerate(reversed(p['vals'])):
            colw[k] = max(colw[k], cv.d.textlength(v, font=font('b', size)))
    gap, right = 34, W - PAD - 40
    xs, x = [], right                                          # правые края колонок
    for k in range(ncol):
        xs.append(x); x -= colw[k] + gap
    label_max = x
    top = cv.y
    h = (len(body) + (1 if hdr else 0)) * 50 + 30
    cv.d.rounded_rectangle([PAD - 8, top, W - PAD + 8, top + h], radius=16, fill=CARD, outline=LINE, width=2)
    cv.y += 18
    if hdr:
        caps(cv, PAD + 16, cv.y + 4, hdr['label'], 19, MUT, 1.5)
        for k, v in enumerate(reversed(hdr['vals'])):
            f = font('b', 19); w = sum(cv.d.textlength(c, font=f) + 1.5 for c in v.upper())
            caps(cv, xs[k] - w, cv.y + 4, v, 19, MUT, 1.5)
        cv.y += 50
    for i, p in enumerate(body):
        if p['head'] and i:
            cv.d.line([PAD + 10, cv.y - 6, W - PAD - 10, cv.y - 6], fill=LINE, width=2)
        x0 = PAD + 16 + (28 if p['ind'] == 1 else 52 if p['ind'] >= 2 else 0)
        st = 'b' if p['head'] else 'm'
        cv.text(x0, cv.y, [(p['label'], st)], size, TXT if p['head'] or p['ind'] <= 1 else MUT, maxw=int(label_max))
        col = FLAG.get(p['flag'], TXT)
        for k, v in enumerate(reversed(p['vals'])):
            last = k == 0
            c = col if last and p['flag'] else (GRN if v.startswith('+') and re.search(r'\d', v) and k == 0 else
                                                RED if re.match(r'[−-]\d', v) and k == 0 else TXT)
            f = font('b' if last else 'r', size)
            cv.d.text((xs[k] - cv.d.textlength(v, font=f), cv.y), v, font=f, fill=c)
        if p['flag']:
            cv.d.ellipse([right + 14, cv.y + 12, right + 26, cv.y + 24], fill=col)
        cv.y += 50
        if (i + 1) % 8 == 0:
            cv.mark()
    cv.y = top + h + 24
    cv.mark()


def _pick(cv: Canvas, m: re.Match, subs: list[str]):
    """Строка ставки дня «✅ ⚽1 <b>ТБ2.5</b> · матч · 64% · кф 1,61 · EV +2,9%» → карточка с цветной полосой."""
    mk, num, line, rest = m.group(1), m.group(2), plain(m.group(3)), m.group(4)
    parts = [p.strip() for p in plain(rest).split('·')]
    match, info = parts[0], ' · '.join(parts[1:])
    col = MARK.get(mk, MUT)
    h = 150 + 40 * len(subs)
    top = cv.y
    cv.d.rounded_rectangle([PAD - 8, top, W - PAD + 8, top + h], radius=16, fill=CARD, outline=LINE, width=2)
    cv.d.rectangle([PAD - 8, top + 16, PAD - 3, top + h - 16], fill=col)
    x = cv.text(PAD + 28, top + 18, [(num, 'b')], 34, MUT)
    cv.text(x + 14, top + 16, [(line, 'b')], 38)
    res = {'✅': 'угадано', '❌': 'мимо', '⏳': 'ждём итог', '⚪': 'возврат', '↩️': 'возврат'}.get(mk, '')
    if res:
        f = font('b', 28); w = cv.d.textlength(res, font=f) + 32
        cv.d.rounded_rectangle([W - PAD - 16 - w, top + 20, W - PAD - 16, top + 62], radius=10, fill=col)
        cv.d.text((W - PAD - w, top + 26), res, font=f, fill=(255, 255, 255))
    cv.text(PAD + 28, top + 70, [(match, 'r')], 30, maxw=W - PAD - 20)
    cv.text(PAD + 28, top + 110, [(info, 'r')], 26, MUT, maxw=W - PAD - 20)
    y = top + 150
    for s in subs:
        cv.text(PAD + 28, y, [(_clean(plain(s)), 'i')], 24, MUT, maxw=W - PAD - 20); y += 40
    cv.y = top + h + 14
    cv.mark()


_CAP = re.compile(r'^(.+?) — (\d+/\d+|итогов нет) · прогнозов (\d+)(?: · ROI ([+\-−]?\d+%))?(?: · ▫ (\d+))?\s*$')   # caprep.report: ▫ — без итога


def _captable(cv: Canvas, rows: list[tuple]):
    """Сводка каперов «Канал — 6/11 · прогнозов 19 · ROI +13%» → таблица: канал | угадано | прогнозов | ROI (цвет по знаку)."""
    top, size = cv.y, 30
    h = 50 * (len(rows) + 1) + 30
    cv.d.rounded_rectangle([PAD - 8, top, W - PAD + 8, top + h], radius=16, fill=CARD, outline=LINE, width=2)
    xs = (W - PAD - 330, W - PAD - 170, W - PAD - 24)       # правые края колонок
    y = top + 18
    for k, hd in enumerate(('угадано', 'всего', 'ROI')):
        f = font('b', 19); w = sum(cv.d.textlength(c, font=f) + 1.5 for c in hd.upper())
        caps(cv, xs[k] - w, y + 4, hd, 19, MUT, 1.5)
    caps(cv, PAD + 16, y + 4, 'канал', 19, MUT, 1.5)
    y += 50
    for name, wn, n, roi, nn in rows:
        x = cv.text(PAD + 16, y, [(name, 'b')], size, maxw=xs[0] - 150)
        if nn:                                                 # прогнозы без итога (▫) — мелко рядом с каналом
            cv.text(x + 12, y + 5, [(f'без итога {nn}', 'r')], 22, MUT, maxw=xs[0] - 140)
        wn = '—' if wn == 'итогов нет' else wn
        for k, v in enumerate((wn, n, roi or '—')):
            col = TXT if k < 2 else (GRN if v.startswith('+') else RED if v[:1] in '-−' else MUT)
            f = font('b' if k == 2 else 'r', size)
            cv.d.text((xs[k] - cv.d.textlength(v, font=f), y), v, font=f, fill=col)
        y += 50
    cv.y = top + h + 20
    cv.mark()


def _line(cv: Canvas, line: str):
    s = line.strip()
    mk = next((k for k in MARK if s.startswith(k)), '')
    size = 30
    if mk:                                                     # ✅ / ❌ … — цветная точка вместо значка
        col = MARK[mk]
        cv.d.ellipse([PAD + 2, cv.y + 10, PAD + 22, cv.y + 30], fill=col)
        parts, x0 = runs(s[len(mk):].strip()), PAD + 36
    else:
        parts, x0 = runs(s), PAD
    parts = [(_clean(t) + (' ' if t.endswith(' ') else ''), st) if _EMO.search(t) else (t, st) for t, st in parts]
    italic = all(st == 'i' for t, st in parts if t.strip())
    for ln in cv.wrap(parts, size - (4 if italic else 0), W - PAD - x0):
        cv.text(x0, cv.y, ln, size - (4 if italic else 0), MUT if italic else TXT)
        cv.y += 44 if not italic else 38
    cv.mark()


def render(blocks: list[str]) -> tuple[list[bytes], list[str], str]:
    lines, texts = split(blocks)
    cv = Canvas()
    title = ''
    in_pre, pre = False, []
    i = 0
    while i < len(lines):
        raw = lines[i]
        if not in_pre and '<pre>' in raw:
            before, after = raw.split('<pre>', 1)
            if before.strip():
                lines[i:i + 1] = [before, '<pre>' + after]; continue
            in_pre, pre = True, []
            raw = after
        if in_pre:
            if '</pre>' in raw:
                pre.append(raw.split('</pre>', 1)[0]); in_pre = False
                _table(cv, pre)
                tail = raw.split('</pre>', 1)[1]
                if tail.strip():
                    lines.insert(i + 1, tail)
            else:
                pre.append(raw)
            i += 1
            continue
        s = raw.strip()
        if not s:
            cv.y += 10 if cv.y > PAD else 0
            i += 1
            continue
        if not title and '<b>' in s:
            title = plain(s).split('—')[0].strip()
            _title(cv, s)
            i += 1
            continue
        if _CAP.match(plain(s)):
            rows = []
            while i < len(lines) and _CAP.match(plain(lines[i].strip())):
                rows.append(_CAP.match(plain(lines[i].strip())).groups()); i += 1
            _captable(cv, rows)
            continue
        m = _PICK.match(s)
        if m:
            subs = []
            while i + 1 < len(lines) and lines[i + 1].startswith('   ') and not _PICK.match(lines[i + 1].strip()):
                subs.append(lines[i + 1]); i += 1
            _pick(cv, m, subs)
            i += 1
            continue
        if '<b>' in s and not mkline(s) and len(plain(s)) < 60:      # подзаголовок блока («⚽ Ставки дня · 26.11 — угадано 3/3», «🧠 Обучение»)
            cv.y += 8
            for ln in cv.wrap(runs(s), 34, W - 2 * PAD):
                cv.text(PAD, cv.y, ln, 34); cv.y += 50
            cv.mark()
            i += 1
            continue
        _line(cv, s)
        i += 1
    if cv.y <= PAD + 5:
        return [], texts, title
    return cv.done(), texts, title


def mkline(s: str) -> bool:
    return any(s.startswith(k) for k in MARK)


# ───────────────────────────── карточка ставки дня (закреп ⭐, этап 2) ─────────────────────────────
def render_pick(text: str) -> bytes:
    """Текст закрепа ставки дня (daypick.text / hpick._text / «снята») → PNG-карточка. Неизвестная строка — мелким текстом,
    ничего не теряется. Сбой — исключение (notify шлёт текст)."""
    lines = [ln for ln in re.sub(r'</?tg-spoiler>', '', text).split('\n') if ln.strip()]
    cv = Canvas()
    head = lines[0] if lines else ''
    off = 'снята' in plain(head)
    m = re.match(r'\s*(\S+?)(\d*)\s*<b>(.*?)</b>(.*)$', head)
    icon, num, title, tail = (m.group(1), m.group(2), plain(m.group(3)), plain(m.group(4)).strip(' ·')) if m else ('', '', plain(head), '')
    title = re.sub(r'\s*снята\s*$', '', title)                 # «снята» — плашкой справа
    rest = lines[1:]
    top = cv.y
    cat = _clean(tail.replace('❌', '')).replace('🎯', '').replace('💰', '').strip()
    if off:
        cat, ccol = 'снята', RED
    else:
        ccol = GRN if '💰' in tail else ACC

    def head_draw():
        y = top + 30
        caps(cv, PAD + 28, y + 8, ' · '.join(x for x in (_clean(title), f'№{num}' if num else '') if x), 22, RED if off else ACC)
        if cat:
            w = sum(cv.d.textlength(c, font=font('b', 20)) + 1.5 for c in cat.upper()) + 32
            cv.d.rounded_rectangle([W - PAD - 24 - w, y, W - PAD - 24, y + 42], radius=10, fill=ccol)
            caps(cv, W - PAD - 24 - w + 16, y + 10, cat, 20, (255, 255, 255), 1.5)

    head_draw()
    cv.y = top + 96
    body = []
    for ln in rest:
        s, p = ln.strip(), plain(ln).strip()
        if p.startswith('🏆') or p.startswith('🔎'):
            for w_ in cv.wrap([(_clean(p), 'r')], 26, W - 2 * PAD - 48):
                body.append(('mut', w_))
        elif '<s>' in s:
            lab, _, why = s.partition('</s>')
            body.append(('strike', plain(lab)))
            for w_ in cv.wrap(runs(why.strip(' —-')), 26, W - 2 * PAD - 48):
                body.append(('hist', w_))
        elif off and ' — ' in p and '%' not in p and not any(k == 'match' for k, _ in body):
            body.append(('match', p))
        elif re.search(r'\d+%\s*·\s*честный кф', p) or re.search(r'— \d+%', p) and '<b>' in s and ' — ' not in plain(s.split('</b>')[0]):
            body.append(('bet', s))
        elif s.startswith('<b>') and ' — ' in p and '%' not in p:
            body.append(('match', p))
        elif p.startswith('💵'):
            body.append(('price', p))
        else:
            for w_ in cv.wrap([(_clean(p), 'r')], 26, W - 2 * PAD - 48):
                body.append(('hist', w_))
    # высота карточки
    hmap = {'mut': 40, 'match': 62, 'bet': 120, 'price': 52, 'hist': 38, 'strike': 64}
    h = (cv.y - top) + sum(hmap[k] for k, _ in body) + 14
    cv.d.rounded_rectangle([PAD - 8, top, W - PAD + 8, top + h], radius=16, fill=CARD, outline=LINE, width=2)
    cv.d.rectangle([PAD - 8, top + 16, PAD - 3, top + h - 16], fill=RED if off else ccol)
    # шапка поверх фона карточки
    y0 = cv.y
    head_draw()
    cv.y = y0
    L = PAD + 24
    for kind, v in body:
        if kind == 'mut':
            cv.text(L, cv.y, v, 26, MUT, maxw=W - PAD - 20); cv.y += 40
        elif kind == 'hist':
            cv.text(L, cv.y, v, 24, MUT, maxw=W - PAD - 20); cv.y += 38
        elif kind == 'match':
            cv.text(L, cv.y, [(v, 'b')], 38, maxw=W - PAD - 20); cv.y += 62
        elif kind == 'strike':
            x = cv.text(L, cv.y, [(v, 'b')], 44, MUT, maxw=W - PAD - 20)
            cv.d.line([L - 4, cv.y + 28, x + 4, cv.y + 28], fill=RED, width=4); cv.y += 64
        elif kind == 'bet':
            label_html, _, after = v.partition('</b>')
            label = plain(label_html)
            pa = plain(after)
            emo = ''.join(ch for ch in ('🔥', '⚔️', '⚔') if ch in pa)[:2]
            pm = re.search(r'(\d+)%', pa)
            pv = int(pm.group(1)) if pm else None
            km = re.search(r'честный кф\s*([\d,\.]+)', pa)
            x = cv.text(L, cv.y, [(label, 'B')], 56, maxw=W - PAD - 260)
            if emo:
                cv.text(x + 12, cv.y + 10, [(emo, 'r')], 44)
            if pv is not None:
                ps = f'{pv}%'
                f = font('B', 56); cv.d.text((W - PAD - 24 - cv.d.textlength(ps, font=f), cv.y), ps, font=f, fill=ACC)
                bx0, bx1, by = L, W - PAD - 24, cv.y + 78
                cv.d.rectangle([bx0, by + 5, bx1, by + 9], fill=LINE)
                cv.d.rectangle([bx0, by + 3, bx0 + int((bx1 - bx0) * pv / 100), by + 11], fill=ACC)
            if km:
                t = f'честный кф {km.group(1)}'
                f = font('r', 24); cv.d.text((W - PAD - 24 - cv.d.textlength(t, font=f), cv.y + 96 - 4), t, font=f, fill=MUT)
            cv.y += 120
        elif kind == 'price':
            s = v.replace('💵', '').strip()
            evm = re.search(r'EV\s*([+\-−]?\d+[,\.]?\d*%)', s)
            pre = s[:evm.start()].rstrip(' ·') if evm else s
            x = cv.text(L, cv.y, [(pre, 'b')], 32)
            if evm:
                ev = evm.group(1)
                col = GRN if ev.startswith('+') else RED if ev[:1] in '-−' else MUT
                x = cv.text(x + 6, cv.y, [(' · EV ', 'r')], 32, MUT)
                cv.text(x, cv.y, [(ev, 'b')], 32, col)
            cv.y += 52
    cv.y = top + h
    buf = io.BytesIO()
    cv.img.crop((0, 0, W, cv.y + PAD)).save(buf, 'PNG', optimize=True)
    return buf.getvalue()


# ───────────────────────────── график банка (недельный отчёт, этап 3) ─────────────────────────────
def bank_chart(points: list[tuple], title: str, week: tuple | None = None) -> bytes:
    """points — [(дата, итог дня, ед.)] по порядку; рисуется накопленный банк по дням, неделя отчёта — подсветкой.
    Hi-tech: тонкая линия, точки дней, нулевая линия, подписи — минимум."""
    import datetime as _dt
    cv = Canvas()
    caps(cv, PAD, cv.y, title)
    cv.y += 40
    cum, s = [], 0.0
    for d, v in points:
        s += float(v); cum.append((d, s))
    last = cum[-1][1] if cum else 0.0
    col = GRN if last > 0 else RED if last < 0 else MUT
    cv.text(PAD, cv.y, [(f'{last:+.2f}'.replace('.', ',').replace('-', '−') + ' ед.', 'B')], 50, col)
    cv.y += 80
    top, H = cv.y, 460
    x0, x1 = PAD + 70, W - PAD - 10
    cv.d.rounded_rectangle([PAD - 8, top - 10, W - PAD + 8, top + H + 70], radius=16, fill=CARD, outline=LINE, width=2)
    if len(cum) < 2:
        cv.text(PAD + 20, top + 20, [('мало данных для графика', 'm')], 28, MUT)
        cv.y = top + H + 90
        buf = io.BytesIO(); cv.img.crop((0, 0, W, cv.y + PAD)).save(buf, 'PNG', optimize=True); return buf.getvalue()
    vals = [v for _, v in cum] + [0.0]
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    lo, hi = lo - span * 0.12, hi + span * 0.12
    Y = lambda v: top + 20 + (hi - v) / (hi - lo) * (H - 40)
    n = len(cum)
    X = lambda i: x0 + (x1 - x0) * (i / (n - 1))
    if week:                                                   # подсветка недели отчёта
        idx = [i for i, (d, _) in enumerate(cum) if week[0] <= d <= week[1]]
        if idx:
            a = X(idx[0]) - (x1 - x0) / (2 * (n - 1)); b = X(idx[-1]) + (x1 - x0) / (2 * (n - 1))
            cv.d.rectangle([max(x0, a), top + 6, min(x1, b), top + H - 6], fill=(238, 242, 255))
    for k in range(5):                                         # сетка
        v = lo + (hi - lo) * k / 4
        y = Y(v)
        cv.d.line([x0, y, x1, y], fill=LINE, width=1)
        t = f'{v:+.1f}'.replace('.', ',').replace('-', '−')
        f = font('m', 20); cv.d.text((x0 - 12 - cv.d.textlength(t, font=f), y - 12), t, font=f, fill=MUT)
    cv.d.line([x0, Y(0), x1, Y(0)], fill=MUT, width=2)          # ноль
    pts = [(X(i), Y(v)) for i, (_, v) in enumerate(cum)]
    cv.d.line(pts, fill=ACC, width=5, joint='curve')
    for (x, y), (_, v) in zip(pts, cum):
        cv.d.ellipse([x - 7, y - 7, x + 7, y + 7], fill=CARD, outline=ACC, width=3)
    step = max(1, n // 6)                                      # подписи дат
    for i in list(range(0, n, step)) + ([n - 1] if (n - 1) % step else []):
        t = f'{cum[i][0]:%d.%m}'
        f = font('m', 20); cv.d.text((X(i) - cv.d.textlength(t, font=f) / 2, top + H + 14), t, font=f, fill=MUT)
    cv.y = top + H + 70
    buf = io.BytesIO(); cv.img.crop((0, 0, W, cv.y + PAD)).save(buf, 'PNG', optimize=True)
    return buf.getvalue()
