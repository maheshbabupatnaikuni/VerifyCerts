"""Verification API route map."""

from django.urls import path

from .views import VerificationAnalyticsView, VerifyCertificateView

urlpatterns = [
    path("verify/<str:certificate_id>", VerifyCertificateView.as_view(), name="verify-certificate"),
    path("verification-analytics", VerificationAnalyticsView.as_view(), name="verification-analytics"),
]
