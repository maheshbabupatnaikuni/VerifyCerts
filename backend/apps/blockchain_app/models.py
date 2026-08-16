"""Persistence models for blockchain evidence records."""

from django.db import models


class BlockchainRecord(models.Model):
    """
    Stores local copy of blockchain anchoring evidence.

    Even when chain is unavailable, app can preserve a traceable record format.
    """
    STATUS_CHOICES = (
        ("stored", "Stored"),
        ("revoked", "Revoked"),
    )

    certificate_id = models.CharField(max_length=64, db_index=True)
    transaction_hash = models.CharField(max_length=200, unique=True)
    block_number = models.BigIntegerField()
    hash = models.CharField(max_length=64)
    issuer_address = models.CharField(max_length=120)
    timestamp = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="stored")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["certificate_id", "status"])]

    def __str__(self) -> str:
        return f"{self.certificate_id} - {self.transaction_hash}"
