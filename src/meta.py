"""Meta Graph API calls for Facebook Page and Instagram Business publishing.

Facebook accepts image bytes directly and supports native scheduling.
Instagram does neither: image posts need a publicly fetchable URL, and the
call has to happen at the moment the post should go live.
"""
import os
import time

import requests

GRAPH = "https://graph.facebook.com/v25.0"
TIMEOUT = 60


class MetaError(RuntimeError):
    pass


def _check(resp, what):
    try:
        data = resp.json()
    except ValueError:
        raise MetaError(f"{what}: HTTP {resp.status_code} {resp.text[:300]}")
    if isinstance(data, dict) and "error" in data:
        raise MetaError(f"{what}: {data['error']}")
    if not resp.ok:
        raise MetaError(f"{what}: HTTP {resp.status_code} {data}")
    return data


# --- Facebook -------------------------------------------------------------
def fb_text(page_id, token, message, scheduled_at=None):
    """Publish (or schedule) a text post. Returns the post id."""
    params = {"message": message, "access_token": token}
    if scheduled_at:
        params["published"] = "false"
        params["scheduled_publish_time"] = int(scheduled_at.timestamp())
    r = requests.post(f"{GRAPH}/{page_id}/feed", data=params, timeout=TIMEOUT)
    return _check(r, "fb_text")["id"]


def fb_photo(page_id, token, image_path, caption, scheduled_at=None):
    """Publish (or schedule) a photo post. Returns the post id.

    Scheduled photos come back as a photo id; the readable post id lives on
    the photo's page_story_id once it exists.
    """
    params = {"caption": caption, "access_token": token}
    if scheduled_at:
        params["published"] = "false"
        params["scheduled_publish_time"] = int(scheduled_at.timestamp())
    with open(image_path, "rb") as f:
        r = requests.post(f"{GRAPH}/{page_id}/photos", data=params,
                          files={"source": f}, timeout=TIMEOUT)
    data = _check(r, "fb_photo")
    return data.get("post_id") or data["id"]


def fb_story_id(photo_id, token):
    """Resolve a photo id to the post id you can comment on."""
    r = requests.get(f"{GRAPH}/{photo_id}",
                     params={"fields": "page_story_id", "access_token": token},
                     timeout=TIMEOUT)
    return _check(r, "fb_story_id").get("page_story_id")


def fb_comment(object_id, token, message):
    r = requests.post(f"{GRAPH}/{object_id}/comments",
                      data={"message": message, "access_token": token},
                      timeout=TIMEOUT)
    return _check(r, "fb_comment")["id"]


# --- Instagram ------------------------------------------------------------
def ig_image(ig_user_id, token, image_url, caption, poll_seconds=90):
    """Publish an image post. image_url must be publicly reachable by Meta."""
    r = requests.post(f"{GRAPH}/{ig_user_id}/media",
                      data={"image_url": image_url, "caption": caption,
                            "access_token": token}, timeout=TIMEOUT)
    container = _check(r, "ig_container")["id"]

    deadline = time.time() + poll_seconds
    while time.time() < deadline:
        s = requests.get(f"{GRAPH}/{container}",
                         params={"fields": "status_code", "access_token": token},
                         timeout=TIMEOUT)
        code = _check(s, "ig_status").get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise MetaError(f"ig processing failed for container {container}")
        time.sleep(5)

    p = requests.post(f"{GRAPH}/{ig_user_id}/media_publish",
                      data={"creation_id": container, "access_token": token},
                      timeout=TIMEOUT)
    return _check(p, "ig_publish")["id"]


def ig_comment(media_id, token, message):
    """Needs the instagram_manage_comments permission."""
    r = requests.post(f"{GRAPH}/{media_id}/comments",
                      data={"message": message, "access_token": token},
                      timeout=TIMEOUT)
    return _check(r, "ig_comment")["id"]


# --- credentials ----------------------------------------------------------
def credentials():
    """Read credentials from the environment, failing loudly if absent."""
    need = ["WILDNATUREUSA_PAGE_ID", "WILDNATUREUSA_PAGE_TOKEN",
            "WILDNATUREUSA_IG_USER_ID"]
    missing = [k for k in need if not os.environ.get(k)]
    if missing:
        raise MetaError(f"missing environment variables: {', '.join(missing)}")
    return {
        "page_id": os.environ["WILDNATUREUSA_PAGE_ID"],
        "token": os.environ["WILDNATUREUSA_PAGE_TOKEN"],
        "ig_user_id": os.environ["WILDNATUREUSA_IG_USER_ID"],
    }


# What the publisher actually needs on the token.
REQUIRED_SCOPES = [
    "pages_show_list",
    "pages_read_engagement",
    "pages_manage_posts",
    "instagram_basic",
    "instagram_content_publish",
    "instagram_manage_comments",
]


def debug_token(token):
    """Inspect a token: type, expiry, and the scopes actually granted."""
    r = requests.get(f"{GRAPH}/debug_token",
                     params={"input_token": token, "access_token": token},
                     timeout=TIMEOUT)
    return _check(r, "debug_token").get("data", {})


def token_expiry(token):
    """Days until the token expires, or None if it never does."""
    expires = debug_token(token).get("expires_at")
    if not expires:
        return None
    return max(0, int((expires - time.time()) // 86400))


def scope_report(token):
    """Return (granted, missing) for the scopes the publisher needs."""
    granted = set(debug_token(token).get("scopes", []))
    missing = [s for s in REQUIRED_SCOPES if s not in granted]
    return sorted(granted), missing
