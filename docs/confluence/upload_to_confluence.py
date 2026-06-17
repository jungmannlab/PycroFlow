#!/usr/bin/env python3
"""Upload the Confluence Storage Format pages in this folder to Confluence Cloud.

Creates the pages if they don't exist (matched by title within the space) or
updates them in place (bumping the version) if they do. Uses only the Python
standard library.

Auth: a Confluence Cloud API token (https://id.atlassian.com/manage/api-tokens)
plus your account email, sent as HTTP Basic auth.

Usage:
    export CONFLUENCE_BASE=https://your-site.atlassian.net/wiki
    export CONFLUENCE_EMAIL=you@example.com
    export CONFLUENCE_TOKEN=xxxxxxxxxxxxxxxx
    export CONFLUENCE_SPACE=ENG          # the space key
    # optional: nest the new pages under a parent
    # export CONFLUENCE_PARENT=123456789

    python docs/confluence/upload_to_confluence.py

To upload only one page, pass its file as an argument:
    python docs/confluence/upload_to_confluence.py pycroflow-overview.confluence.html
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# file -> page title
PAGES = {
    "pycroflow-overview.confluence.html": "PycroFlow — Overview",
    "running-an-experiment.confluence.html": "PycroFlow — Running an Experiment",
}

HERE = Path(__file__).resolve().parent


def _env(name):
    val = os.environ.get(name)
    if not val:
        sys.exit("Missing required environment variable: {}".format(name))
    return val.rstrip("/") if name == "CONFLUENCE_BASE" else val


def _request(method, url, token_header, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", token_header)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        sys.exit("{} {} -> HTTP {}: {}".format(method, url, exc.code, body))


def _find_page(base, auth, space, title):
    q = urllib.parse.urlencode({
        "title": title, "spaceKey": space, "expand": "version"})
    res = _request("GET", "{}/rest/api/content?{}".format(base, q), auth)
    results = res.get("results", [])
    return results[0] if results else None


def upload(base, auth, space, parent, fname, title):
    body = (HERE / fname).read_text()
    storage = {"value": body, "representation": "storage"}
    existing = _find_page(base, auth, space, title)
    if existing:
        page_id = existing["id"]
        version = existing["version"]["number"] + 1
        payload = {
            "id": page_id, "type": "page", "title": title,
            "space": {"key": space},
            "body": {"storage": storage},
            "version": {"number": version},
        }
        _request("PUT", "{}/rest/api/content/{}".format(base, page_id),
                 auth, payload)
        print("updated  '{}'  (v{}, id {})".format(title, version, page_id))
    else:
        payload = {
            "type": "page", "title": title,
            "space": {"key": space},
            "body": {"storage": storage},
        }
        if parent:
            payload["ancestors"] = [{"id": parent}]
        res = _request("POST", "{}/rest/api/content".format(base), auth, payload)
        print("created  '{}'  (id {})".format(title, res["id"]))


def main(argv):
    base = _env("CONFLUENCE_BASE")
    email = _env("CONFLUENCE_EMAIL")
    token = _env("CONFLUENCE_TOKEN")
    space = _env("CONFLUENCE_SPACE")
    parent = os.environ.get("CONFLUENCE_PARENT")

    auth = "Basic " + base64.b64encode(
        "{}:{}".format(email, token).encode()).decode()

    files = argv[1:] or list(PAGES)
    for fname in files:
        if fname not in PAGES:
            sys.exit("Unknown page file: {} (known: {})".format(
                fname, ", ".join(PAGES)))
        upload(base, auth, space, parent, fname, PAGES[fname])


if __name__ == "__main__":
    main(sys.argv)
