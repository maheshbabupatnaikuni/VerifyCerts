"""Inject branding settings into all templates as `branding`."""

from .models import BrandingConfigModel


def branding_config(request):
    """Fetch first branding row (project treats it as singleton config)."""
    config = BrandingConfigModel.objects.first()
    return {"branding": config}
