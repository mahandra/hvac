from django.urls import path

from . import views

urlpatterns = [
    path("", views.property_list, name="property_list"),
    path("properties/<int:pk>/", views.property_detail, name="property_detail"),
    path("properties/<int:pk>/pdf/", views.property_pdf, name="property_pdf"),
    path("properties/<int:pk>/share/", views.share_manage, name="share_manage"),
    path("properties/<int:pk>/visits/new/", views.visit_new, name="visit_new"),
    path("visits/<int:pk>/", views.visit_detail, name="visit_detail"),
    path("visits/<int:pk>/delete/", views.visit_delete, name="visit_delete"),
    path("visits/<int:pk>/photos/", views.visit_photo_grid, name="visit_photo_grid"),
    path("visits/<int:pk>/upload/", views.photo_upload, name="photo_upload"),
    path("photos/<int:pk>/delete/", views.photo_delete, name="photo_delete"),
    path("media/<path:path>", views.media_file, name="media_file"),
    path("share/<str:token>/", views.share_property, name="share_property"),
    path("share/<str:token>/pdf/", views.share_pdf, name="share_pdf"),
    path(
        "share/<str:token>/photos/<int:pk>/<str:kind>/", views.share_photo, name="share_photo"
    ),
]
