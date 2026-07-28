"""Render the flat-colour quiz images described by the Style & Image Specs tab.

Layout: eyebrow label, the clue centred and auto-fitted, and a footer line.
80px safe margin on all sides so nothing crops on mobile.

    python -m src.render            # render every quiz image
    python -m src.render FB-IMG-0005
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import plan

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
FONTS = ROOT / "fonts"

EYEBROW = "GUESS THE ANIMAL"
FOOTER = "Answer in the comments \u2193"
MARGIN = 80

# Preferred first; falls back through whatever the host actually has.
FONT_CANDIDATES = [
    FONTS / "Poppins-Bold.ttf",
    FONTS / "Nunito-Bold.ttf",
    FONTS / "Montserrat-Bold.ttf",
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
]


def _font_path() -> Path | None:
    for c in FONT_CANDIDATES:
        if c.exists():
            return c
    return None


def _font(size: int):
    p = _font_path()
    if p is None:
        return ImageFont.load_default()
    return ImageFont.truetype(str(p), size)


def _wrap(draw, text, font, max_width):
    """Greedy word wrap to a pixel width."""
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _fit(draw, text, max_width, max_height, start, minimum=28):
    """Largest size at which the wrapped text fits the box."""
    size = start
    while size > minimum:
        font = _font(size)
        lines = _wrap(draw, text, font, max_width)
        line_h = int(size * 1.32)
        if len(lines) * line_h <= max_height:
            return font, lines, line_h
        size -= 2
    font = _font(minimum)
    return font, _wrap(draw, text, font, max_width), int(minimum * 1.32)


def render(post, out_dir: Path = IMAGES) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (post.width, post.height), post.bg_hex)
    draw = ImageDraw.Draw(img)
    fg = post.fg_hex

    scale = post.width / 1080
    eyebrow_size = max(int(44 * scale), 22)
    footer_size = max(int(34 * scale), 18)
    clue_start = int(80 * scale)

    ef, ff = _font(eyebrow_size), _font(footer_size)
    inner = post.width - 2 * MARGIN

    # eyebrow, top
    ew = draw.textlength(EYEBROW, font=ef)
    ey = MARGIN
    draw.text(((post.width - ew) / 2, ey), EYEBROW, font=ef, fill=fg)

    # footer, bottom
    fw = draw.textlength(FOOTER, font=ff)
    fy = post.height - MARGIN - footer_size
    draw.text(((post.width - fw) / 2, fy), FOOTER, font=ff, fill=fg)

    # clue fills the space between them
    top = ey + eyebrow_size + int(48 * scale)
    bottom = fy - int(48 * scale)
    font, lines, line_h = _fit(draw, post.overlay, inner, bottom - top, clue_start)

    block = len(lines) * line_h
    y = top + (bottom - top - block) / 2
    for line in lines:
        w = draw.textlength(line, font=font)
        draw.text(((post.width - w) / 2, y), line, font=font, fill=fg)
        y += line_h

    path = out_dir / post.image_name
    img.save(path, "PNG", optimize=True)
    return path


def main():
    posts = [p for p in plan.load(ROOT / "plan" / "content_plan_fixed.xlsx") if p.is_quiz]
    wanted = set(sys.argv[1:])
    if wanted:
        posts = [p for p in posts if p.post_id in wanted]
    if _font_path() is None:
        print("WARNING: no TrueType font found; falling back to a bitmap font.")
        print("Drop Poppins-Bold.ttf into fonts/ for the intended look.")
    for p in posts:
        render(p)
    print(f"rendered {len(posts)} images -> {IMAGES}")


if __name__ == "__main__":
    main()
