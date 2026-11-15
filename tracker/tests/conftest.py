import io
from datetime import date

import pytest
from django.contrib.auth import get_user_model
from PIL import Image

from tracker.models import Property, Visit


@pytest.fixture(autouse=True)
def _test_settings(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
    settings.MAINTENANCE_INTERVAL_DAYS = 180


def make_jpeg(color=(200, 30, 30), size=(64, 48), taken: str | None = None, orientation: int | None = None,
              fmt="JPEG") -> bytes:
    """Build a small image; optionally with EXIF DateTimeOriginal and Orientation."""
    img = Image.new("RGB", size, color)
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[36867] = taken
    if orientation:
        exif[0x0112] = orientation
    out = io.BytesIO()
    kwargs = {"format": fmt}
    if taken or orientation:
        kwargs["exif"] = exif.tobytes()
    img.save(out, **kwargs)
    return out.getvalue()


@pytest.fixture
def jpeg():
    return make_jpeg


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tech", password="pw-123456")


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def prop(db):
    return Property.objects.create(address="2101 Maple Ridge Dr., Springfield", tenant_name="Jane Doe")


@pytest.fixture
def other_prop(db):
    return Property.objects.create(
        address="7070 Willow Bend Circle #305, Riverton", property_type=Property.PropertyType.CONDO
    )


@pytest.fixture
def visit(prop):
    return Visit.objects.create(property=prop, visit_date=date(2026, 10, 1), filter_size="20x25x1")
