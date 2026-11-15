# HVAC Maintenance Photo Tracker

A small Django app for tracking air-conditioner maintenance visits at rental properties: per-address visit
history with checklist, filter size, notes and photos, plus a PDF report and a read-only share link.

Stack: Python 3.13, Django 5.2 LTS, PostgreSQL 17, HTMX, Dropzone.js, Bootstrap 5 (all vendored in
`static/vendor/`, no CDN), Gunicorn + WhiteNoise, Pillow (+ pillow-heif), WeasyPrint, openpyxl.

## First start

```bash
cp .env.example .env          # then edit: SECRET_KEY, POSTGRES_PASSWORD, ALLOWED_HOSTS, ...
id -u; id -g                  # put these into APP_UID / APP_GID in .env if they are not 1000
cp "Property AC Maintenance Oct2026.xlsx" data/import/properties.xlsx   # optional, see below
docker compose up -d
```

The app is then available at `http://<host>:8001/`. Everything also works without a `.env` file (built-in
defaults), but set your own `SECRET_KEY` and database password before real use.

On start the `web` container waits for Postgres, runs `migrate` and `collectstatic`, and — if the properties
table is empty and `data/import/properties.xlsx` exists — imports the spreadsheet automatically. Then it
starts Gunicorn as the non-root user `app`.

Persistent data lives in bind-mounted host directories and survives `docker compose down`:

| Host path        | Contents                                   |
|------------------|--------------------------------------------|
| `data/postgres`  | PostgreSQL data                            |
| `data/media`     | uploaded photos and thumbnails             |
| `data/import`    | input for the import commands (read-only)  |

`data/media` must be writable by `APP_UID` (the container prints a clear error if it is not).

### Configuration (`.env`)

