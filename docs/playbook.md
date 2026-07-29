# Reusing this pipeline for another Page

Everything here was learned building the Wild Nature USA run. Read it before
standing up the next Page — most of it is not discoverable from Meta's docs,
and two items cause silent, invisible failure.

---

## 1. Facebook and Instagram are opposites. Design around that first.

Same Graph API, same Page token, opposite capabilities. This asymmetry decides
the whole architecture, so settle it before writing anything.

| | Facebook Page | Instagram Business |
|---|---|---|
| Server-side scheduling | **Yes** — `published=false` + `scheduled_publish_time` | **None.** Publish call must happen at the slot |
| Image delivery | Raw bytes in the request | **Public URL only** — bytes rejected |
| If your runner is down | Posts still fire | Nothing publishes |
| Rate limit | generous | 50 posts / rolling 24h |

**Consequences that follow directly:**

- Hand Facebook work to Meta's scheduler as far ahead as you can (7 days here).
  Runner reliability then stops mattering for Facebook entirely.
- Every Instagram image must be **committed and pushed before its slot**.
  Rendering it locally at publish time serves a 404.
- A Facebook-only image never needs committing at all. Here that kept 177 MB
  of fact cards out of a repo Actions checks out hourly — they render on demand
  from a 47 MB photo set instead.

**Sharp edges:**

- Minimum scheduling lead is 10 minutes. Use ~20.
- A scheduled photo returns a **photo id**, not a post id. To comment on it you
  need `page_story_id`, which only exists once the post is live.
- **You cannot attach media to an already-scheduled text post.** Converting one
  means create-the-replacement-then-delete-the-original — in that order, so a
  failure leaves a removable duplicate rather than an unfillable slot.
- `instagram_manage_comments` is easy to omit and fails *silently* — every
  other call succeeds while answer comments quietly don't post.

---

## 2. Business Suite will show "No scheduled posts". That is normal.

API-created posts carry the app as `admin_creator`:

```json
"admin_creator": { "name": "Automation", "id": "2304615900310881" }
```

Business Suite's Content → Scheduled tab is built around posts composed by
people in Meta's own surfaces and **does not list these**. The queue can be
full and the tab will read "You haven't scheduled any posts yet" for the entire
run.

Do not debug this. Treat the Graph API as the source of truth:

```bash
python -m src.publish status
```

**But verify firing once per Page before you trust it.** Correct state is not
proof of behaviour. `tools/test_fire.py --live` schedules a real post 12
minutes out, waits, confirms publication, and deletes it. On Wild Nature USA
it fired at 13:17:40Z for a 13:17:39Z slot — one second late.

> Note: that test takes ~15 minutes end to end. Run it somewhere it will not be
> killed part-way, or it leaves the post up.

---

## 3. "expires: never" is not the field that matters

Meta runs **two independent clocks** on a token:

| Field | Meaning |
|---|---|
| `expires_at` | `0` = the token itself never expires |
| `data_access_expires_at` | ~90 days from human authorisation; when it passes **every call fails** |

A long-lived Page token happily reports "expires: never" while its data-access
clock counts down. Wild Nature USA's token: `expires_at: 0`, data access
lapsing 2026-10-26 — 97 days short of a plan running to 2027-01-31.

Check both, via `GET /debug_token?input_token=X&access_token=X`.

The fix is a **System User token**, issued to an app identity, which has no
data-access clock. Full click-path in [token-setup.md](token-setup.md). The
step people miss is **Add Assets** — without assigning the Page, the token
generates fine and then returns "object does not exist" on every call.

Confirmation is `data access: no expiry` in `publish status`. A System User
token reports `type: USER`; that is correct and not the same as a personal one.

---

## 4. Scheduled GitHub Actions do not run on schedule

Measured on this repo, `cron: "0 * * * *"` over 37 hours: **6 runs, not ~37**,
with gaps of **123, 91, 113, 254 and 197 minutes**.

**The trap** — a scheduler that only publishes what is due inside a trailing
window silently loses anything falling in a gap:

```python
lo = now - timedelta(minutes=90)          # WRONG
return [p for p in posts if lo <= p.publish_at <= now]
```

A slot that passed 254 minutes ago is never returned again. No error, no retry,
no state entry. It simply never happens. Every observed gap met or exceeded
that 90-minute window.

**The pattern that works:**

1. Make it a **lateness tolerance**, not a window around `now`, sized well
   above the worst expected gap (6h against 254m observed).
