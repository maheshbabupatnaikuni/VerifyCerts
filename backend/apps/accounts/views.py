from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.views import View
from rest_framework import generics

from .permissions import IsAdminStaff
from .serializers import AdminProfileSerializer, AdminUserSerializer


class AdminUserCreateView(generics.CreateAPIView):
    """Create staff/admin users through protected API."""
    queryset = User.objects.all()
    serializer_class = AdminUserSerializer
    permission_classes = [IsAdminStaff]


class AdminUserListView(generics.ListAPIView):
    """List admin profiles for admin-management screen."""
    serializer_class = AdminProfileSerializer
    permission_classes = [IsAdminStaff]

    def get_queryset(self):
        return User.objects.filter(is_staff=True, admin_profile__isnull=False).select_related("admin_profile")

    def list(self, request, *args, **kwargs):
        profiles = [user.admin_profile for user in self.get_queryset() if hasattr(user, "admin_profile")]
        serializer = self.get_serializer(profiles, many=True)
        from rest_framework.response import Response

        return Response(serializer.data)


class SafeLogoutView(View):
    """Allow logout via both GET and POST to avoid UX issues across different clients."""
    def _perform_logout(self, request):
        logout(request)
        return redirect(getattr(settings, "LOGOUT_REDIRECT_URL", "/accounts/login/"))

    def get(self, request, *args, **kwargs):
        return self._perform_logout(request)

    def post(self, request, *args, **kwargs):
        return self._perform_logout(request)
