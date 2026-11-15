from datetime import date, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from tracker.models import Property, Visit

pytestmark = pytest.mark.django_db


@pytest.fixture
def list_props(db):
    """Three active properties: visited recently, visited long ago (due), never visited (due)."""
    today = timezone.localdate()
    recent = Property.objects.create(address="300 Charlie St", tenant_name="Zed")
    old = Property.objects.create(address="100 Alpha St", property_type="condo", tenant_name="Amy")
    never = Property.objects.create(address="200 Bravo St", property_type="townhouse")
    Property.objects.create(address="400 Inactive St", is_active=False)
    Visit.objects.create(property=recent, visit_date=today - timedelta(days=5), paid=True)
    Visit.objects.create(property=old, visit_date=today - timedelta(days=400))
    return recent, old, never


def addresses(resp):
    return [p.address for p in resp.context["properties"]]


def test_property_list_default_sort_visited_on_top(auth_client, list_props):
    resp = auth_client.get(reverse("property_list"))
    # most recent visit first, never-visited last
    assert addresses(resp) == ["300 Charlie St", "100 Alpha St", "200 Bravo St"]
    assert [p.due for p in resp.context["properties"]] == [False, True, True]
    assert "400 Inactive" not in resp.content.decode()
    assert resp.context["sort"] == "last_visit" and resp.context["dir"] == "desc"


@pytest.mark.parametrize(
    "sort, direction, expected",
    [
        ("last_visit", "asc", ["100 Alpha St", "300 Charlie St", "200 Bravo St"]),  # no visit stays last
        ("address", "asc", ["100 Alpha St", "200 Bravo St", "300 Charlie St"]),
        ("address", "desc", ["300 Charlie St", "200 Bravo St", "100 Alpha St"]),
        ("type", "asc", ["100 Alpha St", "300 Charlie St", "200 Bravo St"]),  # Condo, House, Townhouse
        ("tenant", "asc", ["100 Alpha St", "300 Charlie St", "200 Bravo St"]),  # empty tenant last
        ("tenant", "desc", ["300 Charlie St", "100 Alpha St", "200 Bravo St"]),
        ("status", "asc", ["100 Alpha St", "200 Bravo St", "300 Charlie St"]),  # due first
        ("status", "desc", ["300 Charlie St", "100 Alpha St", "200 Bravo St"]),
        ("paid", "desc", ["100 Alpha St", "300 Charlie St", "200 Bravo St"]),  # unpaid first, no visits last
        ("paid", "asc", ["300 Charlie St", "100 Alpha St", "200 Bravo St"]),
    ],
)
def test_property_list_sorting(auth_client, list_props, sort, direction, expected):
    resp = auth_client.get(reverse("property_list"), {"sort": sort, "dir": direction})
    assert addresses(resp) == expected


def test_property_list_invalid_sort_falls_back_to_default(auth_client, list_props):
    resp = auth_client.get(reverse("property_list"), {"sort": "evil", "dir": "sideways"})
    assert addresses(resp) == ["300 Charlie St", "100 Alpha St", "200 Bravo St"]


def test_property_list_header_links_toggle_and_keep_search(auth_client, list_props):
    resp = auth_client.get(reverse("property_list"), {"q": "St", "sort": "address", "dir": "asc"})
    headers = {h["key"]: h for h in resp.context["headers"]}
    assert headers["address"]["active"] and headers["address"]["dir"] == "asc"
    assert headers["address"]["url"] == "?sort=address&dir=desc&q=St"
    assert headers["last_visit"]["url"] == "?sort=last_visit&dir=desc&q=St"
    html = resp.content.decode()
    assert "Last visit" in html and 'aria-sort="ascending"' in html
    assert 'name="sort" value="address" form="property-search"' in html


def test_property_list_htmx_search_keeps_sort(auth_client, list_props):
    resp = auth_client.get(
        reverse("property_list"), {"q": "r", "sort": "address", "dir": "desc"}, HTTP_HX_REQUEST="true"
    )
    assert addresses(resp) == ["300 Charlie St", "200 Bravo St"]


