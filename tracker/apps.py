from django.apps import AppConfig


class TrackerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "tracker"
    verbose_name = "AC maintenance"

    def ready(self):
        # Enable HEIC/HEIF decoding in Pillow.
        from pillow_heif import register_heif_opener

        register_heif_opener()
