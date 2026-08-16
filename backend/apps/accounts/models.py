"""Account-domain models for admin role metadata."""

from django.contrib.auth.models import User
from django.db import models


class AdminProfile(models.Model):
    """Extends Django User with role and activation flags for admin operations."""
    ROLE_CHOICES = (
        ("super_admin", "Super Admin"),
        ("auditor", "Auditor"),
        ("operator", "Operator"),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="admin_profile")
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="operator")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.user.username} ({self.role})"
