import os

"""Core certificate-domain models used across upload, review, verification, and reporting."""

from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


ALLOWED_CERT_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def validate_certificate_input(file_obj):
    ext = os.path.splitext((file_obj.name or "").lower())[1]
    if ext not in ALLOWED_CERT_EXTENSIONS:
        raise ValidationError("Allowed files: PDF, PNG, JPG, JPEG, WEBP.")


def validate_pdf(file_obj):
    # Backward compatibility for old migrations that import validate_pdf.
    validate_certificate_input(file_obj)


class University(models.Model):
    """Institution master record (prefix/code used in certificate ID generation)."""
    name = models.CharField(max_length=255)
    prefix = models.CharField(max_length=20, unique=True)
    code = models.CharField(max_length=20, unique=True)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self) -> str:
        return f"{self.prefix} - {self.name}"


class Student(models.Model):
    """Student master profile; can be strengthened from OCR over time."""
    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name="students")
    name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=120, unique=True)
    department = models.CharField(max_length=120)
    year = models.PositiveIntegerField()
    email = models.EmailField(blank=True)
    is_verified = models.BooleanField(default=False)

    def __str__(self) -> str:
        return f"{self.name} ({self.registration_number})"


class Certificate(models.Model):
    """Issued certificate record with canonical hash, artifacts, and verification status."""
    STATUS_CHOICES = (
        ("active", "Active"),
        ("needs_review", "Needs Review"),
        ("revoked", "Revoked"),
        ("expired", "Expired"),
    )

    university = models.ForeignKey(University, on_delete=models.PROTECT, related_name="certificates")
    student = models.ForeignKey(Student, on_delete=models.SET_NULL, related_name="certificates", null=True, blank=True)
    certificate_id = models.CharField(max_length=64, unique=True)
    student_name = models.CharField(max_length=255)
    registration_number = models.CharField(max_length=120)
    course = models.CharField(max_length=255)
    department = models.CharField(max_length=120)
    university_name = models.CharField(max_length=255)
    certificate_serial_number = models.CharField(max_length=120)
    issue_date = models.DateField(null=True, blank=True)
    graduation_year = models.PositiveIntegerField()
    certificate_hash = models.CharField(max_length=64, db_index=True)
    confidence_score = models.FloatField(default=0.0)
    qr_code = models.ImageField(upload_to="qrcodes/", blank=True)
    pdf_file = models.FileField(upload_to="certificates/", validators=[validate_certificate_input])
    college_certificate_pdf = models.FileField(upload_to="issued/college/", validators=[validate_certificate_input], blank=True)
    combined_certificate_pdf = models.FileField(upload_to="issued/combined/", validators=[validate_certificate_input], blank=True)
    ipfs_cid = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="needs_review")
    expiry_date = models.DateField(null=True, blank=True)
    extracted_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["certificate_id", "status"])]

    def __str__(self) -> str:
        return self.certificate_id


class CertificateEvent(models.Model):
    EVENT_CHOICES = (
        ("uploaded", "Uploaded"),
        ("ocr_extracted", "OCR Extracted"),
        ("auto_reprocess", "Auto Reprocess"),
        ("approved", "Approved"),
        ("anchored", "Anchored On Blockchain"),
        ("email_sent", "Email Sent"),
        ("email_failed", "Email Failed"),
        ("rejected", "Rejected"),
    )

    certificate = models.ForeignKey(Certificate, on_delete=models.CASCADE, related_name="events")
    event_type = models.CharField(max_length=32, choices=EVENT_CHOICES)
    message = models.CharField(max_length=255, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["event_type", "created_at"])]
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.certificate.certificate_id}::{self.event_type}"


class UploadJob(models.Model):
    STATUS_CHOICES = (
        ("queued", "Queued"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    )

    token = models.CharField(max_length=64, unique=True, db_index=True)
    kind = models.CharField(max_length=16, default="single")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="queued")
    progress = models.PositiveSmallIntegerField(default=0)
    step = models.CharField(max_length=120, blank=True)
    total_files = models.PositiveIntegerField(default=1)
    processed_files = models.PositiveIntegerField(default=0)
    result = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"])]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.token} ({self.status})"
