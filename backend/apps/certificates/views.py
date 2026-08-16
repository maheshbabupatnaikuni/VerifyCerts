import csv
import os
import tempfile
import threading
import uuid
from datetime import datetime

from django.conf import settings
from django.db.models import Q
from django.core.files.base import ContentFile
from django.http import HttpResponse
from rest_framework import generics, status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminStaff
from blockchain_app.models import BlockchainRecord
from blockchain_app.services import BlockchainClient
from verification.models import VerificationLog
from .models import Certificate, University, UploadJob
from .serializers import (
    ApproveCertificateSerializer,
    BatchUploadSerializer,
    CertificateSerializer,
    CertificateUploadSerializer,
    ResendEmailSerializer,
    RevokeCertificateSerializer,
    UniversitySerializer,
)
from .services import derive_institution_email, process_certificate_file, send_certificate_notification


def _save_temp_file(file_obj) -> str:
    suffix = os.path.splitext(file_obj.name or "")[1] or ".bin"
    fd, tmp_path = tempfile.mkstemp(prefix="verifycerts_", suffix=suffix)
    with os.fdopen(fd, "wb") as handle:
        for chunk in file_obj.chunks():
            handle.write(chunk)
    return tmp_path


def _update_job(job_token: str, **kwargs):
    UploadJob.objects.filter(token=job_token).update(**kwargs)


def _build_blockchain_payload(certificate: Certificate) -> dict:
    """Return blockchain status snapshot used by upload-success UI."""
    latest = (
        BlockchainRecord.objects.filter(certificate_id=certificate.certificate_id, status="stored")
        .order_by("-created_at")
        .first()
    )
    tx_hash = latest.transaction_hash if latest else ""
    is_real_chain_tx = bool(tx_hash and not str(tx_hash).startswith("offchain-"))
    tx_base = (getattr(settings, "CHAIN_EXPLORER_TX_BASE", "") or "").rstrip("/")
    tx_link = f"{tx_base}/{tx_hash}" if tx_base and is_real_chain_tx else ""
    return {
        "anchored": bool(latest),
        "network": getattr(settings, "CHAIN_DISPLAY_NAME", "Configured Chain"),
        "transaction_hash": tx_hash or "",
        "on_chain_tx": is_real_chain_tx,
        "transaction_link": tx_link,
        "block_number": latest.block_number if latest else None,
    }


def _process_upload_job(job_token: str, file_specs: list[dict], university_id: int, kind: str):
    try:
        job = UploadJob.objects.get(token=job_token)
        _update_job(job_token, status="processing", progress=5, step="Upload accepted. Preparing files.")
        university = University.objects.get(id=university_id, is_active=True)

        created = []
        total = max(1, len(file_specs))
        for index, spec in enumerate(file_specs, start=1):
            base_progress = int(((index - 1) / total) * 70) + 10
            _update_job(
                job_token,
                step=f"Processing file {index}/{total}: OCR extraction and checks",
                progress=min(90, base_progress),
                processed_files=index - 1,
            )
            with open(spec["path"], "rb") as handle:
                content = handle.read()
            pseudo = ContentFile(content, name=spec["name"])
            cert = process_certificate_file(pseudo, university=university)
            created.append(cert)
            _update_job(
                job_token,
                step=f"Completed file {index}/{total}",
                progress=min(95, int((index / total) * 90)),
                processed_files=index,
            )

        if kind == "single" and created:
            payload = CertificateSerializer(created[0]).data
            payload["review_required"] = created[0].status == "needs_review"
            payload["notification_email"] = derive_institution_email(created[0].registration_number)
            ingestion_meta = (created[0].extracted_data or {}).get("ingestion_meta") or {}
            payload["duplicate_reused"] = bool(ingestion_meta.get("duplicate_reused"))
            payload["reused_certificate_id"] = ingestion_meta.get("reused_certificate_id") or created[0].certificate_id
            payload["blockchain"] = _build_blockchain_payload(created[0])
        else:
            reused_count = 0
            for c in created:
                meta = (c.extracted_data or {}).get("ingestion_meta") or {}
                if meta.get("duplicate_reused"):
                    reused_count += 1
            payload = {
                "processed_count": len(created),
                "review_required_count": sum(1 for c in created if c.status == "needs_review"),
                "duplicate_reused_count": reused_count,
                "notification_emails": [derive_institution_email(c.registration_number) for c in created],
                "certificates": CertificateSerializer(created, many=True).data,
                "blockchain": [
                    {"certificate_id": c.certificate_id, **_build_blockchain_payload(c)}
                    for c in created
                ],
            }

        _update_job(job_token, status="completed", progress=100, step="All tasks finished.", result=payload, processed_files=total)

    except Exception as exc:
        _update_job(job_token, status="failed", progress=100, step="Processing failed.", error_message=str(exc))
    finally:
        for spec in file_specs:
            try:
                os.remove(spec["path"])
            except OSError:
                pass


