"""Non-superusers get read-only access to visits and photos; superusers can edit everything."""

from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from tracker.models import Photo, Visit

pytestmark = pytest.mark.django_db


@pytest.fixture
def photo(client, user, visit, jpeg):
    client.force_login(user)
    client.post(
        reverse("photo_upload", args=[visit.pk]),
        {"file": SimpleUploadedFile("a.jpg", jpeg(), content_type="image/jpeg")},
    )
    client.logout()
    return Photo.objects.get()


def test_viewer_sees_visit_values_read_only(viewer_client, visit):
    visit.note = "Replaced filter, all good"
    visit.paid = True
    visit.save()
    resp = viewer_client.get(reverse("visit_detail", args=[visit.pk]))
    html = resp.content.decode()
    assert resp.status_code == 200
    for field in ("visit_date", "outdoor_unit_cleaned", "filter_replaced", "filter_size", "note", "paid"):
        assert f'name="{field}"' not in html, field
    assert "Save visit" not in html and "Delete this visit" not in html
    assert "photo-dropzone" not in html and "upload.js" not in html
    assert "20x25x1" in html and "Replaced filter, all good" in html
    assert ">Paid</span>" in html


def test_viewer_cannot_edit_visit(viewer_client, visit):
    resp = viewer_client.post(
        reverse("visit_detail", args=[visit.pk]),
        {"visit_date": "2026-10-01", "filter_size": "changed", "paid": "on"},
    )
    assert resp.status_code == 403
    visit.refresh_from_db()
    assert visit.filter_size == "20x25x1" and not visit.paid


def test_viewer_cannot_create_or_delete_visit(viewer_client, prop, visit):
    assert viewer_client.get(reverse("visit_new", args=[prop.pk])).status_code == 403
    resp = viewer_client.post(reverse("visit_new", args=[prop.pk]), {"visit_date": "2026-11-01"})
    assert resp.status_code == 403
    assert viewer_client.post(reverse("visit_delete", args=[visit.pk])).status_code == 403
    assert list(Visit.objects.values_list("visit_date", flat=True)) == [date(2026, 10, 1)]


def test_viewer_cannot_upload_or_delete_photos(client, viewer, visit, photo, jpeg):
    client.force_login(viewer)
    resp = client.post(
        reverse("photo_upload", args=[visit.pk]),
        {"file": SimpleUploadedFile("b.jpg", jpeg(color=(1, 2, 3)), content_type="image/jpeg")},
    )
    assert resp.status_code == 403
    assert client.post(reverse("photo_delete", args=[photo.pk])).status_code == 403
    assert Photo.objects.count() == 1
    # Photos stay viewable.
    assert client.get(photo.image.url).status_code == 200


def test_viewer_property_page_hides_edit_controls(client, viewer, visit, photo):
    client.force_login(viewer)
    html = client.get(reverse("property_detail", args=[visit.property_id])).content.decode()
    assert reverse("visit_new", args=[visit.property_id]) not in html
    assert reverse("photo_delete", args=[photo.pk]) not in html
    assert "lightbox-delete-form" not in html
    assert "Open / edit" not in html and ">Open</a>" in html


def test_superuser_keeps_full_access(client, user, visit, photo):
    client.force_login(user)
    html = client.get(reverse("visit_detail", args=[visit.pk])).content.decode()
    assert "Save visit" in html and "photo-dropzone" in html and "Delete this visit" in html
    html = client.get(reverse("property_detail", args=[visit.property_id])).content.decode()
    assert reverse("visit_new", args=[visit.property_id]) in html
    assert reverse("photo_delete", args=[photo.pk]) in html
    resp = client.post(reverse("visit_detail", args=[visit.pk]), {"visit_date": "2026-10-01", "paid": "on"})
    assert resp.status_code == 302
    visit.refresh_from_db()
    assert visit.paid
