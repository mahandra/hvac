from django.contrib import admin
from django.contrib.admin.views.main import ORDER_VAR, ChangeList
from django.db.models import F, Max, OrderBy
from django.utils.html import format_html

from .models import Photo, Property, Visit


class NullsLastOrderBy(OrderBy):
    """Ordering that keeps NULLs last when the admin reverses it (Django would flip them to first)."""

    def __init__(self, expression, descending=False):
        super().__init__(expression, descending=descending, nulls_last=True)

    def reverse_ordering(self):
        self.descending = not self.descending
        return self


class PropertyChangeList(ChangeList):
    def get_ordering_field_columns(self):
        # Django cannot map the default expression ordering to its column, so mark it explicitly.
        if ORDER_VAR not in self.params and "last_visit" in self.list_display:
            return {self.list_display.index("last_visit"): "asc"}
        return super().get_ordering_field_columns()


class VisitInline(admin.TabularInline):
    model = Visit
    extra = 0
    fields = ("visit_date", "outdoor_unit_cleaned", "filter_replaced", "filter_size", "note", "paid")
    show_change_link = True


@admin.register(Property)
class PropertyAdmin(admin.ModelAdmin):
    list_display = ("address", "property_type", "tenant_name", "last_visit", "is_active", "has_share_link")
    list_filter = ("property_type", "is_active")
    search_fields = ("address", "tenant_name", "note")
    readonly_fields = ("share_token",)
    inlines = [VisitInline]

    def get_changelist(self, request, **kwargs):
        return PropertyChangeList

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(last_visit_date=Max("visits__visit_date"))

    def get_ordering(self, request):
        # Visited properties first, earliest last visit on top; never-visited ones at the bottom.
        return (NullsLastOrderBy(F("last_visit_date")), "address")

    # Properties without visits stay at the bottom in both sort directions.
    @admin.display(description="Last visit", ordering=NullsLastOrderBy(F("last_visit_date")), empty_value="—")
    def last_visit(self, obj):
        return obj.last_visit_date

    @admin.display(boolean=True, description="Share link")
    def has_share_link(self, obj):
        return bool(obj.share_token)


class PhotoInline(admin.TabularInline):
    model = Photo
    extra = 0
    fields = ("preview", "original_filename", "taken_at", "sha256", "uploaded_by", "uploaded_at")
    readonly_fields = fields

    @admin.display(description="Preview")
    def preview(self, obj):
        if obj.thumbnail:
            return format_html('<img src="{}" alt="" height="80">', obj.thumbnail.url)
        return ""


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ("property", "visit_date", "outdoor_unit_cleaned", "filter_replaced", "filter_size", "paid")
    list_filter = ("paid", "outdoor_unit_cleaned", "filter_replaced")
    search_fields = ("property__address", "note")
    date_hierarchy = "visit_date"
    autocomplete_fields = ("property",)
    readonly_fields = ("created_by", "created_at", "updated_at")
    inlines = [PhotoInline]


@admin.register(Photo)
class PhotoAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "visit", "taken_at", "uploaded_at")
    search_fields = ("original_filename", "sha256", "visit__property__address")
    readonly_fields = ("uid", "sha256", "uploaded_by", "uploaded_at")
    raw_id_fields = ("visit",)
