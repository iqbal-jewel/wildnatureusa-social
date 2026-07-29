"""Source one freely-licensed photograph per species from Wikimedia.

The species names in the plan are common names, so we resolve each through
Wikipedia (which follows redirects), take that article's lead image, then read
the licence off the underlying Commons file. Anything not clearly free for
commercial use *with* modification is rejected -- these photos are cropped and
drawn over on a Page that carries ads, so NonCommercial and NoDerivs are both
licence breaches waiting to happen.

    python tools/fetch_photos.py               # fetch whatever is missing
    python tools/fetch_photos.py --force       # re-fetch even if cached
    python tools/fetch_photos.py --habitat     # skip specimen-on-white shots
    python tools/fetch_photos.py "King Cobra"  # just these species

`--habitat` rejects candidates that look like a cut-out on a blank backdrop,
or that are too small to fill a 1080px card without upscaling, and keeps
searching. It cannot judge whether a photo is *editorially* right: asked for a
better Atlantic Bluefin Tuna it happily returned a dead one on a boat deck.
For those, list the Commons file by hand in photos/pinned.json, which is used
verbatim and bypasses every heuristic.

Writes photos/<slug>.jpg plus photos/credits.json. Species that cannot be
satisfied land in photos/unresolved.json so the render step fails loudly
instead of quietly dropping them.
"""
import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import plan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PHOTOS = ROOT / "photos"
CREDITS = PHOTOS / "credits.json"
UNRESOLVED = PHOTOS / "unresolved.json"
PINNED = PHOTOS / "pinned.json"
PLAN_PATH = ROOT / "plan" / "content_plan_fixed.xlsx"

WP_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Wikimedia asks for a descriptive agent with contact details.
UA = ("WildNatureUSA-social/1.0 "
      "(https://github.com/iqbal-jewel/wildnatureusa-social; "
      "iqbalhossenjewel@gmail.com)")

THUMB_PX = 1600   # what we ask Commons for
MIN_PX = 640      # short edge below this upscales badly at 1080 square
SAVE_PX = 1400    # what we keep on disk; the render target is 1080 square
PAUSE = 0.3

# Licence must allow commercial use and derivatives.
PHRASE_BLOCKED = ("noncommercial", "non-commercial", "noderiv", "no derivative",
                  "fair use", "non-free", "nonfree")
# GFDL is deliberately absent: it is a free licence, but complying means
# reproducing the full licence text, which cannot be done in a photo caption.
ALLOWED_PREFIX = ("cc0", "cc by", "cc-by", "public domain", "pd",
                  "attribution")


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def clean(html):
    if not html:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html))).strip()


