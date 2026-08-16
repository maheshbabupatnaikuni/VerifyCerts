"""Branding API route map."""

from django.urls import path

from .views import BrandingConfigView

urlpatterns = [
    path("config/", BrandingConfigView.as_view(), name="branding-config"),
]
