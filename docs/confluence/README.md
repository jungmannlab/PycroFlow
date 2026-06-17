# Confluence pages

Ready-to-upload Confluence pages, written in **Confluence Storage Format**
(the XHTML that Confluence stores pages in). They mirror the repository docs and
the lab SOPs.

| File | Suggested page title |
|------|----------------------|
| [`pycroflow-overview.confluence.html`](pycroflow-overview.confluence.html) | PycroFlow — Overview |
| [`running-an-experiment.confluence.html`](running-an-experiment.confluence.html) | PycroFlow — Running an Experiment |

Each file is a complete page **body** (no `<html>` wrapper) — exactly what goes
into the `body.storage.value` field of the Confluence REST API.

## Upload with the bundled script (recommended)

[`upload_to_confluence.py`](upload_to_confluence.py) creates or updates both
pages (stdlib only, no dependencies):

```bash
export CONFLUENCE_BASE=https://your-site.atlassian.net/wiki
export CONFLUENCE_EMAIL=you@example.com
export CONFLUENCE_TOKEN=<api-token>      # id.atlassian.com/manage/api-tokens
export CONFLUENCE_SPACE=ENG              # space key
# export CONFLUENCE_PARENT=123456789     # optional parent page id

python docs/confluence/upload_to_confluence.py            # both pages
python docs/confluence/upload_to_confluence.py pycroflow-overview.confluence.html  # one
```

Re-running updates the existing pages (matched by title) and bumps their version.

## Upload via the REST API (manual)

Replace the placeholders (`<...>`) and run from this directory. The space key
and a parent page ID are optional; include `ancestors` to nest the page.

```bash
curl -u "<you@example.com>:<API_TOKEN>" \
  -X POST "https://<your-site>.atlassian.net/wiki/rest/api/content" \
  -H "Content-Type: application/json" \
  -d "$(python - <<'PY'
import json
body = open("pycroflow-overview.confluence.html").read()
print(json.dumps({
    "type": "page",
    "title": "PycroFlow — Overview",
    "space": {"key": "<SPACEKEY>"},
    # "ancestors": [{"id": "<PARENT_PAGE_ID>"}],
    "body": {"storage": {"value": body, "representation": "storage"}},
}))
PY
)"
```

Repeat with `running-an-experiment.confluence.html` and title
`PycroFlow — Running an Experiment`.

To **update** an existing page, use `PUT .../content/<pageId>` with an
incremented `version.number`.

## Other upload routes

- **Confluence Cloud editor:** create a blank page, then use the page
  `•••` → *Advanced* / *View storage format* tooling (or a marketplace
  "import HTML/storage" app) to paste the storage XHTML. The plain editor's
  *Insert → Markup* accepts Markdown/wiki markup, not storage format, so the
  API route above is the most reliable.
- **Confluence Server/Data Center:** *Insert → Markup → Insert* supports
  pasting storage format directly.

## Keeping these in sync

These pages are derived from `README.md`, `ARCHITECTURE.md`, `docs/`, the CLI
(`PycroFlow/frontend_cli.py`), and `example_experiment/start_experiment_*.py`.
If those change, regenerate or hand-edit the pages here and re-upload.
