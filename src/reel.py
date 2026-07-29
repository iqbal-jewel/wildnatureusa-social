"""Render a quiz post as a short vertical video.

Reels are rendered on demand, never committed: neither platform's Reels
endpoint needs a public URL the way Instagram's image posts do (both take
the video as raw bytes), so there is nothing to gain from checking a video
into a repo Actions clones every run and real cost -- 552 clips at even
500 KB each is a repo problem the image posts deliberately avoided.

Uses the real per-species photo library (photos/, via render.load_credits)
rather than a hand-picked example, and a full-frame cover crop with a
top/bottom scrim rather than the letterbox trick that only worked because
one particular photo happened to have a black background.
"""
import sys
import tempfile
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
PHOTOS = ROOT / "photos"

sys.path.insert(0, str(TOOLS))
import reel_engine as eng  # noqa: E402

from . import render  # noqa: E402

DURATION = 6.0
EYEBROW = "GUESS THE ANIMAL"
FOOTER = "Answer in the comments ↓"

EYEBROW_IN = (0.15, 0.55)
CLUE_IN = (0.45, 1.75)
FOOTER_IN = (5.2, 5.8)


def render_quiz_reel(post, credits, work_dir=None) -> Path:
    """Render post (a quiz Post) to an MP4. Returns the path; caller deletes
    it once uploaded -- this is scratch output, not a repo asset.
    """
    entry = credits.get(post.animal)
    if not entry:
        raise KeyError(f"no photo for {post.animal!r} -- run tools/fetch_photos.py")
    photo_path = PHOTOS / entry["file"]
    if not photo_path.exists():
        raise FileNotFoundError(f"{photo_path} missing for {post.animal!r}")

    own_temp = work_dir is None
    work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="reel_"))
    frames_dir = work_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    base_img, band = eng.build_kenburns_cover_source(photo_path)
    scrim = eng.scrim_layer(eng.W, eng.H)
    credit_text = render.credit_line(entry)

    n_frames = round(DURATION * eng.FPS)
    for i in range(n_frames):
        t = i / eng.FPS
        progress = i / max(n_frames - 1, 1)

        canvas = Image.new("RGBA", (eng.W, eng.H), (0, 0, 0, 255))
        canvas.paste(eng.kenburns_crop(base_img, band, progress, focal=(0.5, 0.42)), (0, 0))
        canvas.alpha_composite(scrim)

        eyebrow_p = eng.local_progress(t, EYEBROW_IN)
        eng.draw_wipe(canvas, EYEBROW, 46, 130, eyebrow_p)
        # Anchored to sit inside the scrim's bottom band (see scrim_layer's
        # default bottom_frac=0.42 of H=1920, i.e. from y=1113) regardless of
        # how many lines the clue wraps to, comfortably clear of the footer.
        eng.draw_staggered_lines(canvas, post.overlay, 56, 1380, t, CLUE_IN,
                                 max_width=eng.W - 160)
        eng.draw_fade(canvas, FOOTER, 36, eng.H - 220, t, FOOTER_IN)
        eng.draw_credit(canvas, credit_text)

        canvas.convert("RGB").save(frames_dir / f"f_{i:04d}.png")

    out_path = work_dir / f"{post.post_id}_reel.mp4"
    eng.encode(frames_dir, out_path)

    for f in frames_dir.glob("*.png"):
        f.unlink()
    frames_dir.rmdir()

    return out_path
