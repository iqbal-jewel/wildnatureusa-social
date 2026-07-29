# WildNatureUSA social automation

Publishes the 6-month content plan to a Facebook Page and an Instagram
Business account: 1,288 posts across 184 days, Aug 1 2026 – Jan 31 2027.

| Platform  | Daily cadence                                      |
|-----------|----------------------------------------------------|
| Facebook  | 4 photo facts (8am, 12pm, 4pm, 8pm ET) + 1 quiz image (6pm ET) |
| Instagram | 2 quiz images (11am, 7pm ET)                       |

Every post carries an image. Every quiz post gets its answer as the first
comment 60 minutes later.

## How it works

**Facebook** accepts image bytes directly and supports native scheduling, so
posts are handed to Meta's scheduler up to a week ahead. They fire even if
this runner is down.

**Instagram** has neither: image posts need a publicly fetchable URL, and the
publish call has to happen at the moment the post goes live. The quiz PNGs are
committed to this repo and served from `raw.githubusercontent.com`, and the
hourly workflow makes the call at the right slot.

### Two kinds of image

*Quiz cards* are flat-colour PNGs holding the clue text. Instagram fetches them
over HTTP, so they are committed.

*Fact cards* are a species photograph under a scrim with the fact text over it.
They are Facebook-only — the bytes go up in the request — so they are
**rendered on demand at scheduling time and never committed**. Storing all 736
would add ~177 MB to a repo that Actions checks out every hour; the 197 source
photos in `photos/` are 47 MB and regenerate any card in about a second.

Those photos come from Wikimedia Commons via `tools/fetch_photos.py`, which
accepts only licences permitting commercial use *and* derivative works — the
card crops and draws over the image on a Page that can carry ads, so
NonCommercial and NoDerivs are both out. Attribution is burned into the card
and repeated in the caption, so a reshare keeps it either way.

### Timing

Times in the plan are US Eastern and the run crosses the Nov 1 2026 DST
boundary, so the code uses a real `zoneinfo` timezone, never a fixed offset.

The workflow runs hourly and each run is a no-op unless something is due.
GitHub throttles scheduled runs hard — gaps of 91 to 254 minutes were measured
on this repo against an hourly cron — so Instagram publishing tolerates a post
being **up to 6 hours late** (`--max-late`, default 360) rather than needing a
run inside a narrow window. Anything past the tolerance is recorded in state as
`skipped` with the reason, so a missed slot is visible instead of vanishing.

`Post_ID` is the idempotency key. A post recorded in `state/state.json` is
never sent twice, no matter how often a run repeats.

## Layout

```
plan/    wildnatureusa_6month_content_plan.xlsx   original, read-only
         content_plan_fixed.xlsx                  what actually publishes
tools/   grammar.py, rewrite_plan.py, validate_plan.py
         fetch_photos.py   sources the species photos from Wikimedia
         test_catchup.py   proves a missed slot is not silently dropped
src/     plan.py, render.py, meta.py, state.py, publish.py
photos/  197 species photographs + credits.json (licence + attribution)
images/  552 quiz PNGs (committed -- Instagram fetches these)
         fact cards render here on demand and are gitignored
fonts/   Poppins-Bold.ttf (OFL) so local and CI renders match
docs/    token-setup.md  how to get a token that outlives the plan
state/   state.json  run-time status, committed after each run
```

## Setup

**1. Push this to a public GitHub repo.** Public matters for two reasons:
Actions minutes are unlimited, and Instagram must be able to fetch the images
over `raw.githubusercontent.com`. Nothing secret lives in the repo — tokens
are Actions secrets and `.env` is gitignored.

**2. Add the Actions secrets** (Settings → Secrets and variables → Actions):

- `WILDNATUREUSA_PAGE_ID`
- `WILDNATUREUSA_PAGE_TOKEN`
- `WILDNATUREUSA_IG_USER_ID`

These already exist in `Automation/.env` from the reels pipeline.

**3. Use a System User token.** A Page token reports "expires: never" and still
stops working: Meta runs a separate 90-day *data access* clock on it. The token
currently in use lapses **2026-10-26**, which is 97 days before the plan ends.
A System User token has no such clock.

Full click-path, required scopes, and how to verify: **[docs/token-setup.md](docs/token-setup.md)**.

`python -m src.publish status` tells you where you stand — look for
`data access: no expiry`.

**4. Fetch the species photos** (once, ~47 MB):

```bash
python tools/fetch_photos.py
```

Fact cards render from these. `status` reports coverage as
`fact-card photos: 197/197 species covered`.

**5. Turn it on** by setting the repo variable `PUBLISH_ENABLED` to `true`
(Settings → Secrets and variables → Actions → Variables). Until then the
hourly workflow runs as a dry run, so merging it does not start posting.

## Commands

Nothing publishes without `--live`.

```bash
python -m src.publish status
```

```bash
python -m src.publish schedule-fb --days 7
```

```bash
python -m src.publish run --live
```

Simulate a moment to see what would go out (dry run only):

```bash
python -m src.publish run --now 2026-08-01T11:05
```

Re-render images after editing the plan (skips what already exists; add
`--force` to rebuild):

```bash
python -m src.render
```

Check that a missed slot is still caught up rather than dropped:

```bash
python tools/test_catchup.py
```

## Editing the content

`plan/content_plan_fixed.xlsx` is what publishes. After editing it, re-render
the affected images and re-run the validator:

```bash
python tools/validate_plan.py plan/content_plan_fixed.xlsx
```

It exits non-zero on any grammar defect, checking for stacked copulas
("is lays"), subjects with no verb, a/an disagreement, doubled articles,
naive plurals, and unfilled template slots.

`tools/rewrite_plan.py` regenerates the fixed workbook from the original. It
repaired 261 defects in the source, including 180-odd captions where a raw
predicate had been glued onto a subject without a linking verb, and widened
the fact-post phrasing from 10 rotating openers to 56 templates.

## Notes

- Instagram allows 50 posts per rolling 24h. This plan uses 2.
- Facebook rejects a scheduled time less than 10 minutes out; the scheduler
  keeps a 20-minute lead.
- A scheduled Facebook photo returns a photo id. Comments need the post id,
  which the publisher resolves via `page_story_id` once the post is live.
