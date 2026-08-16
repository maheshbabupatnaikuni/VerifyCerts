from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import AdminProfile


class AccountAuthTests(TestCase):
    def setUp(self):
        self.password = "test-only-strong-pass-123"
        self.staff = User.objects.create_user(username="admin1", password=self.password, is_staff=True)
        AdminProfile.objects.create(user=self.staff, role="operator")
        self.client = Client()

    def test_safe_logout_get_logs_user_out(self):
        self.client.login(username="admin1", password=self.password)
        response = self.client.get(reverse("safe-logout"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)

    def test_safe_logout_post_logs_user_out(self):
        self.client.login(username="admin1", password=self.password)
        response = self.client.post(reverse("safe-logout"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response.url)


class AdminApiPermissionTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staffuser", password="test-only-pass-123", is_staff=True)
        AdminProfile.objects.create(user=self.staff, role="super_admin")
        self.non_staff = User.objects.create_user(username="normal", password="test-only-pass-123", is_staff=False)
        self.api = APIClient()

    def test_admin_list_requires_staff(self):
        self.api.force_authenticate(user=self.non_staff)
        response = self.api.get(reverse("admin-list"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_list_returns_profiles_for_staff(self):
        self.api.force_authenticate(user=self.staff)
        response = self.api.get(reverse("admin-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)

    def test_admin_create_requires_staff(self):
        self.api.force_authenticate(user=self.non_staff)
        response = self.api.post(
            reverse("admin-create"),
            {"username": "newadmin", "email": "new@example.com", "password": "test-only-new-pass-123", "role": "operator"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_create_creates_staff_user_and_profile(self):
        self.api.force_authenticate(user=self.staff)
        response = self.api.post(
            reverse("admin-create"),
            {
                "username": "newadmin",
                "email": "newadmin@example.com",
                "password": "test-only-new-pass-123",
                "role": "auditor",
                "is_active": True,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        created = User.objects.get(username="newadmin")
        self.assertTrue(created.is_staff)
        self.assertEqual(created.admin_profile.role, "auditor")
