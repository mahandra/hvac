import secrets
import uuid
from datetime import timedelta
from urllib.parse import quote_plus

from django.conf import settings
from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone


class Property(models.Model):
    class PropertyType(models.TextChoices):
        HOUSE = "house", "House"
        TOWNHOUSE = "townhouse", "Townhouse"
        CONDO = "condo", "Condo"

    address = models.CharField(max_length=300, unique=True, help_text="Full address as one line")
    property_type = models.CharField(
        max_length=20, choices=PropertyType.choices, default=PropertyType.HOUSE
    )
    tenant_name = models.CharField(max_length=200, blank=True)
    note = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    share_token = models.CharField(max_length=64, unique=True, null=True, blank=True, editable=False)

    class Meta:
        ordering = ["address"]
        verbose_name_plural = "properties"

    def __str__(self) -> str:
        return self.address

    def get_absolute_url(self) -> str:
        return reverse("property_detail", args=[self.pk])

    # --- share link -------------------------------------------------------
    @property
    def maps_url(self) -> str:
        """Google Maps search link for the address (opens the Maps app on Android); no API key needed."""
        return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(" ".join(self.address.split()))

    def regenerate_share_token(self) -> str:
        """Create (or replace) the share token. 43 URL-safe characters."""
        self.share_token = secrets.token_urlsafe(32)
        self.save(update_fields=["share_token"])
        return self.share_token

    def disable_share_token(self) -> None:
        self.share_token = None
        self.save(update_fields=["share_token"])

    # --- maintenance state ------------------------------------------------
    @property
    def latest_visit(self):
        return self.visits.order_by("-visit_date").first()

    def is_due(self, last_visit_date=None, today=None) -> bool:
        today = today or timezone.localdate()
        if last_visit_date is None:
            latest = self.latest_visit
            last_visit_date = latest.visit_date if latest else None
        if last_visit_date is None:
            return True
        return last_visit_date < today - timedelta(days=settings.MAINTENANCE_INTERVAL_DAYS)


class Visit(models.Model):
    property = models.ForeignKey(Property, on_delete=models.CASCADE, related_name="visits")
    visit_date = models.DateField(default=timezone.localdate)
    outdoor_unit_cleaned = models.BooleanField("Outdoor unit cleaned", default=False)
    filter_replaced = models.BooleanField("Indoor filter replaced", default=False)
    filter_size = models.CharField(max_length=50, blank=True, help_text='e.g. "20x25x1"')
    paid = models.BooleanField("Paid", default=False, help_text="The visit has been paid for.")
    note = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-visit_date"]
        constraints = [
            models.UniqueConstraint(fields=["property", "visit_date"], name="unique_visit_per_day"),
        ]

    def __str__(self) -> str:
        return f"{self.property.address} — {self.visit_date:%Y-%m-%d}"

    def get_absolute_url(self) -> str:
        return reverse("visit_detail", args=[self.pk])

    @classmethod
    def initial_filter_size(cls, prop: Property) -> str:
        """Filter size from the property's most recent visit (used to prefill a new visit)."""
        latest = prop.visits.exclude(filter_size="").order_by("-visit_date").first()
        return latest.filter_size if latest else ""


def _photo_dir(instance: "Photo") -> str:
    visit = instance.visit
    return f"properties/{visit.property_id}/{visit.visit_date:%Y-%m-%d}"


def photo_upload_to(instance: "Photo", filename: str) -> str:
    return f"{_photo_dir(instance)}/{instance.uid}.jpg"


def thumbnail_upload_to(instance: "Photo", filename: str) -> str:
    return f"{_photo_dir(instance)}/thumbs/{instance.uid}.jpg"


class Photo(models.Model):
    uid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name="photos")
    image = models.ImageField(upload_to=photo_upload_to, max_length=255)
    thumbnail = models.ImageField(upload_to=thumbnail_upload_to, max_length=255)
    original_filename = models.CharField(max_length=255, blank=True)
    taken_at = models.DateTimeField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, db_index=True)
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["taken_at", "uploaded_at", "id"]

    def __str__(self) -> str:
        return self.original_filename or str(self.uid)


@receiver(post_delete, sender=Photo)
def delete_photo_files(sender, instance: Photo, **kwargs):
    """Remove image files from disk when a photo row is deleted."""
    for field in (instance.image, instance.thumbnail):
        if field and field.name:
            field.storage.delete(field.name)
