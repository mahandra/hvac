from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from functools import wraps
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.db.models import Count, OuterRef, Q, Subquery
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils._os import safe_join
from django.utils.http import content_disposition_header, url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_POST

from .forms import VisitForm
from .images import ImageValidationError
from .models import Photo, Property, Visit
from .pdf import pdf_filename, render_property_pdf
from .services import add_uploaded_photo


def _is_htmx(request) -> bool:
    return request.headers.get("HX-Request") == "true"


def noindex(view):
    @wraps(view)
    def wrapper(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response

    return wrapper


# --------------------------------------------------------------------------- properties


@dataclass(frozen=True)
class SortColumn:
    key: str
    label: str
    default_dir: str  # direction used when the column is first clicked
    value: Callable  # row -> sort value; None/"" sorts last in both directions
    css: str = ""  # responsive visibility classes for the column


SORT_COLUMNS = [
    SortColumn("address", "Address", "asc", lambda p: p.address.lower()),
    SortColumn("type", "Type", "asc", lambda p: p.get_property_type_display(), "d-none d-md-table-cell"),
    SortColumn("tenant", "Tenant", "asc", lambda p: p.tenant_name.strip().lower(), "d-none d-md-table-cell"),
    SortColumn("last_visit", "Last visit", "desc", lambda p: p.last_visit_date),
    SortColumn("photos", "Photos", "desc", lambda p: p.last_visit_photo_count, "d-none d-sm-table-cell"),
    # Paid: number of unpaid visits (all visits, not only the last one); no visits sorts last.
    SortColumn("paid", "Paid", "desc", lambda p: p.unpaid_count if p.visit_count else None, "d-none d-sm-table-cell"),
    SortColumn("status", "Status", "asc", lambda p: 0 if p.due else 1),
]
SORT_BY_KEY = {c.key: c for c in SORT_COLUMNS}
# Default: visited properties on top, most recent visit first; never-visited ones at the bottom.
DEFAULT_SORT, DEFAULT_DIR = "last_visit", "desc"


def _sort_rows(rows: list, sort: str, direction: str) -> list:
    column = SORT_BY_KEY[sort]
    rows = sorted(rows, key=lambda p: p.address.lower())  # stable tie-breaker
    present = [p for p in rows if column.value(p) not in (None, "")]
    missing = [p for p in rows if column.value(p) in (None, "")]
    present.sort(key=column.value, reverse=direction == "desc")
    return present + missing


def _property_rows(search: str = "", sort: str = DEFAULT_SORT, direction: str = DEFAULT_DIR):
    latest = Visit.objects.filter(property=OuterRef("pk")).order_by("-visit_date")
    qs = Property.objects.filter(is_active=True).annotate(
        last_visit_date=Subquery(latest.values("visit_date")[:1]),
        last_visit_photo_count=Subquery(latest.annotate(n=Count("photos")).values("n")[:1]),
        visit_count=Count("visits"),
        unpaid_count=Count("visits", filter=Q(visits__paid=False)),
    )
    if search:
        qs = qs.filter(address__icontains=search)
    today = timezone.localdate()
    cutoff = today - timedelta(days=settings.MAINTENANCE_INTERVAL_DAYS)
    rows = list(qs)
    for prop in rows:
        prop.due = prop.last_visit_date is None or prop.last_visit_date < cutoff
    return _sort_rows(rows, sort, direction)


def _sort_headers(search: str, sort: str, direction: str) -> list[dict]:
    headers = []
    for column in SORT_COLUMNS:
        active = column.key == sort
        if active:
            next_dir = "asc" if direction == "desc" else "desc"
        else:
            next_dir = column.default_dir
        params = {"sort": column.key, "dir": next_dir}
        if search:
            params["q"] = search
        headers.append(
            {
                "key": column.key,
                "label": column.label,
                "css": column.css,
                "active": active,
                "dir": direction if active else "",
                "url": "?" + urlencode(params),
            }
        )
    return headers


@require_GET
def property_list(request):
    search = request.GET.get("q", "").strip()
    sort = request.GET.get("sort", DEFAULT_SORT)
    direction = request.GET.get("dir", "")
    if sort not in SORT_BY_KEY:
        sort = DEFAULT_SORT
    if direction not in ("asc", "desc"):
        direction = DEFAULT_DIR if sort == DEFAULT_SORT else SORT_BY_KEY[sort].default_dir
    context = {
        "properties": _property_rows(search, sort, direction),
        "headers": _sort_headers(search, sort, direction),
        "q": search,
        "sort": sort,
        "dir": direction,
        "interval_days": settings.MAINTENANCE_INTERVAL_DAYS,
    }
    if _is_htmx(request):
        return render(request, "tracker/_property_rows.html", context)
    return render(request, "tracker/property_list.html", context)


def _property_context(prop: Property, read_only: bool = False, share_token: str | None = None):
    return {
        "property": prop,
        "visits": prop.visits.prefetch_related("photos"),
        "read_only": read_only,
        "share_token": share_token,
        "due": prop.is_due(),
    }


@require_GET
def property_detail(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    context = _property_context(prop)
    context["share_url"] = _share_url(request, prop)
    return render(request, "tracker/property_detail.html", context)


@require_GET
def property_pdf(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    return _pdf_response(request, prop)


def _pdf_response(request, prop: Property):
    visit = None
    visit_id = request.GET.get("visit")
    if visit_id:
        if not visit_id.isdigit():
            raise Http404
        visit = get_object_or_404(Visit, pk=int(visit_id), property=prop)
    pdf = render_property_pdf(prop, visit)
    filename = pdf_filename(prop)
    if visit:
        filename = filename.replace("-ac-maintenance.pdf", f"-{visit.visit_date:%Y-%m-%d}-ac-maintenance.pdf")
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = content_disposition_header(as_attachment=True, filename=filename)
    return response


# --------------------------------------------------------------------------- share link


def _share_url(request, prop: Property) -> str | None:
    if not prop.share_token:
        return None
    return request.build_absolute_uri(reverse("share_property", args=[prop.share_token]))


@require_POST
def share_manage(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    action = request.POST.get("action")
    if action in ("create", "regenerate"):
        prop.regenerate_share_token()
    elif action == "disable":
        prop.disable_share_token()
    if _is_htmx(request):
        return render(
            request, "tracker/_share_panel.html", {"property": prop, "share_url": _share_url(request, prop)}
        )
    return redirect(prop)


def _shared_property(token: str) -> Property:
    if not token:
        raise Http404
    return get_object_or_404(Property, share_token=token, is_active=True)


@login_not_required
@noindex
@require_GET
def share_property(request, token):
    prop = _shared_property(token)
    return render(request, "tracker/share_property.html", _property_context(prop, True, token))


@login_not_required
@noindex
@require_GET
def share_pdf(request, token):
    return _pdf_response(request, _shared_property(token))


@login_not_required
@noindex
@require_GET
def share_photo(request, token, pk, kind):
    prop = _shared_property(token)
    photo = get_object_or_404(Photo, pk=pk, visit__property=prop)
    return _photo_file_response(photo, kind)


# --------------------------------------------------------------------------- visits


def visit_new(request, pk):
    prop = get_object_or_404(Property, pk=pk)
    if request.method == "POST":
        form = VisitForm(request.POST, property=prop)
        if form.is_valid():
            existing = Visit.objects.filter(property=prop, visit_date=form.cleaned_data["visit_date"]).first()
            if existing:
                messages.info(request, "A visit for that date already exists — opened it.")
                return redirect(existing)
            visit = form.save(commit=False)
            visit.property = prop
            visit.created_by = request.user
            visit.save()
            messages.success(request, "Visit saved. You can add photos now.")
            return redirect(visit)
    else:
        today = timezone.localdate()
        existing = Visit.objects.filter(property=prop, visit_date=today).first()
        if existing:
            messages.info(request, "Today's visit already exists — opened it.")
            return redirect(existing)
        form = VisitForm(
            property=prop,
            initial={"visit_date": today, "filter_size": Visit.initial_filter_size(prop)},
        )
    return render(request, "tracker/visit_form.html", {"property": prop, "form": form, "visit": None})


def visit_detail(request, pk):
    visit = get_object_or_404(Visit.objects.select_related("property"), pk=pk)
    if request.method == "POST":
        form = VisitForm(request.POST, instance=visit)
        if form.is_valid():
            form.save()
            messages.success(request, "Visit saved.")
            return redirect(visit)
    else:
        form = VisitForm(instance=visit)
    return render(
        request,
        "tracker/visit_form.html",
        {"property": visit.property, "form": form, "visit": visit, "photos": visit.photos.all()},
    )


@require_POST
def visit_delete(request, pk):
    visit = get_object_or_404(Visit, pk=pk)
    prop = visit.property
    visit.delete()
    messages.success(request, "Visit deleted.")
    return redirect(prop)


# --------------------------------------------------------------------------- photos


@require_GET
def visit_photo_grid(request, pk):
    visit = get_object_or_404(Visit, pk=pk)
    return render(request, "tracker/_photo_grid.html", {"visit": visit, "photos": visit.photos.all()})


@require_POST
def photo_upload(request, pk):
    visit = get_object_or_404(Visit.objects.select_related("property"), pk=pk)
    upload = request.FILES.get("file")
    if upload is None:
        return JsonResponse({"error": "No file received."}, status=400)
    if upload.size > settings.MAX_UPLOAD_SIZE:
        return JsonResponse({"error": "File is larger than 20 MB."}, status=400)
    data = upload.read()
    client_oriented = request.POST.get("client_oriented") == "1"
    try:
        result = add_uploaded_photo(visit, data, upload.name, request.user, client_oriented)
    except ImageValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    payload = {"status": result.status, "filename": upload.name}
    if result.photo:
        payload["photo_id"] = result.photo.pk
    if result.other_addresses:
        payload["warning"] = (
            f"“{upload.name}” was already uploaded for another property: "
            + "; ".join(result.other_addresses)
        )
    return JsonResponse(payload, status=201 if result.status == "created" else 200)


@require_POST
def photo_delete(request, pk):
    photo = get_object_or_404(Photo.objects.select_related("visit"), pk=pk)
    visit = photo.visit
    photo.delete()
    if _is_htmx(request):
        return render(request, "tracker/_photo_grid.html", {"visit": visit, "photos": visit.photos.all()})
    next_url = request.POST.get("next")
    if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
        return redirect(next_url)
    return redirect(visit)


def _photo_file_response(photo: Photo, kind: str):
    field = {"image": photo.image, "thumb": photo.thumbnail}.get(kind)
    if not field:
        raise Http404
    try:
        handle = field.open("rb")
    except FileNotFoundError:
        raise Http404 from None
    response = FileResponse(handle, content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=86400"
    return response


@require_GET
def media_file(request, path):
    """Login-protected media serving (the login check is done by LoginRequiredMiddleware)."""
    try:
        full_path = safe_join(settings.MEDIA_ROOT, path)
    except Exception:  # SuspiciousFileOperation and friends
        raise Http404 from None
    try:
        response = FileResponse(open(full_path, "rb"))
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise Http404 from None
    response["Cache-Control"] = "private, max-age=86400"
    return response
