import io

import pytest
from PIL import Image

from tracker.images import (
    ImageValidationError,
    process_image,
    sha256_of,
    taken_at_from_filename,
)
from tracker.tests.conftest import make_jpeg


def _size(data: bytes):
    return Image.open(io.BytesIO(data)).size


def test_taken_at_from_exif():
    result = process_image(make_jpeg(taken="2026:10:05 14:30:00"), "photo.jpg")
    local = result.taken_at.astimezone()
    assert (result.taken_at.year, result.taken_at.month, result.taken_at.day) == (2026, 10, 5)
    assert local is not None


def test_taken_at_from_filename():
    dt = taken_at_from_filename("IMG_20251012_093015.jpg")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second) == (2025, 10, 12, 9, 30, 15)
    assert taken_at_from_filename("random.jpg") is None


def test_exif_takes_precedence_over_filename():
    result = process_image(make_jpeg(taken="2026:01:02 03:04:05"), "IMG_20200101_000000.jpg")
    assert result.taken_at.year == 2026


def test_no_date_gives_none():
    assert process_image(make_jpeg(), "photo.jpg").taken_at is None


def test_exif_orientation_is_applied():
    data = make_jpeg(size=(80, 40), orientation=6)  # 6 = rotate 90° CW to display
    result = process_image(data, "a.jpg")
    assert _size(result.image_bytes) == (40, 80)
    stored = Image.open(io.BytesIO(result.image_bytes))
    assert stored.getexif().get(0x0112, 1) == 1


def test_client_oriented_upload_is_not_rotated_twice():
    data = make_jpeg(size=(80, 40), orientation=6)
    result = process_image(data, "a.jpg", client_oriented=True)
    assert _size(result.image_bytes) == (80, 40)
    assert Image.open(io.BytesIO(result.image_bytes)).getexif().get(0x0112, 1) == 1


def test_plain_jpeg_kept_byte_for_byte():
    data = make_jpeg()
    result = process_image(data, "a.jpg")
    assert result.image_bytes == data
    assert result.sha256 == sha256_of(data)


@pytest.mark.parametrize("fmt", ["PNG", "WEBP"])
def test_png_and_webp_converted_to_jpeg(fmt):
    result = process_image(make_jpeg(fmt=fmt), f"a.{fmt.lower()}")
    assert Image.open(io.BytesIO(result.image_bytes)).format == "JPEG"


def test_heic_converted_to_jpeg():
    pillow_heif = pytest.importorskip("pillow_heif")
    img = Image.new("RGB", (64, 48), (10, 120, 200))
    out = io.BytesIO()
    try:
        pillow_heif.from_pillow(img).save(out, format="HEIF")
    except Exception as exc:  # encoder not available in this build
        pytest.skip(f"HEIC encoder unavailable: {exc}")
    result = process_image(out.getvalue(), "a.heic")
    stored = Image.open(io.BytesIO(result.image_bytes))
    assert stored.format == "JPEG"
    assert stored.size == (64, 48)


def test_thumbnail_max_400px():
    result = process_image(make_jpeg(size=(1200, 900)), "a.jpg")
    assert max(_size(result.thumbnail_bytes)) == 400


def test_rejects_non_image():
    with pytest.raises(ImageValidationError):
        process_image(b"this is not an image at all", "evil.jpg")


def test_rejects_unsupported_format():
    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(out, format="GIF")
    with pytest.raises(ImageValidationError):
        process_image(out.getvalue(), "a.gif")


def test_rejects_over_20mb(settings):
    settings.MAX_UPLOAD_SIZE = 1000
    with pytest.raises(ImageValidationError, match="20 MB"):
        process_image(make_jpeg(size=(400, 400)) + b"\0" * 2000, "big.jpg")
