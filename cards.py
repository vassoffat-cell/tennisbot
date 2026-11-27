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
FONT = {'r': ROOT / 'assets/fonts/DejaVuSans.ttf', 'b': ROOT / 'assets/fonts/DejaVuSans-Bold.ttf',
        'i': ROOT / 'assets/fonts/DejaVuSans-Oblique.ttf'}
EMOJI = ROOT / 'assets/emoji'

W, PAD, MAX_H = 1080, 48, 2400                       # Telegram показывает фото до 2560 по длинной стороне
# светлая тема (пользователь 27.11 13:19)
BG, CARD, LINE = (243, 245, 248), (255, 255, 255), (226, 230, 236)
TXT, MUT = (24, 28, 36), (104, 112, 126)
GRN, RED, AMB, BLU = (22, 150, 72), (214, 48, 49), (196, 132, 0), (37, 99, 235)
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


def split(blocks: list[str]) -> tuple[list[str], list[str]]:
    """Блоки → (строки для картинки, текстовые блоки со свёрнутыми списками). Строка перед <blockquote expandable> — заголовок списка,
    уходит в текст вместе с ним."""
    body = '\n\n'.join(b for b in blocks if b)
    body = re.sub(r'</?tg-spoiler>', '', body)
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
                    im = emoji(seg, int(size * 1.05))
                    if im is not None:
                        if maxw and x + im.width > maxw:
                            return x
                        self.img.paste(im, (int(x), int(y + size * 0.08)), im)
                        x += im.width + int(size * 0.18)
                        continue
                    if not _has_glyph(seg[0]):
                        continue
                f = font(st, size)
                col = (colors or {}).get(st, color)
                if maxw and x + self.d.textlength(seg, font=f) > maxw:
                    while seg and x + self.d.textlength(seg + '…', font=f) > maxw:
                        seg = seg[:-1]
                    self.d.text((x, y), seg + '…', font=f, fill=col)
                    return maxw
                self.d.text((x, y), seg, font=f, fill=col)
                x += self.d.textlength(seg, font=f)
        return x

    def width(self, parts, size) -> float:
        w = 0.0
        for t, st in parts:
            for seg in _EMO.split(t):
                if not seg:
                    continue
                if _EMO.fullmatch(seg):
                    w += int(size * 1.05) + int(size * 0.18) if emoji(seg, int(size * 1.05)) is not None else 0
                else:
                    w += self.d.textlength(seg, font=font(st, size))
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


def _title(cv: Canvas, line: str):
    m = re.match(r'\s*(.*?)<b>(.*?)</b>(.*)$', line)
    pre, head, rest = (m.group(1), m.group(2), m.group(3)) if m else ('', plain(line), '')
    x = cv.text(PAD, cv.y, runs(pre), 44) if pre.strip() else PAD
    cv.text(x + (8 if pre.strip() else 0), cv.y, [(html.unescape(head), 'b')], 44, maxw=W - PAD)
    cv.y += 64
    rest = plain(rest).strip(' ·—-')
    if rest:
        for ln in cv.wrap([(rest, 'r')], 30, W - 2 * PAD):
            cv.text(PAD, cv.y, ln, 30, MUT)
            cv.y += 42
    cv.y += 14
    cv.mark()


