from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Q
from django.conf import settings
from django.utils import timezone
from datetime import date
from rest_framework import generics

from accounts.permissions import IsAdminStaff
from blockchain_app.models import BlockchainRecord
from blockchain_app.services import BlockchainClient
from certificates.models import Certificate, University
from certificates.services import (
    compute_confidence,
    derive_institution_email,
    generate_hash,
    generate_student_documents,
    record_certificate_event,
    reprocess_existing_certificate,
    send_certificate_notification,
)
from verification.models import VerificationLog
from .models import BrandingConfigModel
from .serializers import BrandingConfigSerializer
from certificates.models import CertificateEvent


class BrandingConfigView(generics.RetrieveUpdateAPIView):
    serializer_class = BrandingConfigSerializer
    permission_classes = [IsAdminStaff]

    def get_object(self):
        config, _ = BrandingConfigModel.objects.get_or_create(id=1)
        return config


def _require_staff(request):
    if not request.user.is_staff:
        return HttpResponseForbidden("Staff access required.")
    return None


def _parse_iso_date(value: str):
    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _extract_email_status(certificate: Certificate):
    extracted = certificate.extracted_data if isinstance(certificate.extracted_data, dict) else {}
    history = extracted.get("email_delivery_history", [])
    return {
        "last_status": extracted.get("email_last_status", "unknown"),
        "last_recipient": extracted.get("email_last_recipient", "-"),
        "last_at": extracted.get("email_last_at"),
        "retry_count": extracted.get("email_retry_count", 0),
        "history": history[-5:],
    }


def _is_archived_duplicate(certificate: Certificate) -> bool:
    extracted = certificate.extracted_data if isinstance(certificate.extracted_data, dict) else {}
    return bool(extracted.get("archived_duplicate"))


def _group_visible_certificates(certificates_iterable):
    """
    Group records by canonical hash so duplicate uploads do not inflate dashboard/search counts.
    """
    cert_list = [c for c in certificates_iterable if not _is_archived_duplicate(c)]
    status_rank = {"active": 0, "needs_review": 1, "expired": 2, "revoked": 3}
    grouped = {}
    for cert in cert_list:
        key = cert.certificate_hash or f"__nohash__{cert.id}"
        grouped.setdefault(key, []).append(cert)

    rows = []
    for _hash, items in grouped.items():
        ordered = sorted(
            items,
            key=lambda c: (
                status_rank.get(c.status, 9),
                -(c.created_at.timestamp() if c.created_at else 0),
            ),
        )
        rows.append(
            {
                "primary": ordered[0],
                "duplicates": ordered[1:],
                "duplicate_count": len(ordered) - 1,
                "email_status": _extract_email_status(ordered[0]),
            }
        )
    rows.sort(key=lambda row: -(row["primary"].created_at.timestamp() if row["primary"].created_at else 0))
    return rows


def dashboard_home(request):
    """
    Main landing/dashboard controller.

    - Public users: verification-first homepage experience.
    - Admin users: operational dashboard with health/status/security insights.
    """
    context = {"is_admin": request.user.is_authenticated and request.user.is_staff}
    if context["is_admin"]:
        grouped = _group_visible_certificates(Certificate.objects.order_by("-created_at"))
        primary_certs = [row["primary"] for row in grouped]
        context.update(
            {
                "cert_count": len(primary_certs),
                "active_count": sum(1 for cert in primary_certs if cert.status == "active"),
                "verification_count": VerificationLog.objects.count(),
                "review_count": sum(1 for cert in primary_certs if cert.status == "needs_review"),
                "latest_certificates": primary_certs[:10],
                "assistant_context_message": "Dashboard loaded. Monitoring certificates and system health.",
            }
        )
    else:
        context["assistant_context_message"] = "Awaiting QR, ID, or PDF evidence"
    return render(request, "dashboard/home.html", context)


@login_required
def dashboard_upload(request):
    """Render upload workspace and pass contextual assistant message."""
    active_unis = University.objects.filter(is_active=True).order_by("id")
    default_university = active_unis.first() if active_unis.count() == 1 else None
    chain_client = BlockchainClient()
    chain_online = bool(
        chain_client.web3
        and chain_client.web3.is_connected()
        and chain_client.contract_address
        and chain_client.contract_abi
    )
    chain_status = {
        "online": chain_online,
        "network": settings.CHAIN_DISPLAY_NAME,
    }
    return render(
        request,
        "dashboard/upload.html",
        {
            "default_university": default_university,
            "active_universities": active_unis,
            "chain_status": chain_status,
            "assistant_context_message": "Ready to inspect new certificates",
        },
    )


