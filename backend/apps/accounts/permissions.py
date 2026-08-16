"""Custom DRF permission classes used by secure admin endpoints."""

from rest_framework.permissions import BasePermission


class IsAdminStaff(BasePermission):
    """Allow only authenticated Django staff users."""
    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.user.is_staff)
