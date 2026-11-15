"""MCP server exposing the maintenance tracker to MCP clients (Claude Code, Claude Desktop, ...).

Runs over stdio via ``python manage.py mcp_server`` and talks to the database through the Django ORM,
so it needs no network port and no separate credentials: whoever can start it already has shell access
to the app. It can read everything and create/edit visits, but never deletes anything.

Tool functions are plain sync functions (MCPServer runs them in a worker thread, where the ORM is safe),
so they can be unit-tested without an MCP client.
"""

import functools
from datetime import date

from django.conf import settings
from django.db import close_old_connections, connection
from django.db.models import Count, Q
from django.utils import timezone
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations

from .images import downscale_for_pdf
from .models import Photo, Property, Visit

INSTRUCTIONS = """\
AC maintenance tracker for rental properties. A technician visits each property, cleans the outdoor
unit, replaces the indoor filter and takes photos; each visit can be marked as paid.
A property is "due" when it has had no visit in the last {interval} days.
Identify a property by its numeric id or by (part of) its address, e.g. "12" or "Maple Ridge".
Dates are ISO (YYYY-MM-DD). Nothing can be deleted through this server; use the web app for that.
"""

READ = ToolAnnotations(read_only_hint=True, open_world_hint=False)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False)
UPDATE = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def _db(fn):
    """Give each call a fresh DB connection and close it afterwards.

    Tools run in a pool of worker threads, each with its own connection; calls are rare, so closing
    after every call is cheap and keeps idle threads from holding connections open.
    """

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        close_old_connections()
        try:
            return fn(*args, **kwargs)
        finally:
            connection.close()

    return wrapper


# --------------------------------------------------------------------------- helpers


def _iso(value) -> str | None:
    return value.isoformat() if value else None


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise ToolError(f"Invalid date {value!r}; use YYYY-MM-DD.") from None


def _is_due(last_visit: date | None) -> bool:
    if last_visit is None:
        return True
    return (timezone.localdate() - last_visit).days > settings.MAINTENANCE_INTERVAL_DAYS


def _find_property(address_or_id: str) -> Property:
    ref = str(address_or_id).strip()
    if not ref:
        raise ToolError("Give a property id or address.")
    if ref.isdigit():
        prop = Property.objects.filter(pk=int(ref)).first()
        if prop:
            return prop
    exact = Property.objects.filter(address__iexact=ref).first()
    if exact:
        return exact
    matches = list(Property.objects.filter(address__icontains=ref).order_by("address")[:11])
    if not matches:  # every word must appear, e.g. "2101 Ridge"
        query = Q()
        for word in ref.split():
            query &= Q(address__icontains=word)
        matches = list(Property.objects.filter(query).order_by("address")[:11])
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ToolError(f"No property matches {ref!r}. Use list_properties to see all addresses.")
    names = "; ".join(f"{p.pk}: {p.address}" for p in matches[:10])
    raise ToolError(f"{ref!r} matches several properties ({names}). Use the id or a more specific address.")


def _get_visit(visit_id: int) -> Visit:
    visit = Visit.objects.select_related("property").filter(pk=visit_id).first()
    if visit is None:
        raise ToolError(f"No visit with id {visit_id}.")
    return visit


def _visit_dict(visit: Visit, photo_count: int | None = None) -> dict:
    data = {
        "id": visit.pk,
        "property_id": visit.property_id,
        "address": visit.property.address,
        "visit_date": _iso(visit.visit_date),
        "outdoor_unit_cleaned": visit.outdoor_unit_cleaned,
        "filter_replaced": visit.filter_replaced,
        "filter_size": visit.filter_size,
        "note": visit.note,
        "paid": visit.paid,
    }
    if photo_count is not None:
        data["photo_count"] = photo_count
    return data


def _property_dict(prop: Property) -> dict:
    return {
        "id": prop.pk,
        "address": prop.address,
        "type": prop.get_property_type_display(),
        "tenant": prop.tenant_name,
        "active": prop.is_active,
        "note": prop.note,
        "maps_url": prop.maps_url,
    }


def _check_date_free(prop: Property, visit_date: date, exclude_pk: int | None = None) -> None:
    clash = Visit.objects.filter(property=prop, visit_date=visit_date).exclude(pk=exclude_pk).first()
    if clash:
        raise ToolError(
            f"{prop.address} already has a visit on {visit_date.isoformat()} (visit id {clash.pk}); "
            "use update_visit instead."
        )


# --------------------------------------------------------------------------- read tools


def list_properties(search: str = "", only_due: bool = False, only_unpaid: bool = False) -> list[dict]:
    """List active properties with last visit, unpaid visit count and due status.

    search: filter by part of the address. only_due: properties needing maintenance.
    only_unpaid: properties with at least one unpaid visit. Sorted by most recent visit first.
    """
    qs = Property.objects.filter(is_active=True)
    if search:
        qs = qs.filter(address__icontains=search.strip())
    qs = qs.annotate(
        visit_count=Count("visits"),
        unpaid_count=Count("visits", filter=Q(visits__paid=False)),
    )
    last_dates = {}
    for prop_id, visit_date in Visit.objects.filter(property__in=qs).order_by("property", "-visit_date").values_list(
        "property", "visit_date"
    ):
        last_dates.setdefault(prop_id, visit_date)
    rows = []
    for prop in qs:
        last = last_dates.get(prop.pk)
        row = {
            "id": prop.pk,
            "address": prop.address,
            "type": prop.get_property_type_display(),
            "tenant": prop.tenant_name,
            "last_visit": _iso(last),
            "visit_count": prop.visit_count,
            "unpaid_visits": prop.unpaid_count,
            "due": _is_due(last),
        }
        if only_due and not row["due"]:
            continue
        if only_unpaid and not row["unpaid_visits"]:
            continue
        rows.append(row)
    rows.sort(key=lambda r: r["address"].lower())
    rows.sort(key=lambda r: r["last_visit"] or "", reverse=True)  # visited first, newest on top
    return rows


