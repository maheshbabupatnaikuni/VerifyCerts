"""Certificate-domain API routes (upload, job polling, search, review actions, reports)."""

from django.urls import path

from .views import (
    ApproveCertificateView,
    CertificateDetailView,
    CertificateReportView,
    CertificateSearchView,
    PublicCertificateView,
    ResendCertificateEmailView,
    RevokeCertificateView,
    StudentCertificateListView,
    UniversityListCreateView,
    UploadBatchView,
    UploadCertificateView,
    UploadJobStatusView,
    VerificationLogsView,
)

urlpatterns = [
    path("universities/", UniversityListCreateView.as_view(), name="universities"),
    path("upload-certificate", UploadCertificateView.as_view(), name="upload-certificate"),
    path("upload-batch", UploadBatchView.as_view(), name="upload-batch"),
    path("upload-jobs/<str:token>", UploadJobStatusView.as_view(), name="upload-job-status"),
    path("certificate/<str:certificate_id>", CertificateDetailView.as_view(), name="certificate-detail"),
    path("certificate-search", CertificateSearchView.as_view(), name="certificate-search"),
    path("certificate-public/<str:certificate_id>", PublicCertificateView.as_view(), name="certificate-public"),
    path("student-certificates/<str:registration_number>", StudentCertificateListView.as_view(), name="student-certificates"),
    path("approve-certificate", ApproveCertificateView.as_view(), name="approve-certificate"),
    path("resend-certificate-email", ResendCertificateEmailView.as_view(), name="resend-certificate-email"),
    path("revoke-certificate", RevokeCertificateView.as_view(), name="revoke-certificate"),
    path("verification-logs", VerificationLogsView.as_view(), name="verification-logs"),
    path("reports/certificates", CertificateReportView.as_view(), name="certificate-report"),
]