def _table(cv: Canvas, rows: list[str]):
    """<pre>-таблица: «Метка   n   %  🟢» → карточка с колонками; строки без отступа — заголовки групп."""
    parsed = []
    for r in rows:
        s = html.unescape(re.sub(r'<[^>]+>', '', r)).rstrip()
        if not s.strip() or set(s.strip()) <= set('─-—='):
            continue
        ind = len(s) - len(s.lstrip(' '))
        flags = [f for f in FLAG if f in s]
        s2 = s
        for f in list(FLAG) + ['▶']:
            s2 = s2.replace(f, '')
        cells = re.split(r'\s{2,}', s2.strip())
        label, vals = cells[0], cells[1:]
        if len(vals) == 1 and ' ' in vals[0]:
            vals = vals[0].split()
        extra = [v for v in vals if not re.search(r'\d', v)]       # «⛔» и т. п. — к метке, не в колонку
        vals = [v for v in vals if re.search(r'\d', v)]
        if extra:
            label = f"{label} {' '.join(extra)}"
        parsed.append(dict(ind=ind, label=label, vals=vals, flag=flags[0] if flags else '', head=(ind == 0)))
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
    cv.d.rounded_rectangle([PAD - 8, top, W - PAD + 8, top + h], radius=22, fill=CARD, outline=LINE, width=2)
    cv.y += 16
    if hdr:
        cv.text(PAD + 16, cv.y, [(hdr['label'], 'r')], 26, MUT)
        for k, v in enumerate(reversed(hdr['vals'])):
            f = font('r', 26); cv.d.text((xs[k] - cv.d.textlength(v, font=f), cv.y), v, font=f, fill=MUT)
        cv.y += 50
    for i, p in enumerate(body):
        if p['head'] and i:
            cv.d.line([PAD + 10, cv.y - 6, W - PAD - 10, cv.y - 6], fill=LINE, width=2)
        x0 = PAD + 16 + (28 if p['ind'] == 1 else 52 if p['ind'] >= 2 else 0)
        st = 'b' if p['head'] else 'r'
        cv.text(x0, cv.y, [(p['label'], st)], size, TXT if p['head'] or p['ind'] <= 1 else MUT, maxw=int(label_max))
        col = FLAG.get(p['flag'], TXT)
        for k, v in enumerate(reversed(p['vals'])):
            last = k == 0
            c = col if last and p['flag'] else (GRN if v.startswith('+') and re.search(r'\d', v) and k == 0 else
                                                RED if re.match(r'[−-]\d', v) and k == 0 else TXT)
            f = font('b' if last else 'r', size)
            cv.d.text((xs[k] - cv.d.textlength(v, font=f), cv.y), v, font=f, fill=c)
        if p['flag']:
            cv.d.ellipse([right + 12, cv.y + 9, right + 28, cv.y + 25], fill=col)
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
    cv.d.rounded_rectangle([PAD - 8, top, W - PAD + 8, top + h], radius=24, fill=CARD, outline=LINE, width=2)
    cv.d.rounded_rectangle([PAD - 8, top, PAD + 4, top + h], radius=6, fill=col)
    x = cv.text(PAD + 28, top + 18, [(num, 'b')], 34, MUT)
    cv.text(x + 14, top + 16, [(line, 'b')], 38)
    res = {'✅': 'угадано', '❌': 'мимо', '⏳': 'ждём итог', '⚪': 'возврат', '↩️': 'возврат'}.get(mk, '')
    if res:
        f = font('b', 28); w = cv.d.textlength(res, font=f) + 32
        cv.d.rounded_rectangle([W - PAD - 16 - w, top + 20, W - PAD - 16, top + 62], radius=20, fill=col)
        cv.d.text((W - PAD - w, top + 26), res, font=f, fill=(255, 255, 255))
    cv.text(PAD + 28, top + 70, [(match, 'r')], 30, maxw=W - PAD - 20)
    cv.text(PAD + 28, top + 110, [(info, 'r')], 26, MUT, maxw=W - PAD - 20)
    y = top + 150
    for s in subs:
        cv.text(PAD + 28, y, [(plain(s).strip(), 'i')], 24, MUT, maxw=W - PAD - 20); y += 40
    cv.y = top + h + 14
    cv.mark()


_CAP = re.compile(r'^(.+?) — (\d+/\d+) · прогнозов (\d+)(?: · ROI ([+\-−]?\d+%))?\s*$')


def _captable(cv: Canvas, rows: list[tuple]):
    """Сводка каперов «Канал — 6/11 · прогнозов 19 · ROI +13%» → таблица: канал | угадано | прогнозов | ROI (цвет по знаку)."""
    top, size = cv.y, 30
    h = 50 * (len(rows) + 1) + 30
    cv.d.rounded_rectangle([PAD - 8, top, W - PAD + 8, top + h], radius=22, fill=CARD, outline=LINE, width=2)
    xs = (W - PAD - 330, W - PAD - 170, W - PAD - 24)       # правые края колонок
    y = top + 16
    for k, hd in enumerate(('угадано', 'всего', 'ROI')):
        f = font('r', 26); cv.d.text((xs[k] - cv.d.textlength(hd, font=f), y), hd, font=f, fill=MUT)
    cv.text(PAD + 16, y, [('канал', 'r')], 26, MUT)
    y += 50
    for name, wn, n, roi in rows:
        cv.text(PAD + 16, y, [(name, 'b')], size, maxw=xs[0] - 150)
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
