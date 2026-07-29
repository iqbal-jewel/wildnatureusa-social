"""Shared frame-rendering engine for reel prototypes.

Ken Burns photo background + a small library of text-entrance effects
(wipe, staggered slide-fade, plain fade), all driven off a timeline of
(text, window) pairs. Used by both make_test_reel.py (quiz format) and
make_fact_reel.py (fact format) so the two share one engine instead of
diverging copies.
"""
import glob
import os
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
]


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def ease_out_cubic(t):
    t = clamp(t)
    return 1 - (1 - t) ** 3


def ease_out_expo(t):
    t = clamp(t)
    return 1.0 if t >= 1 else 1 - 2 ** (-10 * t)


def stagger(index, count, t, overlap=0.55):
    if count <= 1:
        return clamp(t)
    span = 1.0 / (count - (count - 1) * overlap)
    start = index * span * (1 - overlap)
    return clamp((t - start) / span) if span > 0 else clamp(t)


def local_progress(t, window):
    a, b = window
    return clamp((t - a) / (b - a)) if t >= a else 0.0


def font(size):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def wrap(draw, text, fnt, max_width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=fnt) <= max_width or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def find_ffmpeg():
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    for pattern in (
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg*\**\bin"),
        os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\*FFmpeg*\**\bin"),
        r"C:\ProgramData\chocolatey\bin",
    ):
        for d in sorted(glob.glob(pattern, recursive=True), reverse=True):
            exe = os.path.join(d, "ffmpeg.exe")
            if os.path.exists(exe):
                return exe
    raise RuntimeError("ffmpeg not found. Install with: winget install ffmpeg --source winget")


# --- Ken Burns background --------------------------------------------------
def build_kenburns_source(photo_path, band_w=W, base_zoom_margin=1.3):
    """Pre-scale the photo with headroom so a Ken Burns crop can pan/zoom.

    Letterbox variant: returns (base_img, (band_w, band_h)) sized to the
    photo's own aspect ratio. Only looks right when the photo's background is
    itself near-uniform (see make_test_reel.py's beluga case) -- otherwise
    use build_kenburns_cover_source, which fills the whole frame.
    """
    im = Image.open(photo_path).convert("RGB")
    band_h = round(band_w * im.height / im.width)
    base_w = round(band_w * base_zoom_margin)
    base_h = round(base_w * im.height / im.width)
    return im.resize((base_w, base_h), Image.LANCZOS), (band_w, band_h)


def build_kenburns_cover_source(photo_path, target_w=W, target_h=H, base_zoom_margin=1.3):
    """Pre-scale a photo of *any* aspect ratio to cover the full W x H frame.

    Used for the general per-species case, where photos vary in shape and a
    fixed letterbox band would crop unpredictably. Combine with a top/bottom
    scrim (see scrim_layer) since an arbitrary photo can't be relied on to
    have a legible-behind-text region the way the hand-picked beluga shot did.
    """
    im = Image.open(photo_path).convert("RGB")
    scale = max(target_w / im.width, target_h / im.height) * base_zoom_margin
    base = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    return base, (target_w, target_h)


def kenburns_crop(base_img, band_size, progress, focal=(0.5, 0.5), zoom_end=1.14):
    band_w, band_h = band_size
    zoom = 1.0 + (zoom_end - 1.0) * progress
    crop_w, crop_h = band_w / zoom, band_h / zoom
    cx, cy = base_img.width * focal[0], base_img.height * focal[1]
    cx = clamp(cx, crop_w / 2, base_img.width - crop_w / 2)
    cy = clamp(cy, crop_h / 2, base_img.height - crop_h / 2)
    box = (cx - crop_w / 2, cy - crop_h / 2, cx + crop_w / 2, cy + crop_h / 2)
    return base_img.crop(box).resize((band_w, band_h), Image.LANCZOS)


def scrim_layer(w, h, top_frac=0.30, bottom_frac=0.42, strength=0.72):
    """Dark gradient, heavier at top and bottom, transparent through the
    middle -- keeps the eyebrow and clue/footer legible on an arbitrary
    photo without knowing its composition in advance. Static per post, so
    callers should build it once outside the per-frame loop.
    """
    alpha = Image.new("L", (1, h), 0)
    px = alpha.load()
    top_n = round(h * top_frac)
    bot_n = round(h * bottom_frac)
    for y in range(top_n):
        t = 1 - y / max(top_n - 1, 1)
        px[0, y] = round(255 * strength * t * t)
    for i in range(bot_n):
        y = h - 1 - i
        t = 1 - i / max(bot_n - 1, 1)
        px[0, y] = max(px[0, y], round(255 * strength * t * t))
    alpha = alpha.resize((w, h))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    layer.putalpha(alpha)
    black = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    black.putalpha(alpha)
    return black


# --- text effects -----------------------------------------------------------
def draw_wipe(canvas, text, size, y, progress, fill=(255, 255, 255, 255)):
    """Left-to-right reveal wipe -- for a short eyebrow/label line."""
    if progress <= 0:
        return
    fnt = font(size)
    draw = ImageDraw.Draw(canvas)
    tw = draw.textlength(text, font=fnt)
    x = (W - tw) / 2

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((x, y), text, font=fnt, fill=fill)

    reveal_edge = round(x + tw * ease_out_cubic(progress))
    strip = layer.crop((0, 0, max(reveal_edge, 0), H))
    canvas.paste(strip, (0, 0), strip)


def draw_staggered_lines(canvas, text, size, top, t, window, max_width=None,
                         line_height=None, overlap=0.55, fill=(255, 255, 255)):
    """Wrapped multi-line text, each line sliding up + fading in with stagger."""
    fnt = font(size)
    draw = ImageDraw.Draw(canvas)
    max_width = max_width or (W - 160)
    line_height = line_height or round(size * 1.35)
    lines = wrap(draw, text, fnt, max_width)

    for i, line in enumerate(lines):
        p = stagger(i, len(lines), local_progress(t, window), overlap=overlap)
        alpha = ease_out_cubic(p)
        if alpha <= 0:
            continue
        offset = round(40 * (1 - ease_out_expo(p)))
        lw = draw.textlength(line, font=fnt)
        x, y = (W - lw) / 2, top + i * line_height + offset
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text((x, y), line, font=fnt,
                                   fill=(*fill, round(255 * alpha)))
        canvas.alpha_composite(layer)
    return len(lines) * line_height


def draw_fade(canvas, text, size, y, t, window, fill=(255, 255, 255)):
    p = ease_out_cubic(local_progress(t, window))
    if p <= 0:
        return
    fnt = font(size)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tw = d.textlength(text, font=fnt)
    d.text(((W - tw) / 2, y), text, font=fnt, fill=(*fill, round(255 * p)))
    canvas.alpha_composite(layer)


def draw_credit(canvas, text, alpha=140):
    fnt = font(22)
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((30, H - 46), text, font=fnt, fill=(255, 255, 255, alpha))
    canvas.alpha_composite(layer)


# --- encode -----------------------------------------------------------------
def encode(frames_dir, out_path, fps=FPS):
    ffmpeg = find_ffmpeg()
    cmd = [
        ffmpeg, "-y", "-framerate", str(fps),
        "-i", str(Path(frames_dir) / "f_%04d.png"),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out_path
