from django.contrib import admin

from .models import BrandingConfigModel


@admin.register(BrandingConfigModel)
class BrandingConfigAdmin(admin.ModelAdmin):
    list_display = ("college_name", "primary_color", "updated_at")
