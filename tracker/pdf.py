"""PDF maintenance report rendered with WeasyPrint."""

import base64
import logging
import re

from django.template.loader import render_to_string
from django.utils import timezone

from .images import downscale_for_pdf
from .models import Property, Visit

logger = logging.getLogger(__name__)


def pdf_filename(prop: Property) -> str:
    safe = re.sub(r'[\\/:*?"<>|\r\n]+', " ", prop.address)
    safe = re.sub(r"\s+", " ", safe).strip(" .")
    return f"{safe}-ac-maintenance.pdf"


def _photo_data_uri(photo) -> str | None:
    try:
        data = downscale_for_pdf(photo.image.path)
    except Exception:  # noqa: BLE001 - a missing file must not break the whole report
        logger.warning("Could not embed photo %s in PDF", photo.pk, exc_info=True)
        return None
    return "data:image/jpeg;base64," + base64.b64encode(data).decode("ascii")


def build_report_html(prop: Property, visit: Visit | None = None) -> str:
    visits = [visit] if visit else list(prop.visits.prefetch_related("photos"))
    sections = []
    for v in visits:
        images = [uri for uri in (_photo_data_uri(p) for p in v.photos.all()) if uri]
        rows = [images[i : i + 3] for i in range(0, len(images), 3)]
        if rows:
            rows[-1] += [None] * (3 - len(rows[-1]))  # keep a 3-column grid on the last row
        sections.append({"visit": v, "rows": rows, "photo_count": len(images)})

    return render_to_string(
        "tracker/pdf_report.html",
        {"property": prop, "sections": sections, "generated_at": timezone.localtime()},
    )


def render_property_pdf(prop: Property, visit: Visit | None = None) -> bytes:
    from weasyprint import HTML  # imported lazily: heavy native dependency

    return HTML(string=build_report_html(prop, visit)).write_pdf()
