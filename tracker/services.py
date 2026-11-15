"""Photo storage with duplicate detection, shared by the upload view and the import command."""

from dataclasses import dataclass, field

from django.core.files.base import ContentFile
from django.db import transaction

from .images import ProcessedImage, process_image, sha256_of
from .models import Photo, Visit


@dataclass
class AddPhotoResult:
    status: str  # "created" | "skipped"
    photo: Photo | None = None
    # Addresses of *other* properties that already hold the same file.
    other_addresses: list[str] = field(default_factory=list)


def addresses_with_hash(sha256: str, exclude_property_id: int) -> list[str]:
    qs = (
        Photo.objects.filter(sha256=sha256)
        .exclude(visit__property_id=exclude_property_id)
        .values_list("visit__property__address", flat=True)
        .distinct()
    )
    return sorted(set(qs))


def save_photo(visit: Visit, processed: ProcessedImage, original_filename: str, user=None) -> Photo:
    photo = Photo(
        visit=visit,
        original_filename=original_filename[:255],
        taken_at=processed.taken_at,
        sha256=processed.sha256,
        uploaded_by=user if (user is not None and user.is_authenticated) else None,
    )
    with transaction.atomic():
        photo.image.save("image.jpg", ContentFile(processed.image_bytes), save=False)
        photo.thumbnail.save("thumb.jpg", ContentFile(processed.thumbnail_bytes), save=False)
        photo.save()
    return photo


def add_uploaded_photo(
    visit: Visit, data: bytes, filename: str, user=None, client_oriented: bool = False
) -> AddPhotoResult:
    """Handle one uploaded file.

    Same hash in the same visit -> skipped silently.
    Same hash under another property -> stored, but the other addresses are reported.
    Raises ImageValidationError for invalid files.
    """
    digest = sha256_of(data)
    if Photo.objects.filter(visit=visit, sha256=digest).exists():
        return AddPhotoResult(status="skipped")
    processed = process_image(data, filename, client_oriented=client_oriented)
    others = addresses_with_hash(processed.sha256, visit.property_id)
    photo = save_photo(visit, processed, filename, user=user)
    return AddPhotoResult(status="created", photo=photo, other_addresses=others)
