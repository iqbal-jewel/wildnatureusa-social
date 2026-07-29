"""Render the post images.

Two layouts:

*Quiz cards* -- the flat-colour design from the Style & Image Specs tab:
eyebrow label, the clue centred and auto-fitted, and a footer line.

*Fact cards* -- a species photograph under a bottom-weighted scrim, with the
fact text over it. The photo comes from photos/ (see tools/fetch_photos.py).
Every one of those is CC-BY or CC-BY-SA, which requires attribution, so the
credit is burned into the image as well as added to the caption -- an image
reshared off-platform then keeps its attribution either way.

80px safe margin on all sides so nothing crops on mobile.

    python -m src.render            # render everything missing
    python -m src.render --force    # re-render even if the file exists
    python -m src.render FB-IMG-0005 FB-TXT-0001
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from . import plan

ROOT = Path(__file__).resolve().parent.parent
IMAGES = ROOT / "images"
FONTS = ROOT / "fonts"
PHOTOS = ROOT / "photos"
CREDITS = PHOTOS / "credits.json"

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


# --- fact cards -----------------------------------------------------------
def load_credits():
    if not CREDITS.exists():
        raise FileNotFoundError(
            f"{CREDITS} is missing. Run: python tools/fetch_photos.py")
    return json.loads(CREDITS.read_text("utf-8"))


def fact_text(post) -> str:
    """The fact sentence, without the hashtag block the caption carries."""
    return post.caption.split("\n\n")[0].strip()


def credit_line(entry) -> str:
    """Short attribution that satisfies CC-BY in the space available."""
    author = entry.get("author") or "Unknown"
    if len(author) > 42:
        author = author[:39].rstrip() + "..."
    return f"{author} / Wikimedia Commons / {entry.get('licence', '')}".strip(" /")


def _cover(img, w, h):
    """Scale-and-centre-crop so the photo fills w x h without distortion."""
    src_ratio, dst_ratio = img.width / img.height, w / h
    if src_ratio > dst_ratio:          # too wide -- crop the sides
        new_w = int(img.height * dst_ratio)
        box = ((img.width - new_w) // 2, 0, (img.width + new_w) // 2, img.height)
    else:                              # too tall -- crop top/bottom
        new_h = int(img.width / dst_ratio)
        box = (0, (img.height - new_h) // 2, img.width, (img.height + new_h) // 2)
    return img.resize((w, h), Image.LANCZOS, box=box)


def _lighten(hex_colour, amount=0.55):
    """Mix a colour toward white.

    The brand green (#2E7D32) is chosen for white backgrounds; over a dark
    scrim it reads as muddy, so the accent is lifted for legibility instead of
    hardcoding a second palette.
    """
    h = hex_colour.lstrip("#")
    if len(h) != 6:
        return "#8BC34A"
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    mix = lambda c: int(c + (255 - c) * amount)  # noqa: E731
    return f"#{mix(r):02X}{mix(g):02X}{mix(b):02X}"


def _scrim(w, h, strength=0.88):
    """Bottom-weighted dark gradient so text stays legible on any photo.

    Transparent across the top half, ramping to near-opaque at the base. The
    ramp is quadratic rather than linear: a linear fade leaves a visible band
    where it starts, which reads as a rendering artefact.
    """
    grad = Image.new("L", (1, h), 0)
    px = grad.load()
    start = int(h * 0.38)
    span = h - start
    for y in range(start, h):
        t = (y - start) / span
        px[0, y] = int(255 * strength * t * t)
    return grad.resize((w, h))


def render_fact(post, credits, out_dir: Path = IMAGES) -> Path:
    """Photo background + fact text. Returns the written path."""
    entry = credits.get(post.animal)
    if not entry:
        raise KeyError(f"no photo for {post.animal!r} -- run tools/fetch_photos.py")
    photo_path = PHOTOS / entry["file"]
    if not photo_path.exists():
        raise FileNotFoundError(f"{photo_path} missing for {post.animal!r}")

    out_dir.mkdir(parents=True, exist_ok=True)
    W, H = post.width, post.height
    scale = W / 1080

    base = _cover(Image.open(photo_path).convert("RGB"), W, H)
    base.paste(Image.new("RGB", (W, H), "black"), (0, 0), _scrim(W, H))

    draw = ImageDraw.Draw(base)
    inner = W - 2 * MARGIN
    fg = "#FFFFFF"

    # Credit, bottom-left, small and quiet but legible.
    credit_size = max(int(22 * scale), 14)
    cf = _font(credit_size)
    credit = credit_line(entry)
    cy = H - MARGIN + int(14 * scale)
    draw.text((MARGIN, cy), credit, font=cf, fill="#D8D8D8")

    # Species name, above the credit.
    name_size = max(int(40 * scale), 20)
    nf = _font(name_size)
    ny = cy - int(18 * scale) - name_size
    draw.text((MARGIN, ny), post.animal.upper(), font=nf,
              fill=_lighten(post.bg_hex))

    # Fact text fills upward from the species name.
    top = int(H * 0.34)
    bottom = ny - int(26 * scale)
    font, lines, line_h = _fit(draw, fact_text(post), inner, bottom - top,
                               int(64 * scale))
    y = bottom - len(lines) * line_h
    for line in lines:
        draw.text((MARGIN, y), line, font=font, fill=fg)
        y += line_h

    path = out_dir / post.image_name
    base.save(path, "JPEG", quality=88, optimize=True, progressive=True)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("post_ids", nargs="*", help="limit to these post ids")
    ap.add_argument("--force", action="store_true",
                    help="re-render even when the image already exists")
    args = ap.parse_args()

    posts = plan.load(ROOT / "plan" / "content_plan_fixed.xlsx")
    if args.post_ids:
        wanted = set(args.post_ids)
        posts = [p for p in posts if p.post_id in wanted]

    if _font_path() is None:
        print("WARNING: no TrueType font found; falling back to a bitmap font.")
        print("Drop Poppins-Bold.ttf into fonts/ for the intended look.")

    credits = load_credits()
    made = skipped = 0
    failures = []
    for p in posts:
        target = IMAGES / p.image_name
        if target.exists() and not args.force:
            skipped += 1
            continue
        try:
            render(p) if p.is_quiz else render_fact(p, credits)
            made += 1
        except Exception as e:
            failures.append((p.post_id, f"{type(e).__name__}: {e}"))

    print(f"rendered {made}, already present {skipped}, failed {len(failures)}"
          f" -> {IMAGES}")
    for pid, err in failures[:20]:
        print(f"  FAIL {pid}: {err}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
