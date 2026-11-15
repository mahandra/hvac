# HVAC Maintenance Photo Tracker — build specification

Build a small Django web application for tracking air-conditioner maintenance visits at rental properties. For each property (address), a technician cleans the outdoor unit, replaces the indoor filter, takes photos of the work, and uploads them. The app keeps a history of visits per address, with photos, and produces a PDF report and a read-only share link per address.

Two people use it (a technician and the owner), mostly from an Android phone on site and sometimes from a desktop. It runs on a home server in Docker.

## 1. Stack

- Python 3.13, Django (current LTS), PostgreSQL 17
- Server-rendered Django templates + HTMX; Dropzone.js for uploads; Bootstrap 5 for styling
- No SPA, no Node build step: load Bootstrap, HTMX and Dropzone as vendored static files (not from a CDN, the server must work without internet access)
- Gunicorn + WhiteNoise for static files
- Pillow for image handling, WeasyPrint for PDF, openpyxl for the xlsx import
- Docker Compose for everything

A visual design will be supplied later. Until then use plain Bootstrap. Keep all layout in one `base.html` with blocks and avoid inline styles, so the look can be replaced without touching views.

## 2. Users and access

- Login page (username + password); every page except the share link requires login.
- No self-registration. Accounts are created only by the admin in Django admin.
- All logged-in users have the same rights (create/edit visits, upload/delete photos, export PDF, manage share links).
- Django admin is available to superusers only.
- Logout button in the navbar. Session lifetime 30 days so the phone stays logged in.

## 3. Data model

**Property**
- `address` (text, unique) — full address as one line
- `property_type` — choice: House, Townhouse, Condo
- `tenant_name` (text, blank) — separate field; may hold values like "Vacant"
- `note` (text, blank)
- `is_active` (bool, default true)
- `share_token` (unguessable, nullable) — see section 7

**Visit**
- `property` (FK)
- `visit_date` (date, default today)
- `outdoor_unit_cleaned` (bool)
- `filter_replaced` (bool)
- `filter_size` (short text, blank, e.g. "20x25x1") — on a new visit, prefill from the property's most recent visit
- `note` (text, blank)
- `created_by`, `created_at`, `updated_at`
- Unique on (`property`, `visit_date`); ordering newest first

**Photo**
- `visit` (FK)
- `image` — stored under `media/properties/<property_id>/<visit_date>/<uuid>.jpg`
- `thumbnail` — generated on upload, max 400 px
- `original_filename`
- `taken_at` (datetime, nullable) — from EXIF `DateTimeOriginal`, else parsed from an `IMG_YYYYMMDD_HHMMSS` filename, else null
- `sha256` — content hash, used for duplicate detection
- `uploaded_by`, `uploaded_at`

Photos are not categorised; they simply belong to a visit. Register all three models in Django admin (properties are added and edited only there).

## 4. Screens

All screens must work well on a phone (360 px wide and up) and on desktop. Large tap targets, no horizontal scrolling, no hover-only controls.

1. **Login.**
2. **Property list** (home page). One row/card per active property: address, type, tenant, last visit date, photo count of the last visit. Mark a property as "Due" when it has no visit in the last `MAINTENANCE_INTERVAL_DAYS` (setting, default 180). Search box filtering by address (HTMX). Sort: due first, then by address.
3. **Property detail.** Address, type, tenant, note. Buttons: "New visit", "Export PDF", "Share link". Below: visit history, newest first; each visit shows date, checklist state, filter size, note, and a thumbnail grid.
4. **Visit form / visit page.** Date, two checklist checkboxes, filter size, note, then the upload area and the photo grid of this visit. Saving the form and uploading photos are independent: photos upload immediately and appear in the grid without a page reload (HTMX swap). "New visit" for a date that already has a visit opens the existing one.
5. **Photo viewer.** Tapping a thumbnail opens the full image in a lightbox with prev/next and a delete button (with confirmation).

## 5. Photo upload

- **Desktop:** a Dropzone drag-and-drop box; clicking it opens the file picker. Multiple files, per-file progress, per-file error messages.
- **Phone:** the same box plus two explicit buttons:
  - "Take photo" — `<input type="file" accept="image/*" capture="environment">`, which opens the Android camera directly
  - "Choose from gallery" — `<input type="file" accept="image/*" multiple>`
- Do not use `getUserMedia` or a service worker: the app is served over plain HTTP, where browsers block both. The file-input approach works over HTTP.
- Resize in the browser before upload (Dropzone `resizeWidth: 2000`, quality 0.8) so uploads are fast on mobile data.
- Server side: accept JPEG, PNG, WebP and HEIC (convert HEIC to JPEG); validate that the file is really an image; apply EXIF orientation; reject files over 20 MB; generate the thumbnail; compute `sha256`.
- If the same `sha256` already exists in the same visit, skip it silently. If it exists under a different property, accept it but show a warning naming the other address.
- Media files are served through a Django view that checks login (or a valid share token), not as public static files.