@login_required
def dashboard_search(request):
    """
    Search and filter certificates; also supports resend-email action from search grid.
    """
    staff_error = _require_staff(request)
    if staff_error:
        return staff_error

    if request.method == "POST":
        action = request.POST.get("action", "resend_email").strip()

        certificate_id = request.POST.get("certificate_id", "").strip()
        cert = Certificate.objects.filter(certificate_id=certificate_id).first()
        if not cert:
            messages.error(request, "Certificate not found.")
            return redirect("dashboard-search")

        if action == "archive_duplicates":
            duplicate_qs = Certificate.objects.filter(certificate_hash=cert.certificate_hash).exclude(id=cert.id)
            archived_count = 0
            for dup in duplicate_qs:
                extracted = dup.extracted_data if isinstance(dup.extracted_data, dict) else {}
                extracted["archived_duplicate"] = True
                extracted["archived_duplicate_at"] = timezone.now().isoformat()
                extracted["archived_under"] = cert.certificate_id
                warnings = extracted.get("confidence_warnings", [])
                if "Archived as duplicate record by admin." not in warnings:
                    warnings.append("Archived as duplicate record by admin.")
                extracted["confidence_warnings"] = warnings
                dup.extracted_data = extracted
                if dup.status != "revoked":
                    dup.status = "revoked"
                dup.save(update_fields=["status", "extracted_data"])
                record_certificate_event(
                    dup,
                    "rejected",
                    f"Archived as duplicate under {cert.certificate_id}",
                    {"primary_certificate_id": cert.certificate_id},
                )
                archived_count += 1

            if archived_count:
                messages.success(request, f"Archived {archived_count} duplicate record(s) under {cert.certificate_id}.")
            else:
                messages.info(request, "No duplicates found to archive for this certificate.")
            return redirect(f"/dashboard/search/?q={cert.certificate_id}")

        target_email = request.POST.get("target_email", "").strip()
        used_email = send_certificate_notification(cert, target_email=target_email or None)
        if used_email:
            messages.success(request, f"Certificate email sent to {used_email} for {cert.certificate_id}.")
        else:
            default_email = derive_institution_email(cert.registration_number)
            messages.error(request, f"Could not derive target email. Expected format: {default_email}")
        return redirect(f"/dashboard/search/?q={cert.certificate_id}")

    query = request.GET.get("q", "").strip()
    year = request.GET.get("year", "").strip()
    department = request.GET.get("department", "").strip()
    status = request.GET.get("status", "").strip()
    date_from = request.GET.get("date_from", "").strip()
    date_to = request.GET.get("date_to", "").strip()
    recent = request.GET.get("recent", "").strip()

    certificates = Certificate.objects.order_by("-created_at")
    if query:
        certificates = certificates.filter(
            Q(certificate_id__icontains=query)
            | Q(student_name__icontains=query)
            | Q(registration_number__icontains=query)
            | Q(course__icontains=query)
            | Q(department__icontains=query)
        )
    if year.isdigit():
        certificates = certificates.filter(graduation_year=int(year))
    if department:
        certificates = certificates.filter(department__icontains=department)
    if status:
        certificates = certificates.filter(status=status)
    if date_from:
        parsed_from = _parse_iso_date(date_from)
        if parsed_from:
            certificates = certificates.filter(created_at__date__gte=parsed_from)
    if date_to:
        parsed_to = _parse_iso_date(date_to)
        if parsed_to:
            certificates = certificates.filter(created_at__date__lte=parsed_to)

    if recent == "1":
        certificates = certificates[:25]
    grouped_certificates = _group_visible_certificates(certificates)
    cert_list = [row["primary"] for row in grouped_certificates]

    departments = (
        Certificate.objects.values_list("department", flat=True)
        .exclude(department__isnull=True)
        .exclude(department__exact="")
        .distinct()
        .order_by("department")
    )
    return render(
        request,
        "dashboard/search.html",
        {
            "query": query,
            "certificates": certificates,
            "grouped_certificates": grouped_certificates,
            "raw_result_count": len(cert_list),
            "group_result_count": len(grouped_certificates),
            "year": year,
            "department": department,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "recent": recent,
            "departments": departments,
            "assistant_context_message": "Search and filter certificates quickly.",
        },
    )


