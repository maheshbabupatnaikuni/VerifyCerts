"""Serializers for creating/listing admin users and admin profiles."""

from django.contrib.auth.models import User
from rest_framework import serializers

from .models import AdminProfile


class AdminUserSerializer(serializers.ModelSerializer):
    """Create a Django staff user and linked AdminProfile in one API call."""
    role = serializers.CharField(source="admin_profile.role", required=False)

    class Meta:
        model = User
        fields = ["id", "username", "email", "password", "role", "is_active"]
        extra_kwargs = {"password": {"write_only": True}}

    def create(self, validated_data):
        # Pull nested profile data and write user+profile transactionally at serializer level.
        profile_data = validated_data.pop("admin_profile", {})
        password = validated_data.pop("password")
        user = User.objects.create_user(password=password, is_staff=True, **validated_data)
        AdminProfile.objects.create(user=user, role=profile_data.get("role", "operator"))
        return user


class AdminProfileSerializer(serializers.ModelSerializer):
    """Read-only admin profile serializer used for admin list screen."""
    username = serializers.CharField(source="user.username", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)

    class Meta:
        model = AdminProfile
        fields = ["id", "username", "email", "role", "is_active", "created_at"]
