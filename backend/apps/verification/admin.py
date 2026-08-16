from django.contrib import admin

from .models import VerificationLog


@admin.register(VerificationLog)
class VerificationLogAdmin(admin.ModelAdmin):
    list_display = ("certificate", "verifier_ip", "verification_time", "result")
    search_fields = ("certificate__certificate_id", "verifier_ip")
    list_filter = ("result", "verification_time")
