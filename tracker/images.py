"""Image validation and processing: format checks, HEIC conversion, EXIF orientation,
thumbnails, content hashing and capture-time detection."""

import hashlib
import io
import re
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.utils import timezone
from PIL import Image, ImageOps, UnidentifiedImageError

ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF"}
THUMBNAIL_SIZE = 400
JPEG_QUALITY = 88

EXIF_IFD = 0x8769
TAG_DATETIME_ORIGINAL = 36867
TAG_DATETIME = 306
TAG_ORIENTATION = 0x0112

FILENAME_DATE_RE = re.compile(r"(?:IMG|PXL|VID)?_?(\d{8})_(\d{6})")

# Guard against decompression bombs while allowing large phone photos.
Image.MAX_IMAGE_PIXELS = 120_000_000


class ImageValidationError(Exception):
    """Raised when an uploaded file is not an acceptable image."""


@dataclass
class ProcessedImage:
    image_bytes: bytes  # JPEG to store
    thumbnail_bytes: bytes  # JPEG, max THUMBNAIL_SIZE px
    sha256: str  # hash of the file as received
    taken_at: datetime | None


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _parse_exif_datetime(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, bytes):
        value = value.decode("ascii", "ignore")
    value = str(value).strip().strip("\x00")
    try:
        naive = datetime.strptime(value[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None
    return timezone.make_aware(naive)


def taken_at_from_filename(filename: str) -> datetime | None:
    """Parse IMG_YYYYMMDD_HHMMSS style names, interpreting them in the local time zone."""
    match = FILENAME_DATE_RE.search(filename or "")
    if not match:
        return None
    try:
        naive = datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return timezone.make_aware(naive)


def taken_at_from_exif(img: Image.Image) -> datetime | None:
    try:
        exif = img.getexif()
    except Exception:  # noqa: BLE001 - malformed EXIF must not break uploads
        return None
    if not exif:
        return None
    original = exif.get_ifd(EXIF_IFD).get(TAG_DATETIME_ORIGINAL)
    return _parse_exif_datetime(original) or _parse_exif_datetime(exif.get(TAG_DATETIME))


def detect_taken_at(img: Image.Image, filename: str) -> datetime | None:
    return taken_at_from_exif(img) or taken_at_from_filename(filename)


def _open_image(data: bytes) -> Image.Image:
    try:
        probe = Image.open(io.BytesIO(data))
        fmt = probe.format
        probe.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageValidationError("File is not a valid image.") from exc
    if fmt not in ALLOWED_FORMATS:
        raise ImageValidationError(f"Unsupported image type ({fmt}). Use JPEG, PNG, WebP or HEIC.")
    # verify() leaves the object unusable; reopen and fully decode.
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ImageValidationError("Image data is corrupted.") from exc
    return img


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        background = Image.new("RGB", img.size, (255, 255, 255))
        rgba = img.convert("RGBA")
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB") if img.mode != "RGB" else img


def _encode_jpeg(img: Image.Image, quality: int = JPEG_QUALITY, exif: bytes | None = None) -> bytes:
    out = io.BytesIO()
    kwargs = {"format": "JPEG", "quality": quality, "optimize": True}
    if exif:
        kwargs["exif"] = exif
    img.save(out, **kwargs)
    return out.getvalue()


def process_image(data: bytes, filename: str, client_oriented: bool = False) -> ProcessedImage:
    """Validate and normalise an image.

    ``client_oriented`` means the browser already drew the pixels upright while resizing
    (Dropzone keeps the original EXIF, including the Orientation tag), so the tag must be
    dropped rather than applied a second time.
    """
    if len(data) > settings.MAX_UPLOAD_SIZE:
        raise ImageValidationError("File is larger than 20 MB.")
    if not data:
        raise ImageValidationError("File is empty.")

    img = _open_image(data)
    source_format = img.format
    taken_at = detect_taken_at(img, filename)

    exif = img.getexif()
    orientation = exif.get(TAG_ORIENTATION, 1) if exif else 1
    needs_rotation = orientation not in (1, None) and not client_oriented

    if needs_rotation:
        img = ImageOps.exif_transpose(img)

    if source_format == "JPEG" and orientation in (1, None) and img.mode == "RGB":
        # Already upright, already JPEG: keep the original bytes untouched.
        image_bytes = data
    else:
        if exif and TAG_ORIENTATION in exif:
            exif[TAG_ORIENTATION] = 1
        image_bytes = _encode_jpeg(_to_rgb(img), exif=exif.tobytes() if exif else None)

    thumb = _to_rgb(img).copy()
    thumb.thumbnail((THUMBNAIL_SIZE, THUMBNAIL_SIZE), Image.Resampling.LANCZOS)
    thumbnail_bytes = _encode_jpeg(thumb, quality=80)

    return ProcessedImage(
        image_bytes=image_bytes,
        thumbnail_bytes=thumbnail_bytes,
        sha256=sha256_of(data),
        taken_at=taken_at,
    )


def downscale_for_pdf(path: str, max_px: int = 900, quality: int = 70) -> bytes:
    """Return a small JPEG version of a stored image for embedding in the PDF."""
    with Image.open(path) as img:
        img = _to_rgb(img)
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
        return _encode_jpeg(img, quality=quality)
