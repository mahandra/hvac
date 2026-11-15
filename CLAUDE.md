# CLAUDE.md

Django app for AC maintenance visits at rental properties. Spec: `SPEC.md`; user docs: `README.md`.

## Commands

- `make test [ARGS=...]` — pytest against a throwaway `postgres:17-alpine` on 127.0.0.1:55432. Run after every change.
- `make rebuild` — rebuild and restart the web container (app at http://127.0.0.1:8001).
- Python deps: `uv add` / `uv run` only (no pip, no requirements.txt).
- Local run without Docker: see "Development" in README (`POSTGRES_PORT=55432 DEBUG=1 uv run ...`).

## Private data — never commit

`data/import/` (real spreadsheet with tenant names, real photos), `data/media/`, `data/postgres/`,
`backups/`, `.env`. They are gitignored; keep it that way. Do not use real addresses/tenants in tests.

## Non-obvious decisions (don't "simplify" these away)

- **Auth**: `LoginRequiredMiddleware` is on; share views opt out with `@login_not_required` and send
  `X-Robots-Tag: noindex`. Admin is superuser-only (`admin.site.has_permission` override).
- **Media**: never served as static. `/media/...` goes through the login-protected `media_file` view
  (`safe_join`); share pages use token-scoped `/share/<token>/photos/<pk>/<kind>/`.
- **EXIF orientation**: Dropzone resize copies the original EXIF (incl. Orientation) onto already-rotated
  pixels, so the client sends `client_oriented=1` and the server skips `exif_transpose` and resets
  Orientation=1 (`tracker/images.py`, `static/js/upload.js`).
- **Duplicates** (sha256): same visit → skipped silently; another property on upload → accepted with a
  warning; on import a hash belonging to several properties → no copy imported, all reported.
- **Admin "Last visit" sort**: `NullsLastOrderBy` because Django's `OrderBy.reverse_ordering` flips
  `nulls_last`; `PropertyChangeList` shows the default sort indicator. Admin default = earliest visit on top;
  home page default = most recent on top. Never-visited is always last.
- **Home table sort** is done in Python (`SORT_COLUMNS` in `tracker/views.py`); sort state rides in hidden
  inputs with `form="property-search"` so HTMX search keeps it.
- **MCP server** (`tracker/mcp_server.py`, `manage.py mcp_server`): stdio only, ORM access, no deletes, no
  photo upload. Tools are plain sync functions (MCPServer runs them in worker threads) wrapped by `_db`,
  which closes the DB connection after each call. Never print to stdout there — it is the protocol channel.
  Uses `mcp` 2.x (`mcp.server.mcpserver.MCPServer`, not the 1.x `FastMCP`).
- `navigator.clipboard` doesn't work over plain HTTP — keep the `execCommand('copy')` fallback.

## Conventions

- Front-end libs are vendored in `static/vendor/` (no CDN); strip `sourceMappingURL` comments or
  WhiteNoise's manifest build fails.
- All styling lives in `templates/base.html` + `static/css/app.css`; no inline styles.
- Code, comments, commits and README in English.
