"""Branding configuration model for logos, colors, and public text settings."""

from django.db import models


class BrandingConfigModel(models.Model):
    """Single-row branding config consumed by templates via context processor."""
    college_name = models.CharField(max_length=255, default="VerifyCerts")
    college_address = models.TextField(blank=True)
    verification_message = models.CharField(max_length=255, default="Certificate authenticity verified on blockchain")
    primary_color = models.CharField(max_length=7, default="#1f6feb")
    college_logo = models.ImageField(upload_to="branding/", blank=True)
    college_banner = models.ImageField(upload_to="branding/", blank=True)
    homepage_background = models.ImageField(upload_to="branding/", blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.college_name
