from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Create or update the configured super admin user."

    def handle(self, *args, **options):
        username = getattr(settings, "SUPER_ADMIN_USERNAME", "admin")
        password = getattr(settings, "SUPER_ADMIN_PASSWORD", "test-only-local-admin-password")
        email = getattr(settings, "SUPER_ADMIN_EMAIL", "")

        user, created = User.objects.get_or_create(
            username=username,
            defaults={"email": email, "is_staff": True, "is_superuser": True, "is_active": True},
        )

        changed = []
        if user.email != email:
            user.email = email
            changed.append("email")
        if not user.is_staff:
            user.is_staff = True
            changed.append("is_staff")
        if not user.is_superuser:
            user.is_superuser = True
            changed.append("is_superuser")
        if not user.is_active:
            user.is_active = True
            changed.append("is_active")

        user.set_password(password)
        changed.append("password")
        user.save()

        if created:
            self.stdout.write(self.style.SUCCESS(f"Created super admin '{username}'"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Updated super admin '{username}' ({', '.join(changed)})"))

