from django import template
from django.urls import reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def photo_src(context, photo, kind="thumb"):
    """URL of a photo file: token-scoped on share pages, login-protected media otherwise."""
    token = context.get("share_token")
    if token:
        return reverse("share_photo", args=[token, photo.pk, kind])
    field = photo.thumbnail if kind == "thumb" else photo.image
    return field.url


@register.filter
def yesno_icon(value):
    return "✔" if value else "✘"
