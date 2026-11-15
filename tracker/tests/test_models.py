from datetime import date, timedelta

import pytest
from django.core.files.base import ContentFile
from django.db import IntegrityError
from django.utils import timezone

from tracker.models import Photo, Property, Visit

pytestmark = pytest.mark.django_db


def test_share_token_generate_regenerate_disable(prop):
    assert prop.share_token is None
    first = prop.regenerate_share_token()
    assert len(first) >= 32
    second = prop.regenerate_share_token()
    assert second != first
    prop.refresh_from_db()
    assert prop.share_token == second
    prop.disable_share_token()
    prop.refresh_from_db()
    assert prop.share_token is None


def test_visit_unique_per_property_and_date(prop):
    Visit.objects.create(property=prop, visit_date=date(2026, 1, 1))
    with pytest.raises(IntegrityError):
        Visit.objects.create(property=prop, visit_date=date(2026, 1, 1))


def test_visit_ordering_newest_first(prop):
    Visit.objects.create(property=prop, visit_date=date(2025, 1, 1))
    Visit.objects.create(property=prop, visit_date=date(2026, 1, 1))
    assert [v.visit_date.year for v in prop.visits.all()] == [2026, 2025]


def test_visit_date_defaults_to_today(prop):
    v = Visit.objects.create(property=prop)
    assert v.visit_date == timezone.localdate()


def test_initial_filter_size_from_latest_visit(prop):
    assert Visit.initial_filter_size(prop) == ""
    Visit.objects.create(property=prop, visit_date=date(2025, 1, 1), filter_size="16x20x1")
    Visit.objects.create(property=prop, visit_date=date(2026, 1, 1), filter_size="20x25x1")
    assert Visit.initial_filter_size(prop) == "20x25x1"


def test_is_due(prop):
    today = timezone.localdate()
    assert prop.is_due()  # no visits at all
    Visit.objects.create(property=prop, visit_date=today - timedelta(days=200))
    assert prop.is_due()
    Visit.objects.create(property=prop, visit_date=today - timedelta(days=10))
    assert not prop.is_due()


def test_photo_paths_and_file_cleanup(visit, jpeg):
    photo = Photo(visit=visit, sha256="x" * 64, original_filename="a.jpg")
    photo.image.save("whatever.png", ContentFile(jpeg()), save=False)
    photo.thumbnail.save("t.jpg", ContentFile(jpeg()), save=False)
    photo.save()
    assert photo.image.name == f"properties/{visit.property_id}/2026-10-01/{photo.uid}.jpg"
    assert photo.thumbnail.name.startswith(f"properties/{visit.property_id}/2026-10-01/thumbs/")
    storage = photo.image.storage
    image_name, thumb_name = photo.image.name, photo.thumbnail.name
    assert storage.exists(image_name)
    photo.delete()
    assert not storage.exists(image_name)
    assert not storage.exists(thumb_name)


def test_property_type_choices():
    assert {c for c, _ in Property.PropertyType.choices} == {"house", "townhouse", "condo"}


def test_maps_url_encodes_address():
    prop = Property(address="123 Main St,\n Apt 4 & Co")
    assert prop.maps_url == (
        "https://www.google.com/maps/search/?api=1&query=123+Main+St%2C+Apt+4+%26+Co"
    )
