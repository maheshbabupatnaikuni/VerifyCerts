"""Models for verification audit events captured when verifiers check certificates."""

from django.db import models

from certificates.models import Certificate


class VerificationLog(models.Model):
    """Immutable log line recording who verified which certificate and the resulting decision."""
    RESULT_CHOICES = (
        ("valid", "Valid"),
        ("invalid", "Invalid"),
        ("tampered", "Tampered"),
        ("revoked", "Revoked"),
        ("expired", "Expired"),
        ("needs_review", "Needs Review"),
    )

    certificate = models.ForeignKey(Certificate, on_delete=models.CASCADE, related_name="verification_logs")
    verifier_ip = models.GenericIPAddressField()
    verification_time = models.DateTimeField(auto_now_add=True)
    result = models.CharField(max_length=20, choices=RESULT_CHOICES)

    def __str__(self) -> str:
        return f"{self.certificate.certificate_id} - {self.result}"