def get_property(address_or_id: str) -> dict:
    """Property details with its full visit history (newest first), including photo counts and paid status."""
    prop = _find_property(address_or_id)
    visits = prop.visits.select_related("property").annotate(n=Count("photos")).order_by("-visit_date")
    data = _property_dict(prop)
    data["due"] = _is_due(visits[0].visit_date if visits else None)
    data["has_share_link"] = bool(prop.share_token)
    data["visits"] = [_visit_dict(v, v.n) for v in visits]
    return data


def get_visit(visit_id: int) -> dict:
    """One visit with its checklist, note, paid status and photo list (use get_photo to see a photo)."""
    visit = _get_visit(visit_id)
    data = _visit_dict(visit)
    data["photos"] = [
        {"id": p.pk, "filename": p.original_filename, "taken_at": _iso(p.taken_at)}
        for p in visit.photos.order_by("taken_at", "uploaded_at")
    ]
    data["photo_count"] = len(data["photos"])
    return data


def list_unpaid_visits() -> list[dict]:
    """All visits not marked as paid (oldest first), across all properties."""
    visits = (
        Visit.objects.filter(paid=False)
        .select_related("property")
        .annotate(n=Count("photos"))
        .order_by("visit_date", "property__address")
    )
    return [_visit_dict(v, v.n) for v in visits]


def get_photo(photo_id: int, full_size: bool = False) -> Image:
    """Return a photo as an image: the 400 px thumbnail, or a 900 px version with full_size=true."""
    photo = Photo.objects.filter(pk=photo_id).first()
    if photo is None:
        raise ToolError(f"No photo with id {photo_id}.")
    try:
        if full_size:
            return Image(data=downscale_for_pdf(photo.image.path), format="jpeg")
        with photo.thumbnail.open("rb") as fh:
            return Image(data=fh.read(), format="jpeg")
    except (FileNotFoundError, ValueError):
        raise ToolError(f"The file for photo {photo_id} is missing on disk.") from None


# --------------------------------------------------------------------------- write tools


def create_visit(
    address_or_id: str,
    visit_date: str = "",
    outdoor_unit_cleaned: bool = False,
    filter_replaced: bool = False,
    filter_size: str | None = None,
    note: str = "",
    paid: bool = False,
) -> dict:
    """Record a new visit. visit_date defaults to today; filter_size defaults to the previous visit's size.

    Fails if the property already has a visit on that date (then use update_visit).
    Photos cannot be added here; upload them in the web app.
    """
    prop = _find_property(address_or_id)
    day = _parse_date(visit_date) if visit_date else timezone.localdate()
    _check_date_free(prop, day)
    visit = Visit.objects.create(
        property=prop,
        visit_date=day,
        outdoor_unit_cleaned=outdoor_unit_cleaned,
        filter_replaced=filter_replaced,
        filter_size=(filter_size if filter_size is not None else Visit.initial_filter_size(prop)).strip(),
        note=note,
        paid=paid,
    )
    return _visit_dict(visit, 0)


def update_visit(
    visit_id: int,
    visit_date: str | None = None,
    outdoor_unit_cleaned: bool | None = None,
    filter_replaced: bool | None = None,
    filter_size: str | None = None,
    note: str | None = None,
    paid: bool | None = None,
) -> dict:
    """Change fields of an existing visit. Only the fields you pass are changed; omitted ones stay as they are.

    note replaces the whole note text.
    """
    visit = _get_visit(visit_id)
    if visit_date is not None:
        day = _parse_date(visit_date)
        _check_date_free(visit.property, day, exclude_pk=visit.pk)
        visit.visit_date = day
    for field, value in (
        ("outdoor_unit_cleaned", outdoor_unit_cleaned),
        ("filter_replaced", filter_replaced),
        ("filter_size", filter_size.strip() if filter_size is not None else None),
        ("note", note),
        ("paid", paid),
    ):
        if value is not None:
            setattr(visit, field, value)
    visit.save()
    return get_visit(visit.pk)


def set_visit_paid(visit_id: int, paid: bool = True) -> dict:
    """Mark a visit as paid (or unpaid with paid=false)."""
    visit = _get_visit(visit_id)
    visit.paid = paid
    visit.save(update_fields=["paid", "updated_at"])
    return _visit_dict(visit)


# --------------------------------------------------------------------------- server


READ_TOOLS = (list_properties, get_property, get_visit, list_unpaid_visits, get_photo)
WRITE_TOOLS = ((create_visit, WRITE), (update_visit, UPDATE), (set_visit_paid, UPDATE))


def build_server() -> MCPServer:
    server = MCPServer(
        name="hvac-tracker",
        title="AC Maintenance Tracker",
        instructions=INSTRUCTIONS.format(interval=settings.MAINTENANCE_INTERVAL_DAYS),
    )
    for fn in READ_TOOLS:
        server.tool(annotations=READ)(_db(fn))
    for fn, annotations in WRITE_TOOLS:
        server.tool(annotations=annotations)(_db(fn))
    return server