## 6. PDF export

"Export PDF" on the property page downloads `<address>-ac-maintenance.pdf`:
- header with address, type, tenant, generation date
- one section per visit, newest first: date, checklist, filter size, note, then the visit's photos in a 3-column grid (use downscaled images so the file stays small)
- optional query parameter to export a single visit

## 7. Share link

- On the property page, "Share link" generates a URL of the form `/share/<token>/` with a random 32+ character token, and shows it with a copy button.
- The link opens a read-only version of the property page (visits, checklist, photos, lightbox, PDF download) without login and with no navigation to other properties.
- "Disable link" clears the token; "Regenerate" replaces it. Old tokens return 404.
- Share pages send `noindex` headers.

## 8. Data import

Two management commands, both idempotent (safe to run twice).

**`import_properties <path-to-xlsx>`**
- Source file: `Property AC Maintenance Oct2026.xlsx`, 16 properties.
- The header row is not the first row; find the row whose first cell is `Property`. Columns: `Property`, `Tenant`, `AC maintenance done Date`, `Note`.
- A `Townhouse:` or `Condo:` prefix on the address sets `property_type` and is removed from the address; no prefix means House.
- `Tenant` goes to `tenant_name`, `Note` to `note`. If a done date is present, create a visit for that date.
- Run automatically on first start when the properties table is empty and the file exists at `data/properties.xlsx`.

**`import_photos <directory>`**
- Source: the Google Drive `Photo` folder downloaded to disk; one subfolder per address, containing phone JPEGs named `IMG_YYYYMMDD_HHMMSS.jpg`.
- Folder names do not match the spreadsheet addresses exactly (missing type prefix, different punctuation, typos). Match on house number + first word of the street name, case-insensitive. Print unmatched folders and continue.
- Group each folder's photos into visits by date taken (EXIF, else filename).
- Skip files already imported (same `sha256` in the same property). When a file's hash already exists under a different property, do not import it and list it in the summary. The existing folders contain at least one such misfiled photo.
- Print a summary: properties matched, visits created, photos imported, skipped, duplicates.

## 9. Deployment

- `docker-compose.yml` with two services: `web` (Gunicorn) and `db` (Postgres).
- Publish the app on host port **8001** (`8001:8000`). Port 8000 on the host is taken.
- No reverse proxy, no HTTPS, no domain.
- Persistent data in bind-mounted host directories so it survives `docker compose down` and container removal:
  - `./data/postgres` → Postgres data
  - `./data/media` → uploaded photos
  - `./data/import` → input for the import commands (read-only)
- Configuration through `.env` (provide `.env.example`): `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, database credentials, `MAINTENANCE_INTERVAL_DAYS`, `TZ=America/New_York`.
- Because the site is HTTP-only, keep `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` off.
- The web container entrypoint waits for the database, runs `migrate` and `collectstatic`, then starts Gunicorn. `db` has a healthcheck and `web` depends on it.
- Run as a non-root user in the container.
- README with: first start, creating the superuser, running both imports, backup (dump the database and copy `data/media`), update procedure.

## 10. Tests and acceptance criteria

Write tests (pytest-django) for models, the upload endpoint, duplicate handling, both import commands, share-link access rules and PDF generation.

The work is done when:
1. `docker compose up -d` on a clean machine brings the app up on `http://<host>:8001/` and the login page loads.
2. After `docker compose down` and `up` again, properties, visits and photos are still there.
3. Anonymous requests to any page or media URL except `/share/<token>/` redirect to login.
4. The xlsx import creates 16 properties with correct types and tenant names; a second run changes nothing.
5. The photo import attaches each folder's photos to the right property, split into visits by date, and reports the cross-property duplicate.
6. On an Android phone in Chrome: "Take photo" opens the camera and the photo appears in the visit grid; "Choose from gallery" uploads several photos at once.
7. On desktop: dragging several files onto the box uploads them with progress and they appear without a reload.
8. A new visit prefills filter size from the previous visit.
9. PDF export contains every visit with its checklist, filter size, note and photos.
10. A share link works in a private browser window, gives no access to other properties, and stops working after "Disable link".
11. All pages are usable at 360 px width.

## 11. Out of scope

HTTPS and reverse proxy, user self-registration, per-user roles, photo categories, Google Drive API integration, email notifications, non-English UI.