@login_required
def dashboard_logs(request):
    """Recent verification logs view for admin audit tracking."""
    logs = VerificationLog.objects.select_related("certificate").order_by("-verification_time")[:200]
    admin_actions = CertificateEvent.objects.select_related("certificate").order_by("-created_at")[:200]
    return render(
        request,
        "dashboard/logs.html",
        {
            "logs": logs,
            "admin_actions": admin_actions,
            "assistant_context_message": "Verification logs loaded.",
        },
    )


@login_required
def dashboard_blockchain(request):
    """Blockchain record table view with tx/hash history."""
    records = (
        BlockchainRecord.objects.filter(status="stored", transaction_hash__startswith="0x")
        .order_by("-timestamp")[:200]
    )
    return render(
        request,
        "dashboard/blockchain.html",
        {"records": records, "assistant_context_message": "Blockchain records and transaction evidence loaded."},
    )


@login_required
def dashboard_admins(request):
    """Admin accounts overview page."""
    from django.contrib.auth.models import User

    admins = User.objects.filter(is_staff=True).order_by("-date_joined")
    return render(request, "dashboard/admins.html", {"admins": admins, "assistant_context_message": "Admin accounts overview."})


@login_required
def dashboard_review(request):
    """
    Manual review workbench.

    Admin can:
    - reprocess OCR
    - edit extracted fields
    - approve (and attempt chain anchor)
    - reject/revoke
    - resend certificate email
    """
    staff_error = _require_staff(request)
    if staff_error:
        return staff_error

    if request.method == "POST":
        action = request.POST.get("action", "").strip().lower()
        if action == "bulk_approve":
            selected_ids = request.POST.getlist("selected_ids")
            if not selected_ids:
                messages.info(request, "No certificates selected for bulk approval.")
                return redirect("dashboard-review")
            approved_count = 0
            failed_count = 0
            for cid in selected_ids:
                cert = Certificate.objects.filter(certificate_id=cid, status="needs_review").first()
                if not cert:
                    continue
                cert.status = "active"
                cert.save(update_fields=["status"])
                record_certificate_event(cert, "approved", "Approved in bulk action")
                try:
                    BlockchainClient().store_certificate_hash(cert.certificate_id, cert.certificate_hash)
                    record_certificate_event(cert, "anchored", "Anchored on blockchain in bulk action")
                    approved_count += 1
                except ValueError:
                    approved_count += 1
                except Exception as exc:
                    cert.status = "needs_review"
                    extracted = cert.extracted_data if isinstance(cert.extracted_data, dict) else {}
                    warnings = extracted.get("confidence_warnings", [])
                    warnings.append(f"Bulk approve anchor failed: {exc.__class__.__name__}")
                    extracted["confidence_warnings"] = warnings
                    cert.extracted_data = extracted
                    cert.save(update_fields=["status", "extracted_data"])
                    failed_count += 1
            if approved_count:
                messages.success(request, f"Bulk approved {approved_count} certificate(s).")
            if failed_count:
                messages.warning(request, f"{failed_count} certificate(s) returned to review due to anchor failure.")
            return redirect("dashboard-review")

        cert = get_object_or_404(Certificate, certificate_id=request.POST.get("certificate_id", ""))

        if action == "reprocess":
            try:
                cert = reprocess_existing_certificate(cert)
                messages.success(
                    request,
                    f"Data scan refreshed for {cert.certificate_id}: {cert.student_name} | {cert.course}.",
                )
            except ValueError as exc:
                messages.error(request, f"Reprocess failed for {cert.certificate_id}: {exc}")

        elif action in {"save", "approve"}:
            cert.student_name = request.POST.get("student_name", cert.student_name).strip() or cert.student_name
            cert.registration_number = request.POST.get("registration_number", cert.registration_number).strip() or cert.registration_number
            cert.course = request.POST.get("course", cert.course).strip() or cert.course
            cert.department = request.POST.get("department", cert.department).strip() or cert.department
            cert.university_name = request.POST.get("university_name", cert.university_name).strip() or cert.university_name

            grad_year_raw = request.POST.get("graduation_year", "").strip()
            if grad_year_raw.isdigit():
                cert.graduation_year = int(grad_year_raw)

            cert.issue_date = _parse_iso_date(request.POST.get("issue_date", ""))

            cert.certificate_hash = generate_hash(
                {
                    "student_name": cert.student_name,
                    "registration_number": cert.registration_number,
                    "course": cert.course,
                    "certificate_serial_number": cert.certificate_serial_number,
                    "graduation_year": cert.graduation_year,
                }
            )

            extracted = cert.extracted_data or {}
            extracted.update(
                {
                    "student_name": cert.student_name,
                    "registration_number": cert.registration_number,
                    "course": cert.course,
                    "department": cert.department,
                    "university_name": cert.university_name,
                    "graduation_year": cert.graduation_year,
                    "issue_date": cert.issue_date.isoformat() if cert.issue_date else None,
                }
            )
            score, warnings = compute_confidence(extracted)
            cert.confidence_score = score
            extracted["confidence_score"] = score
            extracted["confidence_warnings"] = warnings
            cert.extracted_data = extracted

            generate_student_documents(cert)
            cert.status = "needs_review"
            cert.save()
            messages.success(request, f"Saved updates for {cert.certificate_id}.")

            if action == "approve":
                cert.status = "active"
                cert.save(update_fields=["status"])
                record_certificate_event(cert, "approved", "Approved by admin")
                try:
                    BlockchainClient().store_certificate_hash(cert.certificate_id, cert.certificate_hash)
                    record_certificate_event(cert, "anchored", "Anchored on blockchain by approval action")
                    messages.success(request, f"Approved and published: {cert.certificate_id}.")
                except ValueError:
                    messages.info(request, f"Approved {cert.certificate_id}; existing record was reused.")
                except Exception as exc:
                    cert.status = "needs_review"
                    extracted = cert.extracted_data or {}
                    warnings = extracted.get("confidence_warnings", [])
                    warnings.append(f"Publish step failed during approve: {exc.__class__.__name__}")
                    extracted["confidence_warnings"] = warnings
                    cert.extracted_data = extracted
                    cert.save(update_fields=["status", "extracted_data"])
                    messages.error(request, f"Approve failed for {cert.certificate_id}. It was returned to review queue.")

        elif action == "reject":
            cert.status = "revoked"
            cert.save(update_fields=["status"])
            record_certificate_event(cert, "rejected", "Rejected by admin")
            BlockchainClient().revoke_certificate(cert.certificate_id)
            messages.warning(request, f"Rejected and revoked {cert.certificate_id}.")

        elif action == "resend_email":
            target_email = request.POST.get("target_email", "").strip()
            used_email = send_certificate_notification(cert, target_email=target_email or None)
            if used_email:
                messages.success(request, f"Certificate email sent to {used_email} for {cert.certificate_id}.")
            else:
                expected = derive_institution_email(cert.registration_number)
                messages.error(request, f"Could not derive target email. Expected format: {expected}")

        return redirect("dashboard-review")

    certificates = list(Certificate.objects.filter(status="needs_review").order_by("-created_at")[:200])
    cert_rows = []
    for cert in certificates:
        cert_rows.append(
            {
                "cert": cert,
                "email_status": _extract_email_status(cert),
            }
        )
    return render(
        request,
        "dashboard/review.html",
        {
            "certificates": certificates,
            "cert_rows": cert_rows,
            "assistant_context_message": "Pending certificates need your approval",
            "now_ts": timezone.now(),
        },
    )


def student_portal(request):
    """Student-side lookup for downloading generated certificate artifacts by registration/certificate ID."""
    reg_no = request.GET.get("reg_no", "").strip()
    certificate_id = request.GET.get("certificate_id", "").strip()

    certificates = Certificate.objects.none()
    if reg_no or certificate_id:
        query = Q()
        if reg_no:
            query &= Q(registration_number__iexact=reg_no)
        if certificate_id:
            query &= Q(certificate_id__iexact=certificate_id)
        certificates = Certificate.objects.filter(query).order_by("-created_at")

    return render(
        request,
        "student/portal.html",
        {
            "reg_no": reg_no,
            "certificate_id": certificate_id,
            "certificates": certificates,
            "assistant_context_message": "Find and download your issued certificates.",
        },
    )