2. Keep it bounded — publishing yesterday's post today is worse than skipping.
3. Record anything past the bound as an **explicit skip with a reason**, so a
   miss is visible in state rather than vanishing.
4. Assert nothing falls between the buckets: `due ∪ overdue` covers every past
   item, and the intersection is empty.

Write the regression test against the **real observed gaps**
(`tools/test_catchup.py`), not invented ones. Replaying the five actual gaps
showed the old window lost the post in all five, which is what made it
undeniable.

Best of all, avoid the dependency: offload to server-side scheduling wherever
the platform offers it, and only publish-at-the-slot where it doesn't.

Also: **a dry run must never write state.** Gate every mutation behind `--live`,
including "record this as skipped" bookkeeping.

---

## 5. Sourcing photos at scale

Pattern that resolved 197/197 species with zero failures
(`tools/fetch_photos.py`).

**Resolution:** common name → Wikipedia article (`prop=pageimages`,
`redirects=1`) for the lead image; fall back to Commons file search
(`list=search`, `srnamespace=6`) when the article exposes none. Read licence and
attribution from the Commons file (`prop=imageinfo`, `iiprop=extmetadata`).

**Licence filter for a page that can carry ads and crops/overlays the image:**

- Require commercial use **and** derivatives. Reject NC and ND.
- Tokenise before matching, so a bare `nc`/`nd` can't hit inside another word.
- **Exclude GFDL.** It is free, but complying means reproducing the full licence
  text — impossible in a caption. Re-fetching the 4 GFDL hits got CC
  equivalents immediately.
- Accept CC0, CC BY, CC BY-SA, Public domain.

Put attribution **both** burned into the image and in the caption, so it
survives the platform's re-encoding and any reshare.

**Two automated gates worth having:**

- *Resolution floor.* Commons serves the **original** when it's smaller than the
  thumbnail width you requested, so a bad fetch looks exactly like a good one.
  One returned 230×159 and would have rendered as mush at 1080.
- *Flat-background detector.* Mean and standard deviation of the border ring on
  a 64×64 greyscale; `mean > 225 and sd < 22` catches specimen cut-outs on
  white. Pure PIL, no numpy.

**The part that matters most: heuristics cannot judge editorial fitness, and
used alone they make things worse.** Told to prefer non-studio shots, the
fetcher returned:

- a **dead tuna** bled out on a boat deck
- a juvenile catfish **dwarfed by a human hand**
- for "Flying Fish", a **Thorpe Park rollercoaster** — common-name collision

All three passed every automated check. So always keep a `photos/pinned.json`
mapping subject → exact Commons filename, used verbatim and bypassing every
heuristic, and **always eyeball a contact sheet** of the results. The detector
also over-flags legitimately pale habitats — snow foxes, electron micrographs —
which only inspection reveals.

---

## 6. Structure worth copying

- The **xlsx plan is the single source of truth**; run-time status lives in a
  separate `state/state.json` and is never written back into the plan.
- **`Post_ID` is the idempotency key.** Terminal states: `posted`, `scheduled`,
  `skipped`. Nothing is ever sent twice however often runs repeat or overlap.
- **Nothing publishes without an explicit `--live`.** A repo variable
  (`PUBLISH_ENABLED`) gates scheduled runs, so merging the workflow does not
  start posting.
- A **`--now` flag** simulates a moment for dry-run testing, and refuses to
  combine with `--live`.
- Commit only images a platform must fetch by URL.
- Only CI runs `--live` — see the multi-machine hazard in the [README](../README.md#working-from-more-than-one-machine).

---

## 7. Standing up a new Page: checklist

1. Copy this repo; replace `plan/`, `photos/`, `images/`.
2. Rename the three env keys (`<PAGE>_PAGE_ID`, `_PAGE_TOKEN`, `_IG_USER_ID`)
   in `src/meta.py`, the workflow, and `.env`.
3. Add them as **Actions secrets**. Repo must be **public** if Instagram is
   fetching images from `raw.githubusercontent.com`.
4. Generate a **System User token** with the six required scopes (§3).
5. `python tools/fetch_photos.py`, then eyeball a contact sheet.
6. `python -m src.render`.
7. `python -m src.publish status` — expect all counts full and
   `data access: no expiry`.
8. `python tools/test_catchup.py` — proves no slot is silently dropped.
9. **`python tools/test_fire.py --live`** — proves scheduling actually fires.
10. Only then set `PUBLISH_ENABLED=true`.
