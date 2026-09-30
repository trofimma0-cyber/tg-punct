import functools
from PIL import Image, ImageDraw, ImageFont

WIDTH      = 820
PAD        = 24
BUBBLE_MAX = 560
MAX_PAGE_H = 4000

_FONT_REGULAR = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "C:/Windows/Fonts/arial.ttf",
)
_FONT_BOLD = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


@functools.lru_cache(maxsize=16)
def _get_font(bold: bool, size: int):
    paths = _FONT_BOLD if bold else _FONT_REGULAR
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _wrap(text, font, max_w, draw):
    lines = []
    for para in (text or "").split("\n"):
        if not para:
            lines.append("")
            continue
        cur = ""
        for w in para.split(" "):
            test = (cur + " " + w).strip()
            if draw.textlength(test, font=font) <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                while draw.textlength(w, font=font) > max_w and len(w) > 1:
                    cut = len(w)
                    while cut > 1 and draw.textlength(w[:cut], font=font) > max_w:
                        cut -= 1
                    lines.append(w[:cut])
                    w = w[cut:]
                cur = w
        if cur:
            lines.append(cur)
    return lines


class Bubble:
    __slots__ = ("name", "text", "time", "is_owner", "media_label")

    def __init__(self, name, text, time_str, is_owner, media_label=None):
        self.name = name
        self.text = text or ""
        self.time = time_str
        self.is_owner = is_owner
        self.media_label = media_label


def render_pages(bubbles, chat_title):
    font        = _get_font(False, 18)
    font_header = _get_font(True, 20)
    font_small  = _get_font(False, 13)

    meas = Image.new("RGB", (10, 10))
    mdraw = ImageDraw.Draw(meas)

    pages = []
    img = None
    draw = None
    y = 0

    def start_page():
        nonlocal img, draw, y
        img = Image.new("RGB", (WIDTH, MAX_PAGE_H), (245, 245, 245))
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, WIDTH, 44], fill=(82, 136, 193))
        draw.text((PAD, 10), (chat_title or "")[:60], font=font_header, fill=(255, 255, 255))
        y = 60

    def commit_page():
        nonlocal img
        if img is None:
            return
        pages.append(img.crop((0, 0, WIDTH, max(y + 20, 100))))

    start_page()
    for b in bubbles:
        prefix = f"[{b.media_label}]\n" if b.media_label else ""
        full = prefix + b.text
        inner_max = BUBBLE_MAX - 24
        lines = _wrap(full, font, inner_max, mdraw)
        line_h = 22
        body_h = max(line_h, len(lines) * line_h)
        bubble_h = body_h + 26 + 16
        widest = max((mdraw.textlength(l, font=font) for l in lines), default=0)
        bubble_w = int(min(BUBBLE_MAX, max(180, widest + 24)))

        if y + bubble_h + 10 > MAX_PAGE_H - 20:
            commit_page()
            start_page()

        if b.is_owner:
            x0 = WIDTH - PAD - bubble_w
            x1 = WIDTH - PAD
            bg = (220, 240, 255)
        else:
            x0 = PAD
            x1 = PAD + bubble_w
            bg = (255, 255, 255)

        draw.rounded_rectangle([x0, y, x1, y + bubble_h],
                               radius=14, fill=bg, outline=(205, 205, 205))
        draw.text((x0 + 12, y + 6),
                  f"{b.name}  •  {b.time}",
                  font=font_small, fill=(120, 120, 120))
        ty = y + 26
        for line in lines:
            draw.text((x0 + 12, ty), line, font=font, fill=(20, 20, 20))
            ty += line_h
        y += bubble_h + 10

    commit_page()
    return pages