def get(session, url, params):
    last = None
    for attempt in range(4):
        try:
            r = session.get(url, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(2 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:  # transient network / rate limiting
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"giving up on {url}: {last}")


def lead_image(session, species):
    """(commons_filename, thumb_url) for the species article's lead image."""
    data = get(session, WP_API, {
        "action": "query", "format": "json", "formatversion": "2",
        "titles": species, "redirects": "1",
        "prop": "pageimages", "piprop": "name|thumbnail",
        "pithumbsize": THUMB_PX,
    })
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing"):
        return None, None
    page = pages[0]
    return page.get("pageimage"), (page.get("thumbnail") or {}).get("source")


def commons_search(session, species, limit=8):
    """Fallback: search Commons directly when the article has no lead image.

    Some species articles carry only a taxobox image that the pageimages API
    does not expose. Returns a list of candidate file names, best match first.
    """
    data = get(session, COMMONS_API, {
        "action": "query", "format": "json", "formatversion": "2",
        "list": "search", "srsearch": f'{species} filetype:bitmap',
        "srnamespace": "6", "srlimit": str(limit),
    })
    hits = data.get("query", {}).get("search", [])
    names = []
    for h in hits:
        title = h.get("title", "")
        if title.lower().startswith("file:"):
            title = title[5:]
        if title.lower().endswith((".jpg", ".jpeg", ".png")):
            names.append(title)
    return names


def thumb_for(session, filename, width=THUMB_PX):
    """Thumbnail URL for a Commons file at the given width."""
    data = get(session, COMMONS_API, {
        "action": "query", "format": "json", "formatversion": "2",
        "titles": f"File:{filename}", "prop": "imageinfo",
        "iiprop": "url", "iiurlwidth": str(width),
    })
    pages = data.get("query", {}).get("pages", [])
    if not pages or not pages[0].get("imageinfo"):
        return None
    info = pages[0]["imageinfo"][0]
    return info.get("thumburl") or info.get("url")


def commons_meta(session, filename):
    """Licence + attribution for a Commons file, or None if unreadable."""
    data = get(session, COMMONS_API, {
        "action": "query", "format": "json", "formatversion": "2",
        "titles": f"File:{filename}", "prop": "imageinfo",
        "iiprop": "url|extmetadata",
    })
    pages = data.get("query", {}).get("pages", [])
    if not pages or pages[0].get("missing") or not pages[0].get("imageinfo"):
        return None
    info = pages[0]["imageinfo"][0]
    ext = info.get("extmetadata", {})

    def field(key):
        return clean((ext.get(key) or {}).get("value"))

    return {
        "licence": field("LicenseShortName") or field("License"),
        "usage_terms": field("UsageTerms"),
        "author": field("Artist") or "Unknown",
        "licence_url": field("LicenseUrl"),
        "descriptionurl": info.get("descriptionurl", ""),
    }


def is_free(meta):
    """True only for licences allowing commercial use and derivative works."""
    lic = (meta.get("licence") or "").lower().strip()
    blob = f"{lic} {(meta.get('usage_terms') or '').lower()}"
    if not lic:
        return False

    # Tokenise so a bare "nc"/"nd" cannot match inside an unrelated word.
    tokens = set(re.split(r"[^a-z0-9]+", blob))
    if tokens & {"nc", "nd"}:
        return False
    if any(phrase in blob for phrase in PHRASE_BLOCKED):
        return False

    return lic.startswith(ALLOWED_PREFIX)


def studio_score(raw):
    """How much a photo looks like a specimen shot on a blank backdrop.

    Returns the mean brightness of the border ring and its spread. A cut-out
    on white gives a very bright, very flat border; an animal photographed in
    its habitat gives a darker, noisier one. Used to prefer in-habitat shots,
    which make a far better full-bleed card than a floating specimen.
    """
    img = Image.open(io.BytesIO(raw)).convert("L").resize((64, 64))
    px = img.load()
    n = 64
    ring = ([px[x, 0] for x in range(n)] + [px[x, n - 1] for x in range(n)]
            + [px[0, y] for y in range(n)] + [px[n - 1, y] for y in range(n)])
    mean = sum(ring) / len(ring)
    var = sum((v - mean) ** 2 for v in ring) / len(ring)
    return mean, var ** 0.5


def looks_studio(raw):
    mean, sd = studio_score(raw)
    return mean > 225 and sd < 22


def too_small(raw, minimum=MIN_PX):
    """Reject anything that would have to be upscaled into the card.

    Commons serves the original when it is smaller than the thumbnail width
    asked for, so a candidate can come back at 230px and still look like a
    successful fetch. Rendered at 1080 square that is visible mush.
    """
    w, h = Image.open(io.BytesIO(raw)).size
    return min(w, h) < minimum


def fetch_bytes(session, url):
    r = session.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def download(session, url, dest):
    """Fetch and normalise to a repo-friendly JPEG.

    Commons originals run to several MB each; at 197 species that is a
    repository nobody wants to clone. These are only ever used as a 1080px
    square backdrop, so downscale to SAVE_PX on the long edge and re-encode.
    """
    r = session.get(url, timeout=60)
    r.raise_for_status()

    img = Image.open(io.BytesIO(r.content))
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    elif img.mode == "L":
        img = img.convert("RGB")
    img.thumbnail((SAVE_PX, SAVE_PX), Image.LANCZOS)
    img.save(dest, "JPEG", quality=85, optimize=True, progressive=True)
    return dest.stat().st_size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("species", nargs="*", help="limit to these species")
    ap.add_argument("--force", action="store_true", help="re-fetch cached entries")
    ap.add_argument("--habitat", action="store_true",
                    help="prefer in-habitat shots over specimen-on-white ones")
    args = ap.parse_args()

    PHOTOS.mkdir(exist_ok=True)
    credits = json.loads(CREDITS.read_text("utf-8")) if CREDITS.exists() else {}
    pinned = json.loads(PINNED.read_text("utf-8")) if PINNED.exists() else {}

    wanted = sorted({p.animal for p in plan.load(PLAN_PATH)})
    if args.species:
        pick = {s.lower() for s in args.species}
        wanted = [s for s in wanted if s.lower() in pick]

    session = requests.Session()
    session.headers["User-Agent"] = UA

    unresolved, fetched, cached = [], 0, 0
    for i, sp in enumerate(wanted, 1):
        have = credits.get(sp)
        if not args.force and have and (PHOTOS / have["file"]).exists():
            cached += 1
            continue

        time.sleep(PAUSE)
        try:
            # A pinned species is a hand-picked choice: no automated heuristic
            # gets to overrule it. Editorial fitness is not something the
            # studio/size checks can see -- for Atlantic Bluefin Tuna they
            # happily returned a dead fish on a boat deck, which is worse for a
            # wildlife page than the studio shot it replaced.
            if sp in pinned:
                candidates = [(pinned[sp], None)]
            else:
                # The article's lead image first, then a Commons search.
                candidates = []
                fname, thumb = lead_image(session, sp)
                if fname:
                    candidates.append((fname, thumb))
                for name in commons_search(session, sp,
                                           limit=16 if args.habitat else 8):
                    if name != fname:
                        candidates.append((name, None))

            if not candidates:
                unresolved.append({"species": sp, "reason": "no candidate images"})
                print(f"[{i}/{len(wanted)}] {sp}: no candidate images")
                continue

            meta = thumb_url = None
            rejected = []
            fallback = None
            for name, url in candidates:
                m = commons_meta(session, name)
                if m is None:
                    continue
                if not is_free(m):
                    rejected.append(m["licence"] or "?")
                    continue
                url = url or thumb_for(session, name)
                if not url:
                    continue

                if sp in pinned or not args.habitat:
                    meta, thumb_url, fname = m, url, name
                    break

                # Habitat mode: keep looking past specimen-on-white shots,
                # but remember the first free one so we never end up worse off.
                raw = fetch_bytes(session, url)
                if too_small(raw):
                    w, h = Image.open(io.BytesIO(raw)).size
                    print(f"      skip {name[:48]} (only {w}x{h})")
                    continue
                if fallback is None:
                    fallback = (m, url, name, raw)
                if looks_studio(raw):
                    mean, sd = studio_score(raw)
                    print(f"      skip {name[:48]} (studio: mean {mean:.0f}, "
                          f"sd {sd:.0f})")
                    continue
                meta, thumb_url, fname = m, url, name
                break

            if meta is None and fallback is not None:
                print("      no in-habitat candidate found; keeping best free one")
                meta, thumb_url, fname, _ = fallback

            if meta is None:
                reason = (f"no freely-licensed candidate "
                          f"(rejected: {', '.join(rejected[:4]) or 'none readable'})")
                unresolved.append({"species": sp, "reason": reason})
                print(f"[{i}/{len(wanted)}] {sp}: {reason}")
                continue

            thumb = thumb_url
            dest = PHOTOS / f"{slug(sp)}.jpg"
            size = download(session, thumb, dest)
            credits[sp] = {
                "file": dest.name,
                "licence": meta["licence"],
                "author": meta["author"],
                "licence_url": meta["licence_url"],
                "source": meta["descriptionurl"],
                "commons_file": fname,
            }
            fetched += 1
            print(f"[{i}/{len(wanted)}] {sp}: {meta['licence']} ({size // 1024} KB)")
        except Exception as e:
            unresolved.append({"species": sp, "reason": f"{type(e).__name__}: {e}"})
            print(f"[{i}/{len(wanted)}] {sp}: FAILED {e}")

        CREDITS.write_text(json.dumps(credits, indent=2, ensure_ascii=False), "utf-8")

    CREDITS.write_text(json.dumps(credits, indent=2, ensure_ascii=False), "utf-8")
    UNRESOLVED.write_text(json.dumps(unresolved, indent=2, ensure_ascii=False), "utf-8")

    print(f"\ncached {cached}, fetched {fetched}, unresolved {len(unresolved)}")
    if unresolved:
        print("unresolved species:")
        for u in unresolved:
            print(f"  {u['species']}: {u['reason']}")
    return 1 if unresolved else 0


if __name__ == "__main__":
    sys.exit(main())
