"""Project-level URL router combining auth, APIs, dashboard, and public verification routes."""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path, re_path
from django.views.generic import RedirectView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.views import SafeLogoutView
from verification.views import (
    download_verification_report,
    public_verify_by_tx_page,
    public_verify_page,
    verify_portal_page,
    verify_uploaded_pdf,
)

urlpatterns = [
    re_path(r"^admin(?:/.*)?$", RedirectView.as_view(url="/accounts/login/", permanent=False)),
    path("logout-now/", SafeLogoutView.as_view(), name="logout-now"),
    path("accounts/logout/", SafeLogoutView.as_view(), name="safe-logout"),
    path("accounts/", include("django.contrib.auth.urls")),
    path("accounts/profile/", RedirectView.as_view(url="/", permanent=False)),
    path("api/auth/token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/accounts/", include("accounts.urls")),
    path("api/", include("certificates.urls")),
    path("api/", include("blockchain_app.urls")),
    path("api/", include("verification.urls")),
    path("api/branding/", include("branding.urls")),
    path("", include("branding.dashboard_urls")),
    path("verify/", verify_portal_page, name="verify_portal_page"),
    path("verify/tx/<str:tx_hash>/", public_verify_by_tx_page, name="public_verify_by_tx_page"),
    path("verify/upload-pdf/", verify_uploaded_pdf, name="verify_uploaded_pdf"),
    path("verify/report/<str:certificate_id>/", download_verification_report, name="download_verification_report"),
    path("verify/<str:certificate_id>/", public_verify_page, name="public_verify_page"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