| Variable | Meaning |
|---|---|
| `SECRET_KEY` | Django secret key |
| `DEBUG` | `0` in production |
| `ALLOWED_HOSTS` | comma-separated host names/IPs used to reach the app (`*` = any) |
| `CSRF_TRUSTED_ORIGINS` | e.g. `http://192.168.1.10:8001` |
| `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | database credentials |
| `MAINTENANCE_INTERVAL_DAYS` | a property is "Due" without a visit in this many days (default 180) |
| `TZ` | time zone, `America/New_York` |
| `APP_UID`, `APP_GID` | uid/gid the web container runs as |

The site is plain HTTP, so secure-cookie flags are off. The session lasts 30 days.

## Make shortcuts

Common tasks are wrapped in the `Makefile` (`make` alone lists them):

| Command | Does |
|---|---|
| `make up` / `make down` | start / stop the stack (data in `./data` is kept) |
| `make rebuild`, `make restart` | rebuild + restart the web image / restart the web container |
| `make ps`, `make logs` | container status / follow web logs |
| `make superuser` | create a superuser |
| `make mcp` | run the MCP server over stdio (see [MCP server](#mcp-server)) |
| `make shell`, `make dbshell` | Django shell / psql |
| `make migrate` | apply migrations |
| `make import-properties [XLSX=...]` | spreadsheet import (default `/app/data/import/properties.xlsx`) |
| `make import-photos [PHOTOS=...]` | photo import (default `/app/data/import/Photo`) |
| `make backup` | database dump + `data/media` archive into `./backups/` |
| `make restore DB=... [MEDIA=...]` | restore a backup (asks for confirmation) |
| `make test [ARGS=...]` | run the tests against a throwaway Postgres container |
| `make update` | `git pull`, rebuild, restart |

The sections below show the underlying commands.

## Users

There is no self-registration. Create the first (super)user:

```bash
docker compose exec web python manage.py createsuperuser
```

Further accounts are created in Django admin (`/admin/`, superusers only). Properties are also added and
edited only in admin.

## Importing data

Put input files under `data/import/` on the host; inside the container that is `/app/data/import/`.
Both commands are idempotent.

**Properties** from the spreadsheet (finds the header row whose first cell is `Property`; a `Townhouse:` or
`Condo:` prefix sets the type; a done date creates a visit):

```bash
docker compose exec web python manage.py import_properties /app/data/import/properties.xlsx
```

**Photos** from the downloaded Google Drive `Photo` folder (one subfolder per address):

```bash
cp -r ~/Downloads/Photo data/import/Photo
docker compose exec web python manage.py import_photos /app/data/import/Photo
```

Folders are matched to properties by house number + first word of the street name (case-insensitive, in any
order in the folder name, with tolerance for small typos). Unmatched folders are listed and skipped. Photos
are grouped into visits by the date taken (EXIF, else the `IMG_YYYYMMDD_HHMMSS` file name, else file
modification time). Visits created this way have both checklist boxes unticked and the note
"Imported from photo folder." — tick them in the app if the work was done. Files already imported for the
same property are skipped; a file whose content already exists under a *different* property is not imported
and is listed as a cross-property duplicate in the summary.

## Backup

```bash
mkdir -p backups
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > backups/hvac-$(date +%F).dump
tar czf backups/media-$(date +%F).tar.gz -C data media
```

Restore into a fresh install:

```bash
docker compose up -d db
docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --clean --if-exists' < backups/hvac-YYYY-MM-DD.dump
tar xzf backups/media-YYYY-MM-DD.tar.gz -C data
docker compose up -d
```

## Update

```bash
git pull                      # or copy the new source over
docker compose build web
docker compose up -d          # migrations and collectstatic run automatically on start
```

Back up first (see above) before updates that change the database.

## Using it

- **Home**: table of active properties (address, type, tenant, last visit, photos of the last visit, Paid —
  "Unpaid"/"N unpaid" if any visit of the property is not paid —, Due/OK status). Click a column header to sort, click again to reverse; by default visited properties are on top
  (most recent visit first) and never-visited ones at the bottom. Live search by address keeps the sort.
  On phones, type and tenant are shown under the address.
- **Property page**: New visit, Export PDF, Share link, and the visit history with thumbnails.
- **Visit page**: date, checklist, filter size (prefilled from the previous visit), note, Paid checkbox
  (shown as a Paid/Unpaid badge in the visit history; not shown on share pages or in PDFs); photos upload
  immediately via the drop box (desktop) or the **Take photo** / **Choose from gallery** buttons (phone) and
  appear in the grid without a reload. Images are resized in the browser to 2000 px wide before upload.
  Choosing "New visit" for a date that already has a visit opens the existing one.
- **Lightbox**: tap a thumbnail; prev/next (or swipe / arrow keys) and delete.
- **PDF**: `/properties/<id>/pdf/`, add `?visit=<visit id>` for a single visit.
- **Share link**: `/share/<token>/` read-only page (with PDF download) without login; "Regenerate" replaces
  the token, "Disable link" removes it, old tokens return 404. Share pages send `X-Robots-Tag: noindex`.

Photos are never served as public static files: `/media/...` requires login, and share pages use
token-scoped photo URLs that only work for that property's photos.

## MCP server

The app includes an [MCP](https://modelcontextprotocol.io) server so an AI assistant (Claude Code, Claude
Desktop, ...) can read and update the data. It runs over stdio inside the `web` container
(`python manage.py mcp_server`), uses the database directly and opens no network port; anyone who can run
`docker compose exec` on the server can use it, so no separate login is needed.

| Tool | Does |
|---|---|
| `list_properties` | active properties with last visit, unpaid visit count and due status (filters: search, due, unpaid) |
| `get_property` | one property (by id or part of the address) with its full visit history |
| `get_visit` | one visit with checklist, note, paid flag and photo list |
| `list_unpaid_visits` | all visits not marked as paid |
| `get_photo` | a photo as an image (thumbnail, or 900 px with `full_size`) |
| `create_visit` | record a visit (date defaults to today, filter size to the previous visit's) |
| `update_visit` | change only the given fields of a visit |
| `set_visit_paid` | mark a visit paid / unpaid |

Nothing can be deleted and photos cannot be uploaded through MCP; use the web app for that.

Register it in Claude Code (on the server, stack running):

```bash
claude mcp add hvac -- docker compose -f /path/to/hvac/docker-compose.yml exec -T web python manage.py mcp_server
```

From another machine, wrap the same command in SSH:

```bash
claude mcp add hvac -- ssh user@server docker compose -f /path/to/hvac/docker-compose.yml exec -T web python manage.py mcp_server
```

For Claude Desktop, put the same command and arguments into `claude_desktop_config.json` under
`mcpServers`. Data such as tenant names is sent to the AI model when the assistant reads it.

## Development

```bash
uv sync
docker run -d --name hvac-testdb -e POSTGRES_USER=hvac -e POSTGRES_PASSWORD=hvac -e POSTGRES_DB=hvac \
  -p 127.0.0.1:55432:5432 postgres:17-alpine
POSTGRES_PORT=55432 DEBUG=1 uv run python manage.py migrate
POSTGRES_PORT=55432 DEBUG=1 uv run python manage.py runserver
POSTGRES_PORT=55432 uv run pytest
```

The look is plain Bootstrap: all layout is in `templates/base.html` (blocks) and `static/css/app.css`, with
no inline styles, so a new design can be dropped in without touching views.
