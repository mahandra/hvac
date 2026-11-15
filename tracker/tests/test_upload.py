from datetime import date

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from tracker.models import Photo, Visit

pytestmark = pytest.mark.django_db


def upload(client, visit, data, name="IMG_20261001_101500.jpg", **extra):
    return client.post(
        reverse("photo_upload", args=[visit.pk]),
        {"file": SimpleUploadedFile(name, data, content_type="image/jpeg"), **extra},
    )


def test_upload_creates_photo_with_thumbnail(auth_client, visit, user, jpeg):
    resp = upload(auth_client, visit, jpeg())
    assert resp.status_code == 201
    assert resp.json()["status"] == "created"
    photo = Photo.objects.get()
    assert photo.visit == visit
    assert photo.uploaded_by == user
    assert photo.original_filename == "IMG_20261001_101500.jpg"
    assert timezone.localtime(photo.taken_at).hour == 10
    assert len(photo.sha256) == 64
    assert photo.image.name.startswith(f"properties/{visit.property_id}/2026-10-01/")
    assert photo.image.storage.exists(photo.thumbnail.name)


def test_upload_rejects_invalid_file(auth_client, visit):
    resp = upload(auth_client, visit, b"not an image", name="x.jpg")
    assert resp.status_code == 400
    assert "error" in resp.json()
    assert not Photo.objects.exists()


def test_upload_rejects_missing_file(auth_client, visit):
    resp = auth_client.post(reverse("photo_upload", args=[visit.pk]))
    assert resp.status_code == 400


def test_upload_rejects_too_large(auth_client, visit, jpeg, settings):
    settings.MAX_UPLOAD_SIZE = 100
    resp = upload(auth_client, visit, jpeg(size=(300, 300)))
    assert resp.status_code == 400
    assert "20 MB" in resp.json()["error"]


def test_upload_requires_login(client, visit, jpeg):
    resp = upload(client, visit, jpeg())
    assert resp.status_code == 302
    assert reverse("login") in resp["Location"]
    assert not Photo.objects.exists()


def test_upload_requires_post(auth_client, visit):
    assert auth_client.get(reverse("photo_upload", args=[visit.pk])).status_code == 405


# --- duplicate handling --------------------------------------------------------------


def test_duplicate_in_same_visit_is_skipped_silently(auth_client, visit, jpeg):
    data = jpeg()
    assert upload(auth_client, visit, data).status_code == 201
    resp = upload(auth_client, visit, data)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "skipped"
    assert "warning" not in body
    assert Photo.objects.count() == 1


def test_duplicate_under_other_property_is_accepted_with_warning(auth_client, visit, other_prop, jpeg):
    data = jpeg()
    other_visit = Visit.objects.create(property=other_prop, visit_date=date(2026, 9, 1))
    assert upload(auth_client, other_visit, data).status_code == 201
    resp = upload(auth_client, visit, data)
    assert resp.status_code == 201
    assert other_prop.address in resp.json()["warning"]
    assert Photo.objects.count() == 2


def test_same_hash_in_other_visit_of_same_property_no_warning(auth_client, visit, jpeg):
    data = jpeg()
    second = Visit.objects.create(property=visit.property, visit_date=date(2026, 11, 1))
    upload(auth_client, visit, data)
    resp = upload(auth_client, second, data)
    assert resp.status_code == 201
    assert "warning" not in resp.json()


# --- grid / delete ---------------------------------------------------------------------


def test_photo_grid_partial_lists_photos(auth_client, visit, jpeg):
    upload(auth_client, visit, jpeg())
    resp = auth_client.get(reverse("visit_photo_grid", args=[visit.pk]), HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert 'id="photo-grid"' in html
    assert "1 photo" in html
    assert "<html" not in html


def test_photo_delete(auth_client, visit, jpeg):
    upload(auth_client, visit, jpeg())
    photo = Photo.objects.get()
    resp = auth_client.post(reverse("photo_delete", args=[photo.pk]), {"next": visit.get_absolute_url()})
    assert resp.status_code == 302
    assert resp["Location"] == visit.get_absolute_url()
    assert not Photo.objects.exists()
    assert not photo.image.storage.exists(photo.image.name)


def test_photo_delete_ignores_external_next(auth_client, visit, jpeg):
    upload(auth_client, visit, jpeg())
    photo = Photo.objects.get()
    resp = auth_client.post(reverse("photo_delete", args=[photo.pk]), {"next": "https://evil.example/"})
    assert resp["Location"] == visit.get_absolute_url()


def test_media_served_to_logged_in_user(auth_client, visit, jpeg):
    upload(auth_client, visit, jpeg())
    photo = Photo.objects.get()
    resp = auth_client.get(photo.thumbnail.url)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/jpeg"


def test_media_path_traversal_blocked(auth_client):
    assert auth_client.get("/media/../config/settings.py").status_code == 404
    assert auth_client.get("/media/%2e%2e/manage.py").status_code == 404
