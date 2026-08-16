from django.contrib import admin

from .models import Certificate, Student, University


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "prefix", "code", "is_active")
    search_fields = ("name", "prefix", "code")


@admin.register(Student)
class StudentAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "registration_number", "department", "year", "is_verified")
    search_fields = ("name", "registration_number")
    list_filter = ("is_verified", "department", "year")


@admin.register(Certificate)
class CertificateAdmin(admin.ModelAdmin):
    list_display = ("certificate_id", "student_name", "registration_number", "status", "confidence_score", "created_at")
    search_fields = ("certificate_id", "student_name", "registration_number", "certificate_serial_number")
    list_filter = ("status", "graduation_year", "department")