def resolve_university_or_error(university_id):
    """Resolve active university from provided ID or single-active default selection rule."""
    if university_id:
        uni = University.objects.filter(id=university_id, is_active=True).first()
        if not uni:
            return None, "Invalid university_id."
        return uni, None

    active_unis = list(University.objects.filter(is_active=True).order_by("id"))
    if len(active_unis) == 1:
        return active_unis[0], None
    if len(active_unis) == 0:
        return None, "No active university configured."
    return None, "Multiple active universities found. Please provide university_id."


class UniversityListCreateView(generics.ListCreateAPIView):
    """Admin API to list/create universities."""
    queryset = University.objects.all()
    serializer_class = UniversitySerializer
    permission_classes = [IsAdminStaff]


class UploadCertificateView(APIView):
    """Create async single-file upload job and return job token immediately."""
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAdminStaff]

    def post(self, request):
        serializer = CertificateUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        file_obj = serializer.validated_data["pdf_file"]
        if file_obj.size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            return Response({"detail": "File exceeds size limit."}, status=status.HTTP_400_BAD_REQUEST)

        university, err = resolve_university_or_error(serializer.validated_data.get("university_id"))
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        temp_path = _save_temp_file(file_obj)
        job_token = uuid.uuid4().hex
        UploadJob.objects.create(
            token=job_token,
            kind="single",
            status="queued",
            progress=0,
            step="Queued for processing",
            total_files=1,
        )
        worker = threading.Thread(
            target=_process_upload_job,
            args=(job_token, [{"name": file_obj.name, "path": temp_path}], university.id, "single"),
            daemon=True,
        )
        worker.start()
        return Response({"job_token": job_token, "status": "queued"}, status=status.HTTP_202_ACCEPTED)


class UploadBatchView(APIView):
    """Create async batch upload job and return job token immediately."""
    parser_classes = [MultiPartParser, FormParser]
    permission_classes = [IsAdminStaff]

    def post(self, request):
        serializer = BatchUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        university, err = resolve_university_or_error(serializer.validated_data.get("university_id"))
        if err:
            return Response({"detail": err}, status=status.HTTP_400_BAD_REQUEST)

        files = serializer.validated_data["files"]
        total_size = sum(file_obj.size for file_obj in files)
        if total_size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 * 50:
            return Response({"detail": "Batch exceeds configured limit."}, status=status.HTTP_400_BAD_REQUEST)

        specs = [{"name": file_obj.name, "path": _save_temp_file(file_obj)} for file_obj in files]
        job_token = uuid.uuid4().hex
        UploadJob.objects.create(
            token=job_token,
            kind="batch",
            status="queued",
            progress=0,
            step="Queued for processing",
            total_files=len(specs),
        )
        worker = threading.Thread(
            target=_process_upload_job,
            args=(job_token, specs, university.id, "batch"),
            daemon=True,
        )
        worker.start()
        return Response({"job_token": job_token, "status": "queued"}, status=status.HTTP_202_ACCEPTED)


class UploadJobStatusView(APIView):
    """Polling endpoint for frontend to track async upload progress and final result."""
    permission_classes = [IsAdminStaff]

    def get(self, request, token: str):
        job = UploadJob.objects.filter(token=token).first()
        if not job:
            return Response({"detail": "Upload job not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "job_token": job.token,
                "kind": job.kind,
                "status": job.status,
                "progress": job.progress,
                "step": job.step,
                "total_files": job.total_files,
                "processed_files": job.processed_files,
                "error_message": job.error_message,
                "result": job.result if job.status == "completed" else None,
            }
        )


class CertificateDetailView(generics.RetrieveAPIView):
    """Fetch one certificate by certificate_id for admin use."""
    queryset = Certificate.objects.all()
    serializer_class = CertificateSerializer
    permission_classes = [IsAdminStaff]
    lookup_field = "certificate_id"


class CertificateSearchView(generics.ListAPIView):
    """Search certificates by ID, student name, registration number, or serial number."""
    serializer_class = CertificateSerializer
    permission_classes = [IsAdminStaff]

    def get_queryset(self):
        query = self.request.query_params.get("q", "").strip()
        base_qs = Certificate.objects.select_related("university", "student").order_by("-created_at")
        if not query:
            return base_qs
        return base_qs.filter(
            Q(certificate_id__icontains=query)
            | Q(student_name__icontains=query)
            | Q(registration_number__icontains=query)
            | Q(certificate_serial_number__icontains=query)
        )


