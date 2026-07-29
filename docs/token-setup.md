# Getting a token that outlives the plan

## Why the current token is not enough

The token in use is a **Page token**. `python -m src.publish status` reports it
honestly:

```
token type: PAGE  expires in: never
data access expires in: 88 days
  NOTE: shorter than the plan. Re-authorise before it lapses, or use a
  System User token (no data-access clock).
```

"Expires: never" is true and beside the point. Meta runs *two* clocks on a
token and only one of them is the expiry:

| Clock | This token | What it does when it runs out |
|---|---|---|
| `expires_at` | never | nothing — the token stays valid |
| `data_access_expires_at` | **2026-10-26** | every call starts failing |

The second clock is the one that matters. It is a 90-day timer that starts when
a human authorises the app, and it applies regardless of how long-lived the
token itself is. When it lapses, Facebook scheduling and Instagram publishing
both stop.

The plan runs to **2027-01-31**. That leaves **97 days of content** — roughly
680 posts — on the far side of a token that has already stopped working.

A **System User** token is issued to an application identity rather than a
person, so it carries no data-access clock at all.

## Creating one

You have to do this yourself — it involves signing in to Business Manager,
which is not something to hand to an automation.

1. **Business Settings** → <https://business.facebook.com/settings>
   Confirm the selector at top-left is the business that owns the Wild Nature
   USA Page (the token reports business id `952323010483256`).

2. **Users → System Users** → **Add**
   - Name: `wildnatureusa-publisher`
   - Role: **Admin**

3. Select the new system user → **Add Assets**
   - **Pages** → *Wild Nature USA* → enable **Manage Page** (full control)
   - If Instagram is listed separately under **Instagram accounts**, add
     `wildnatureinusa` with full control as well.

   Asset assignment is the step people miss. Without it the token generates
   fine and then returns "object does not exist" on every call.

4. **Generate New Token**
   - App: the app currently in use — **Automation** (`2304615900310881`)
   - Token expiration: **Never**
   - Tick exactly these scopes:

     ```
     pages_show_list
     pages_read_engagement
     pages_manage_posts
     instagram_basic
     instagram_content_publish
     instagram_manage_comments
     ```

     `instagram_manage_comments` is the one the original workbook omits.
     Without it the quiz answer comments fail while everything else succeeds,
     which is an annoying way to find out.

5. Copy the token. It is shown **once**.

## Installing it

Replace the Actions secret — the workflow reads it from there, not from
`.env`:

**Repo → Settings → Secrets and variables → Actions → `WILDNATUREUSA_PAGE_TOKEN` → Update**

Then update your local `Automation/.env` to match, so local dry runs test the
same credential the pipeline uses.

## Verifying

```bash
python -m src.publish status
```

What you want to see:

```
token type: USER  expires in: never
data access: no expiry
scopes granted (6): instagram_basic, instagram_content_publish,
  instagram_manage_comments, pages_manage_posts, pages_read_engagement,
  pages_show_list
  all required scopes present
```

Two lines decide it:

- **`data access: no expiry`** — the 90-day clock is gone. If it still shows a
  day count, the token came from a user login rather than a system user, and
  it will fail in October exactly as before.
- **`all required scopes present`** — if a scope is missing here it will fail
  silently at publish time, not at generation time.

`type: USER` is expected and correct for a system user token; it is not the
same thing as a personal user token.

## If a scope refuses to stick

Usually `instagram_content_publish` or `instagram_manage_comments` dropping off
means the Instagram account is not attached to the Page as a Business or
Creator account. Check **Page → Settings → Linked accounts → Instagram**, then
regenerate. No amount of re-ticking the box fixes it from the token side.