def test_property_list_shows_last_visit_photo_count(auth_client, visit):
    resp = auth_client.get(reverse("property_list"))
    row = resp.context["properties"][0]
    assert row.last_visit_date == visit.visit_date
    assert row.last_visit_photo_count == 0


def test_property_list_htmx_search_returns_partial(auth_client):
    Property.objects.create(address="100 Alpha St")
    Property.objects.create(address="200 Beta St")
    resp = auth_client.get(reverse("property_list"), {"q": "beta"}, HTTP_HX_REQUEST="true")
    html = resp.content.decode()
    assert "200 Beta St" in html and "100 Alpha St" not in html
    assert "<html" not in html


def test_property_detail(auth_client, visit):
    resp = auth_client.get(reverse("property_detail", args=[visit.property_id]))
    html = resp.content.decode()
    assert resp.status_code == 200
    for label in ("New visit", "Export PDF", "Share link", "20x25x1", "Open in Google Maps"):
        assert label in html
    assert 'href="https://www.google.com/maps/search/?api=1&amp;query=' in html


def test_new_visit_prefills_filter_size(auth_client, visit):
    resp = auth_client.get(reverse("visit_new", args=[visit.property_id]))
    assert resp.status_code == 200
    form = resp.context["form"]
    assert form.initial["filter_size"] == "20x25x1"
    assert form.initial["visit_date"] == timezone.localdate()


def test_new_visit_creates_and_redirects(auth_client, prop, user):
    resp = auth_client.post(
        reverse("visit_new", args=[prop.pk]),
        {"visit_date": "2026-11-01", "outdoor_unit_cleaned": "on", "filter_size": "16x25x1", "note": "ok"},
    )
    v = Visit.objects.get(property=prop)
    assert resp.status_code == 302 and resp["Location"] == v.get_absolute_url()
    assert v.outdoor_unit_cleaned and not v.filter_replaced
    assert v.created_by == user


def test_new_visit_for_existing_date_opens_existing(auth_client, visit):
    resp = auth_client.post(
        reverse("visit_new", args=[visit.property_id]),
        {"visit_date": visit.visit_date.isoformat(), "filter_size": "zzz"},
    )
    assert resp["Location"] == visit.get_absolute_url()
    assert Visit.objects.count() == 1
    visit.refresh_from_db()
    assert visit.filter_size == "20x25x1"


def test_new_visit_get_opens_todays_existing_visit(auth_client, prop):
    today_visit = Visit.objects.create(property=prop, visit_date=timezone.localdate())
    resp = auth_client.get(reverse("visit_new", args=[prop.pk]))
    assert resp["Location"] == today_visit.get_absolute_url()


def test_edit_visit(auth_client, visit):
    resp = auth_client.post(
        reverse("visit_detail", args=[visit.pk]),
        {"visit_date": "2026-10-02", "filter_replaced": "on", "filter_size": "20x20x1", "note": "n"},
    )
    assert resp.status_code == 302
    visit.refresh_from_db()
    assert visit.visit_date == date(2026, 10, 2) and visit.filter_replaced


def test_edit_visit_to_clashing_date_is_rejected(auth_client, visit):
    Visit.objects.create(property=visit.property, visit_date=date(2026, 10, 5))
    resp = auth_client.post(reverse("visit_detail", args=[visit.pk]), {"visit_date": "2026-10-05"})
    assert resp.status_code == 200
    assert "already has a visit" in resp.content.decode()


def test_visit_page_has_upload_controls(auth_client, visit):
    html = auth_client.get(reverse("visit_detail", args=[visit.pk])).content.decode()
    assert 'capture="environment"' in html
    assert 'accept="image/*" multiple' in html
    assert 'id="photo-dropzone"' in html


def test_delete_visit(auth_client, visit):
    resp = auth_client.post(reverse("visit_delete", args=[visit.pk]))
    assert resp.status_code == 302
    assert not Visit.objects.exists()


