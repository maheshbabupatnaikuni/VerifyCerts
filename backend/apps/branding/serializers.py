"""Serializer for branding configuration CRUD endpoint."""

from rest_framework import serializers

from .models import BrandingConfigModel


class BrandingConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = BrandingConfigModel
        fields = "__all__"