class ApproveCertificateView(APIView):
    """Approve a pending certificate and try blockchain anchoring."""
    permission_classes = [IsAdminStaff]

    def post(self, request):
        serializer = ApproveCertificateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        certificate = generics.get_object_or_404(Certificate, certificate_id=serializer.validated_data["certificate_id"])
        if certificate.status != "needs_review":
            return Response({"detail": "Certificate is not in review state."}, status=status.HTTP_400_BAD_REQUEST)

        certificate.status = "active"
        certificate.save(update_fields=["status"])
        try:
            BlockchainClient().store_certificate_hash(certificate.certificate_id, certificate.certificate_hash)
            return Response({"certificate_id": certificate.certificate_id, "status": certificate.status, "message": "Approved and anchored on blockchain."})
        except Exception as exc:
            extracted = certificate.extracted_data or {}
            warnings = extracted.get("confidence_warnings", [])
            warnings.append(f"Blockchain anchor failed during approve API: {exc.__class__.__name__}")
            extracted["confidence_warnings"] = warnings
            certificate.extracted_data = extracted
            certificate.status = "needs_review"
            certificate.save(update_fields=["status", "extracted_data"])
            return Response(
                {"certificate_id": certificate.certificate_id, "status": certificate.status, "detail": "Blockchain anchoring failed; certificate moved back to review."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )


class RevokeCertificateView(APIView):
    """Revoke certificate and mark corresponding blockchain/local revoke record."""
    permission_classes = [IsAdminStaff]

    def post(self, request):
        serializer = RevokeCertificateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        certificate = generics.get_object_or_404(Certificate, certificate_id=serializer.validated_data["certificate_id"])
        certificate.status = "revoked"
        certificate.save(update_fields=["status"])

        BlockchainClient().revoke_certificate(certificate.certificate_id)

        return Response(
            {
                "certificate_id": certificate.certificate_id,
                "status": certificate.status,
                "message": "Certificate revoked successfully.",
            },
            status=status.HTTP_200_OK,
        )


class VerificationLogsView(generics.ListAPIView):
    """Return recent verification activity logs for admin monitoring."""
    permission_classes = [IsAdminStaff]

    def get(self, request):
        logs = VerificationLog.objects.select_related("certificate").order_by("-verification_time")[:1000]
        data = [
            {
                "certificate_id": log.certificate.certificate_id,
                "verifier_ip": log.verifier_ip,
                "verification_time": log.verification_time,
                "result": log.result,
            }
            for log in logs
        ]
        return Response(data)


class ResendCertificateEmailView(APIView):
    """Resend generated certificate artifacts to derived/manual target email."""
    permission_classes = [IsAdminStaff]

    def post(self, request):
        serializer = ResendEmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        cert = generics.get_object_or_404(Certificate, certificate_id=serializer.validated_data["certificate_id"])
        target_email = serializer.validated_data.get("target_email") or None
        used_email = send_certificate_notification(cert, target_email=target_email)

        if not used_email:
            expected = derive_institution_email(cert.registration_number)
            return Response(
                {"detail": f"Could not derive target email. Expected format: {expected}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {"certificate_id": cert.certificate_id, "sent_to": used_email, "message": "Certificate email sent successfully."},
            status=status.HTTP_200_OK,
        )


class PublicCertificateView(generics.RetrieveAPIView):
    """Public read API for certificate payload by certificate_id."""
    queryset = Certificate.objects.all()
    serializer_class = CertificateSerializer
    permission_classes = [AllowAny]
    lookup_field = "certificate_id"


class StudentCertificateListView(generics.ListAPIView):
    """Public API to fetch certificates by registration number."""
    permission_classes = [AllowAny]
    serializer_class = CertificateSerializer

    def get_queryset(self):
        reg_no = self.kwargs["registration_number"]
        return Certificate.objects.filter(registration_number=reg_no).order_by("-created_at")


class CertificateReportView(APIView):
    """Export certificate data as CSV for reporting/review submissions."""
    permission_classes = [IsAdminStaff]

    def get(self, request):
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="certificate_report_{datetime.now().strftime("%Y%m%d%H%M%S")}.csv"'

        writer = csv.writer(response)
        writer.writerow(["Certificate ID", "Student", "Reg No", "Department", "Status", "Hash", "Created At"])

        for cert in Certificate.objects.order_by("-created_at"):
            writer.writerow(
                [
                    cert.certificate_id,
                    cert.student_name,
                    cert.registration_number,
                    cert.department,
                    cert.status,
                    cert.certificate_hash,
                    cert.created_at.isoformat(),
                ]
            )

        return response
