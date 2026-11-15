"""Login requirements and share-link access rules."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from tracker.models import Photo, Visit

pytestmark = pytest.mark.django_db


@pytest.fixture
def photo(auth_client, visit, jpeg):
    auth_client.post(
        reverse("photo_upload", args=[visit.pk]),
        {"file": SimpleUploadedFile("a.jpg", jpeg(), content_type="image/jpeg")},
    )
    auth_client.logout()
    return Photo.objects.get()


def protected_urls(visit, photo):
    pk = visit.property_id
    return [
        reverse("property_list"),
        reverse("property_detail", args=[pk]),
        reverse("property_pdf", args=[pk]),
        reverse("visit_new", args=[pk]),
        reverse("visit_detail", args=[visit.pk]),
        reverse("visit_photo_grid", args=[visit.pk]),
        photo.image.url,
        photo.thumbnail.url,
        "/media/does/not/exist.jpg",
    ]


def test_anonymous_requests_redirect_to_login(client, visit, photo):
    for url in protected_urls(visit, photo):
        resp = client.get(url)
        assert resp.status_code == 302, url
        assert resp["Location"].startswith(reverse("login")), url


def test_anonymous_post_endpoints_redirect_to_login(client, visit, photo):
    for url in (
        reverse("share_manage", args=[visit.property_id]),
        reverse("photo_delete", args=[photo.pk]),
        reverse("visit_delete", args=[visit.pk]),
    ):
        resp = client.post(url)
        assert resp.status_code == 302 and resp["Location"].startswith(reverse("login")), url
    assert Photo.objects.exists() and Visit.objects.exists()


def test_share_manage_create_regenerate_disable(auth_client, prop):
    url = reverse("share_manage", args=[prop.pk])
    resp = auth_client.post(url, {"action": "create"}, HTTP_HX_REQUEST="true")
    prop.refresh_from_db()
    token = prop.share_token
    assert token and f"/share/{token}/" in resp.content.decode()

    auth_client.post(url, {"action": "regenerate"})
    prop.refresh_from_db()
    assert prop.share_token and prop.share_token != token

    auth_client.post(url, {"action": "disable"})
    prop.refresh_from_db()
    assert prop.share_token is None


def test_share_link_works_without_login(client, visit, photo):
    token = visit.property.regenerate_share_token()
    resp = client.get(reverse("share_property", args=[token]))
    assert resp.status_code == 200
    assert resp["X-Robots-Tag"] == "noindex, nofollow"
    html = resp.content.decode()
    assert visit.property.address in html
    assert 'name="robots" content="noindex' in html
    # read-only: no editing or navigation to other properties
    assert 'href="' + reverse("property_list") + '"' not in html
    assert "/visits/" not in html
    assert "Delete" not in html
    assert "/media/" not in html  # photos are served through token-scoped URLs

    for kind in ("thumb", "image"):
        r = client.get(reverse("share_photo", args=[token, photo.pk, kind]))
        assert r.status_code == 200 and r["X-Robots-Tag"] == "noindex, nofollow"

    pdf = client.get(reverse("share_pdf", args=[token]))
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf"


def test_share_link_gives_no_access_to_other_properties(client, visit, photo, other_prop, jpeg):
    token = visit.property.regenerate_share_token()
    other_visit = Visit.objects.create(property=other_prop, visit_date=visit.visit_date)
    from tracker.services import add_uploaded_photo

    other_photo = add_uploaded_photo(other_visit, jpeg(color=(0, 0, 255)), "b.jpg").photo
    assert client.get(reverse("share_photo", args=[token, other_photo.pk, "image"])).status_code == 404
    assert client.get(reverse("share_pdf", args=[token]) + f"?visit={other_visit.pk}").status_code == 404
    assert client.get(reverse("share_photo", args=[token, photo.pk, "bogus"])).status_code == 404
    # A share session does not log anyone in.
    assert client.get(reverse("property_detail", args=[other_prop.pk])).status_code == 302


def test_disabled_and_regenerated_tokens_return_404(client, prop):
    old = prop.regenerate_share_token()
    new = prop.regenerate_share_token()
    assert client.get(reverse("share_property", args=[old])).status_code == 404
    assert client.get(reverse("share_property", args=[new])).status_code == 200
    prop.disable_share_token()
    assert client.get(reverse("share_property", args=[new])).status_code == 404
    assert client.get(reverse("share_pdf", args=[new])).status_code == 404


def test_share_link_for_inactive_property_is_404(client, prop):
    token = prop.regenerate_share_token()
    prop.is_active = False
    prop.save()
    assert client.get(reverse("share_property", args=[token])).status_code == 404
