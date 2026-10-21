"""Turn a built plan into a real Google Slides deck.

Two paths, same plan:

* **Integration path** (agents with a Google integration, e.g. WAM): the agent
  uploads every file from ``media.json`` to Drive itself, makes it readable by
  link, writes ``media_map.json`` (``{"m001": "<driveFileId>", …}``) and calls
  ``finalize``. That writes ready ``batch_NN.json`` files: the agent creates an
  empty presentation and sends each batch to ``presentations.batchUpdate``,
  then deletes the presentation's default first slide.
* **Direct path** (``direct``): the same steps done here with the user's own
  OAuth token (authorized-user JSON with refresh_token, client_id,
  client_secret, token_uri).
"""
import json
import os
import time

from .media import PLACEHOLDER

BATCH = 100          # requests per batchUpdate call — small enough for any integration


def drive_image_url(file_id):
    # =s0 → original size (no 1600/2048 cap)
    return f"https://lh3.googleusercontent.com/d/{file_id}=s0"


def _load(plan_dir, name):
    with open(os.path.join(plan_dir, name), encoding="utf-8") as f:
        return json.load(f)


def substitute(requests, media_map):
    missing = set()

    def fix(url):
        if url.startswith(PLACEHOLDER):
            key = url[len(PLACEHOLDER):]
            if key not in media_map:
                missing.add(key)
                return url
            return drive_image_url(media_map[key])
        return url

    out = []
    for r in requests:
        if "createImage" in r:
            r = json.loads(json.dumps(r))
            r["createImage"]["url"] = fix(r["createImage"]["url"])
        out.append(r)
    if missing:
        raise SystemExit(f"media_map has no Drive id for: {', '.join(sorted(missing))}")
    return out


def finalize(plan_dir, media_map_path=None):
    """Write batch_NN.json with real image URLs; return their paths."""
    if media_map_path:
        with open(media_map_path, encoding="utf-8") as f:
            media_map = json.load(f)
    else:
        media_map = _load(plan_dir, "media_map.json")
    reqs = substitute(_load(plan_dir, "requests.json"), media_map)
    paths = []
    for n, i in enumerate(range(0, len(reqs), BATCH), 1):
        p = os.path.join(plan_dir, f"batch_{n:02d}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"requests": reqs[i:i + BATCH]}, f, ensure_ascii=False)
        paths.append(p)
    return paths


def _creds(token_path):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    t = json.load(open(token_path, encoding="utf-8"))
    c = Credentials(token=t.get("access_token") or t.get("token"), refresh_token=t["refresh_token"],
                    token_uri=t.get("token_uri", "https://oauth2.googleapis.com/token"),
                    client_id=t["client_id"], client_secret=t["client_secret"])
    if not c.valid:
        c.refresh(Request())
    return c


def direct(plan_dir, token_path, share=()):
    """Upload media, create the deck, apply batches, share. Returns the URL."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    creds = _creds(token_path)
    D = build("drive", "v3", credentials=creds, cache_discovery=False)
    S = build("slides", "v1", credentials=creds, cache_discovery=False)
    plan = _load(plan_dir, "plan.json")

    folder = D.files().create(body={"name": f"RD deck media — {plan['title']}",
                                    "mimeType": "application/vnd.google-apps.folder"},
                              fields="id").execute()["id"]
    media_map = {}
    for it in _load(plan_dir, "media.json"):
        path = os.path.join(plan_dir, it["file"])
        mime = "image/png" if path.endswith(".png") else "image/jpeg"
        fid = D.files().create(body={"name": os.path.basename(path), "parents": [folder]},
                               media_body=MediaFileUpload(path, mimetype=mime, resumable=False),
                               fields="id").execute()["id"]
        D.permissions().create(fileId=fid, body={"type": "anyone", "role": "reader"}).execute()
        media_map[it["key"]] = fid
    with open(os.path.join(plan_dir, "media_map.json"), "w", encoding="utf-8") as f:
        json.dump(media_map, f, indent=1)

    pres = S.presentations().create(body={"title": plan["title"]}).execute()
    pid = pres["presentationId"]
    default_slide = pres["slides"][0]["objectId"]
    for p in finalize(plan_dir):
        S.presentations().batchUpdate(presentationId=pid, body=_load(plan_dir, os.path.basename(p))).execute()
        time.sleep(0.3)
    S.presentations().batchUpdate(presentationId=pid, body={"requests": [
        {"deleteObject": {"objectId": default_slide}}]}).execute()
    for email in list(share) + list(plan.get("share", [])):
        D.permissions().create(fileId=pid, sendNotificationEmail=False,
                               body={"type": "user", "role": "writer", "emailAddress": email}).execute()
    return f"https://docs.google.com/presentation/d/{pid}/edit"
