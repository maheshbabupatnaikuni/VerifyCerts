"""Accounts API routes (admin listing and creation)."""

from django.urls import path

from .views import AdminUserCreateView, AdminUserListView

urlpatterns = [
    path("admins/", AdminUserListView.as_view(), name="admin-list"),
    path("admins/create/", AdminUserCreateView.as_view(), name="admin-create"),
]
