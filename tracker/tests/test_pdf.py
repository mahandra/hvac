from datetime import date

import pytest
from django.urls import reverse

from tracker.models import Visit
from tracker.pdf import build_report_html, pdf_filename
from tracker.services import add_uploaded_photo

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_visits(prop, jpeg):
    old = Visit.objects.create(
        property=prop, visit_date=date(2025, 4, 1), outdoor_unit_cleaned=True, filter_size="16x20x1",
        note="Old visit note",
    )
    new = Visit.objects.create(
        property=prop, visit_date=date(2026, 10, 1), filter_replaced=True, filter_size="20x25x1",
        note="New visit note",
    )
    for i in range(4):
        add_uploaded_photo(new, jpeg(color=(i * 40, 10, 10), size=(1600, 1200)), f"n{i}.jpg")
    add_uploaded_photo(old, jpeg(color=(1, 200, 1)), "o.jpg")
    return old, new


def test_report_html_contains_every_visit(prop, two_visits):
    html = build_report_html(prop)
    assert prop.address in html and "Jane Doe" in html and "House" in html
    assert html.index("Oct 1, 2026") < html.index("Apr 1, 2025")  # newest first
    for text in ("16x20x1", "20x25x1", "Old visit note", "New visit note",
                 "Outdoor unit cleaned", "Indoor filter replaced"):
        assert text in html
    assert html.count("data:image/jpeg;base64,") == 5
    assert html.count("<tr>") == 3  # 4 photos -> 2 rows, 1 photo -> 1 row (3 columns each)


def test_report_html_single_visit(prop, two_visits):
    old, _ = two_visits
    html = build_report_html(prop, old)
    assert "Old visit note" in html and "New visit note" not in html


def test_report_images_are_downscaled(prop, two_visits):
    import base64
    import io

    from PIL import Image

    html = build_report_html(prop, two_visits[1])
    b64 = html.split("data:image/jpeg;base64,")[1].split('"')[0]
    assert max(Image.open(io.BytesIO(base64.b64decode(b64))).size) <= 900


def test_pdf_download(auth_client, prop, two_visits):
    resp = auth_client.get(reverse("property_pdf", args=[prop.pk]))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF")
    assert "2101 Maple Ridge Dr., Springfield-ac-maintenance.pdf" in resp["Content-Disposition"]
    assert "attachment" in resp["Content-Disposition"]


def test_pdf_single_visit_query_param(auth_client, prop, two_visits):
    _, new = two_visits
    resp = auth_client.get(reverse("property_pdf", args=[prop.pk]), {"visit": new.pk})
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    assert "2026-10-01" in resp["Content-Disposition"]
    assert auth_client.get(reverse("property_pdf", args=[prop.pk]), {"visit": "abc"}).status_code == 404


def test_pdf_filename_sanitized(prop):
    prop.address = 'Unit 5/B "North": Springfield'
    assert pdf_filename(prop) == "Unit 5 B North Springfield-ac-maintenance.pdf"