def test_logout_requires_post_and_logs_out(auth_client):
    resp = auth_client.post(reverse("logout"))
    assert resp.status_code == 302
    assert auth_client.get(reverse("property_list")).status_code == 302


def test_login_page_loads(client):
    resp = client.get(reverse("login"))
    assert resp.status_code == 200
    assert 'name="password"' in resp.content.decode()


def test_admin_superuser_only(client, user, django_user_model):
    user.is_staff = True
    user.save()
    client.force_login(user)
    assert client.get("/admin/").status_code == 302  # staff but not superuser
    admin = django_user_model.objects.create_superuser("boss", password="pw-123456")
    client.force_login(admin)
    assert client.get("/admin/").status_code == 200
    assert client.get("/admin/tracker/property/").status_code == 200


def test_admin_property_list_last_visit_column_and_ordering(client, django_user_model, list_props):
    admin = django_user_model.objects.create_superuser("boss", password="pw-123456")
    client.force_login(admin)
    url = "/admin/tracker/property/"

    resp = client.get(url)
    assert resp.status_code == 200
    assert "Last visit" in resp.content.decode()
    # default: earliest visit on top, never-visited at the bottom
    assert [p.address for p in resp.context["cl"].result_list] == [
        "100 Alpha St", "300 Charlie St", "200 Bravo St", "400 Inactive St"]

    # "Last visit" is column 4 (1-based, after the action checkbox column is excluded)
    asc = client.get(url, {"o": "4"})
    desc = client.get(url, {"o": "-4"})
    assert [p.address for p in asc.context["cl"].result_list][:2] == ["100 Alpha St", "300 Charlie St"]
    desc_order = [p.address for p in desc.context["cl"].result_list]
    assert desc_order[:2] == ["300 Charlie St", "100 Alpha St"]
    assert set(desc_order[2:]) == {"200 Bravo St", "400 Inactive St"}  # no visits: still last


def test_admin_default_sort_indicator_on_last_visit(client, django_user_model, list_props):
    client.force_login(django_user_model.objects.create_superuser("boss", password="pw-123456"))
    resp = client.get("/admin/tracker/property/")
    assert resp.context["cl"].get_ordering_field_columns() == {4: "asc"}
    html = resp.content.decode()
    assert 'class="sortable column-last_visit sorted ascending"' in html
    assert 'class="sortable column-address"' in html


def test_visit_paid_checkbox_saves_and_shows_in_history(auth_client, visit):
    assert not visit.paid
    html = auth_client.get(reverse("visit_detail", args=[visit.pk])).content.decode()
    assert 'name="paid"' in html
    detail = reverse("property_detail", args=[visit.property_id])
    assert "Unpaid" in auth_client.get(detail).content.decode()

    resp = auth_client.post(
        reverse("visit_detail", args=[visit.pk]),
        {"visit_date": visit.visit_date.isoformat(), "filter_size": visit.filter_size, "paid": "on"},
    )
    assert resp.status_code == 302
    visit.refresh_from_db()
    assert visit.paid
    html = auth_client.get(detail).content.decode()
    assert ">Paid</span>" in html and "Unpaid" not in html


def test_paid_status_hidden_on_share_page(client, visit):
    token = visit.property.regenerate_share_token()
    html = client.get(reverse("share_property", args=[token])).content.decode()
    assert "Paid" not in html and "Unpaid" not in html


def test_property_list_paid_column_counts_all_unpaid_visits(auth_client, list_props):
    recent, old, never = list_props
    Visit.objects.create(property=old, visit_date=timezone.localdate() - timedelta(days=800))
    resp = auth_client.get(reverse("property_list"))
    rows = {p.address: p for p in resp.context["properties"]}
    assert (rows["300 Charlie St"].visit_count, rows["300 Charlie St"].unpaid_count) == (1, 0)
    assert (rows["100 Alpha St"].visit_count, rows["100 Alpha St"].unpaid_count) == (2, 2)
    assert rows["200 Bravo St"].visit_count == 0
    html = resp.content.decode()
    assert ">Paid</span>" in html and ">2 unpaid</span>" in html
