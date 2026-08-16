import re
import hashlib
from django.db.models import Case, Count, IntegerField, Q, Value, When
from django.shortcuts import redirect, render
from django.http import HttpResponse
from django.conf import settings
from django.urls import reverse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminStaff
from certificates.models import Certificate
from certificates.services import OCRService, generate_hash, ensure_generated_documents_current
from blockchain_app.models import BlockchainRecord
from blockchain_app.services import BlockchainClient
from .models import VerificationLog
from .services import evaluate_certificate, log_verification
from certificates.models import CertificateEvent


class VerifyCertificateView(APIView):
    """API endpoint used by verifier clients to validate a certificate by certificate_id."""
    permission_classes = [AllowAny]

    def get_client_ip(self, request):
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.META.get("REMOTE_ADDR", "0.0.0.0")

    def get(self, request, certificate_id: str):
        certificate = Certificate.objects.filter(certificate_id=certificate_id).first()
        if not certificate:
            return Response({"status": "INVALID", "message": "Certificate not found."}, status=404)

        result, message = evaluate_certificate(certificate)
        log_verification(certificate, self.get_client_ip(request), result)

        return Response(
            {
                "certificate_id": certificate.certificate_id,
                "status": result.upper(),
                "message": message,
                "student_name": certificate.student_name,
                "course": certificate.course,
                "department": certificate.department,
                "graduation_year": certificate.graduation_year,
                "university": certificate.university_name,
            }
        )


class VerificationAnalyticsView(APIView):
    permission_classes = [IsAdminStaff]

    def get(self, request):
        result_counts = VerificationLog.objects.values("result").annotate(count=Count("id")).order_by("result")
        cert_counts = Certificate.objects.values("status").annotate(count=Count("id")).order_by("status")
        return Response(
            {
                "verification_results": list(result_counts),
                "certificate_status": list(cert_counts),
                "total_certificates": Certificate.objects.count(),
                "total_verifications": VerificationLog.objects.count(),
            }
        )


def verify_portal_page(request):
    """
    Public verify entry endpoint.
    Supports both certificate_id and transaction hash input from portal form.
    """
    certificate_id = ""
    tx_hash = ""
    if request.method == "POST":
        certificate_id = request.POST.get("certificate_id", "").strip()
        tx_hash = request.POST.get("tx_hash", "").strip()
    else:
        certificate_id = request.GET.get("certificate_id", "").strip()
        tx_hash = request.GET.get("tx_hash", "").strip()
    if tx_hash:
        return redirect("public_verify_by_tx_page", tx_hash=tx_hash)
    if certificate_id:
        return redirect("public_verify_page", certificate_id=certificate_id)
    return render(request, "verification/portal.html")


def public_verify_by_tx_page(request, tx_hash: str):
    """Resolve a transaction hash to certificate_id and redirect to normal certificate verification page."""
    tx_hash = (tx_hash or "").strip()
    record = BlockchainRecord.objects.filter(transaction_hash__iexact=tx_hash).order_by("-created_at").first()
    if not record:
        return render(
            request,
            "verification/public_verify.html",
            {
                "status": "INVALID",
                "message": "No certificate found for this transaction hash.",
                "certificate": None,
            },
        )
    return redirect("public_verify_page", certificate_id=record.certificate_id)


def public_verify_page(request, certificate_id: str):
    """
    Render human-readable public verification result page with proof panel and timeline.
    """
    certificate = Certificate.objects.filter(certificate_id=certificate_id).first()
    if not certificate:
        context = {
            "status": "INVALID",
            "message": "Certificate not found.",
            "certificate": None,
        }
        return render(request, "verification/public_verify.html", context)

    # Always keep generated student artifacts in sync with latest template/data
    # so verifier/student never sees stale layout or legacy network labels.
    ensure_generated_documents_current(certificate)

    result, message = evaluate_certificate(certificate)
    verifier_ip = request.META.get("REMOTE_ADDR", "0.0.0.0")
    log_verification(certificate, verifier_ip, result)

    recomputed_hash = generate_hash(
        {
            "student_name": certificate.student_name,
            "registration_number": certificate.registration_number,
            "course": certificate.course,
            "certificate_serial_number": certificate.certificate_serial_number,
            "graduation_year": certificate.graduation_year,
        }
    )
    local_hash = (certificate.certificate_hash or "").strip() or recomputed_hash
    chain_hash = BlockchainClient().get_chain_hash(certificate.certificate_id)
    latest_record = (
        BlockchainRecord.objects.filter(certificate_id=certificate.certificate_id)
        .order_by("-created_at")
        .first()
    )

    uploaded_check = request.session.pop("uploaded_pdf_check", None)
    force_uploaded_mismatch = request.GET.get("uploaded_mismatch") == "1"
    explorer_tx_base = (getattr(settings, "CHAIN_EXPLORER_TX_BASE", "") or "").rstrip("/")
    explorer_addr_base = (getattr(settings, "CHAIN_EXPLORER_ADDRESS_BASE", "") or "").rstrip("/")
    tx_hash = latest_record.transaction_hash if latest_record else ""
    is_real_chain_tx = bool(tx_hash and not str(tx_hash).startswith("offchain-"))
    explorer_url = f"{explorer_tx_base}/{tx_hash}" if explorer_tx_base and tx_hash and str(tx_hash).startswith("0x") else ""
    contract_addr = (getattr(settings, "CONTRACT_ADDRESS", "") or "").strip()
    contract_url = f"{explorer_addr_base}/{contract_addr}" if explorer_addr_base and contract_addr.startswith("0x") else ""

    events = list(certificate.events.order_by("created_at"))
    if not events:
        # Backfill timeline for old records created before event tracking.
        CertificateEvent.objects.create(
            certificate=certificate,
            event_type="uploaded",
            message="Backfilled from existing certificate record",
            metadata={"backfilled": True},
            created_at=certificate.created_at,
        )
        CertificateEvent.objects.create(
            certificate=certificate,
            event_type="ocr_extracted",
            message="Backfilled extraction step",
            metadata={"backfilled": True},
            created_at=certificate.created_at,
        )
        if certificate.status in {"active", "revoked", "expired"}:
            CertificateEvent.objects.create(
                certificate=certificate,
                event_type="approved",
                message="Backfilled approval status",
                metadata={"backfilled": True},
                created_at=certificate.created_at,
            )
        if latest_record and is_real_chain_tx:
            CertificateEvent.objects.create(
                certificate=certificate,
                event_type="anchored",
                message="Backfilled from blockchain record",
                metadata={"backfilled": True},
                created_at=latest_record.timestamp,
            )
        events = list(certificate.events.order_by("created_at"))
    event_map = {}
    for ev in events:
        event_map.setdefault(ev.event_type, ev)
    timeline_steps = [
        ("uploaded", "Uploaded"),
        ("ocr_extracted", "OCR extracted"),
        ("approved", "Approved"),
        ("anchored", "Anchored on blockchain"),
        ("email_sent", "Email sent"),
    ]
    verification_timeline = [
        {
            "key": key,
            "label": label,
            "done": key in event_map,
            "time": event_map.get(key).created_at if key in event_map else None,
            "message": event_map.get(key).message if key in event_map else "",
        }
        for key, label in timeline_steps
    ]

    on_chain_proof_id = (
        f"{str(tx_hash)[:18]}...{str(tx_hash)[-8:]}" if is_real_chain_tx and tx_hash and len(str(tx_hash)) > 30 else "Not anchored on public chain"
    )

    has_chain_hash = bool(chain_hash and str(chain_hash).strip() and str(chain_hash).strip() != "-")
    hash_match = bool(has_chain_hash and chain_hash == local_hash)
    anchored_on_chain = bool(is_real_chain_tx and has_chain_hash)

    uploaded_file_mismatch = force_uploaded_mismatch or bool(uploaded_check and uploaded_check.get("overall_match") != "TRUE")
    if uploaded_file_mismatch:
        # If verifier submitted a file and it does not exactly match any issued artifact,
        # always surface tampered status on the main verification banner.
        result = "tampered"
        message = "Uploaded file does not match the issued certificate artifact."

    context = {
        "status": result.upper(),
        "message": message,
        "certificate": certificate,
        "proof": {
            "network": getattr(settings, "CHAIN_DISPLAY_NAME", "Configured Chain"),
            "local_hash": local_hash,
            "chain_hash": chain_hash or "-",
            "hash_match": hash_match,
            "has_chain_hash": has_chain_hash,
            "anchored_on_chain": anchored_on_chain,
            "tx_hash": tx_hash or "-",
            "block_number": latest_record.block_number if latest_record else "-",
            "recorded_at": latest_record.timestamp if latest_record else None,
            "explorer_url": explorer_url,
            "contract_url": contract_url,
            "on_chain_proof_id": on_chain_proof_id,
            "contract_address": contract_addr or "-",
            "is_real_chain_tx": is_real_chain_tx,
        },
        "uploaded_check": uploaded_check,
        "verification_timeline": verification_timeline,
    }
    return render(request, "verification/public_verify.html", context)


def download_verification_report(request, certificate_id: str):
    """Download a plain-text verification report for reviewer/examiner evidence submission."""
    certificate = Certificate.objects.filter(certificate_id=certificate_id).first()
    if not certificate:
        return HttpResponse("Certificate not found.", status=404, content_type="text/plain")

    result, message = evaluate_certificate(certificate)
    chain_hash = BlockchainClient().get_chain_hash(certificate.certificate_id) or "-"
    latest_record = BlockchainRecord.objects.filter(certificate_id=certificate.certificate_id).order_by("-created_at").first()

    lines = [
        "Verification Report",
        "===================",
        f"Certificate ID: {certificate.certificate_id}",
        f"Status: {result.upper()}",
        f"Message: {message}",
        "",
        f"Student Name: {certificate.student_name}",
        f"Registration Number: {certificate.registration_number}",
        f"Course: {certificate.course}",
        f"Department: {certificate.department}",
        f"University: {certificate.university_name}",
        f"Graduation Year: {certificate.graduation_year}",
        "",
        f"Local Hash: {certificate.certificate_hash}",
        f"Chain Hash: {chain_hash}",
        f"Hash Match: {'YES' if (chain_hash == certificate.certificate_hash and chain_hash != '-') else 'NO'}",
        f"Transaction Hash: {latest_record.transaction_hash if latest_record else '-'}",
        f"Block Number: {latest_record.block_number if latest_record else '-'}",
        f"Recorded At: {latest_record.timestamp if latest_record else '-'}",
    ]
    content = "\n".join(lines)
    response = HttpResponse(content, content_type="text/plain; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="verification_report_{certificate.certificate_id}.txt"'
    return response


def verify_uploaded_pdf(request):
    """
    Verifier-side document check endpoint.

    Accepts PDF/image file, extracts fields, finds matching issued record, and compares:
    - exact file signature
    - canonical field hash
    - semantic identity match (ID/RegNo)
    """
    if request.method != "POST":
        return redirect("/#verify-section")

    pdf_file = request.FILES.get("pdf_file")
    if not pdf_file:
        return render(
            request,
            "verification/public_verify.html",
            {"status": "INVALID", "message": "No PDF file provided.", "certificate": None},
        )

    if not str(pdf_file.name).lower().endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp")):
        return render(
            request,
            "verification/public_verify.html",
            {"status": "INVALID", "message": "Please upload a valid file (PDF, PNG, JPG, JPEG, WEBP).", "certificate": None},
        )

    pdf_bytes = pdf_file.read()
    extracted = OCRService.extract_fields(pdf_bytes)
    uploaded_field_hash = generate_hash(
        {
            "student_name": extracted.get("student_name", ""),
            "registration_number": extracted.get("registration_number", ""),
            "course": extracted.get("course", ""),
            "certificate_serial_number": extracted.get("certificate_serial_number", ""),
            "graduation_year": extracted.get("graduation_year", ""),
        }
    )
    uploaded_file_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
    raw_text = (extracted.get("raw_text") or "")[:20000]

    detected_id = None
    id_match = re.search(r"\b([A-Z0-9]+-\d{4}-[A-Z0-9]+-\d{3,})\b", raw_text, re.IGNORECASE)
    if id_match:
        detected_id = id_match.group(1).upper()

    certificate = None
    if detected_id:
        certificate = Certificate.objects.filter(certificate_id__iexact=detected_id).first()

    reg_no = (extracted.get("registration_number") or "").strip()
    student_name = (extracted.get("student_name") or "").strip()
    course = (extracted.get("course") or "").strip()
    if not certificate and reg_no:
        candidate_q = Q(registration_number__iexact=reg_no)
        if student_name:
            candidate_q &= Q(student_name__icontains=student_name.split(" ")[0])
        if course:
            candidate_q &= Q(course__icontains=course.split(" ")[0])
        certificate = (
            Certificate.objects.filter(candidate_q)
            .annotate(
                _status_rank=Case(
                    When(status="active", then=Value(0)),
                    When(status="expired", then=Value(1)),
                    When(status="needs_review", then=Value(2)),
                    When(status="revoked", then=Value(3)),
                    default=Value(4),
                    output_field=IntegerField(),
                )
            )
            .order_by("_status_rank", "-created_at")
            .first()
        )

    if not certificate:
        return render(
            request,
            "verification/public_verify.html",
            {
                "status": "INVALID",
                "message": "Uploaded PDF does not match any issued certificate record.",
                "certificate": None,
                "uploaded_preview": {
                    "student_name": extracted.get("student_name", "-"),
                    "registration_number": extracted.get("registration_number", "-"),
                    "course": extracted.get("course", "-"),
                    "detected_certificate_id": detected_id or "-",
                    "uploaded_hash": uploaded_field_hash,
                    "uploaded_file_sha256": uploaded_file_sha256,
                },
            },
        )

    def _file_sha256(file_field):
        if not file_field:
            return ""
        try:
            file_field.open("rb")
            raw = file_field.read()
            file_field.close()
            return hashlib.sha256(raw).hexdigest()
        except Exception:
            return ""

    original_sha = _file_sha256(certificate.pdf_file)
    college_sha = _file_sha256(certificate.college_certificate_pdf)
    combined_sha = _file_sha256(certificate.combined_certificate_pdf)

    exact_source_match = uploaded_file_sha256 in {h for h in [original_sha, college_sha, combined_sha] if h}

    extracted_with_fallback_hash = generate_hash(
        {
            "student_name": extracted.get("student_name") or certificate.student_name,
            "registration_number": extracted.get("registration_number") or certificate.registration_number,
            "course": extracted.get("course") or certificate.course,
            "certificate_serial_number": extracted.get("certificate_serial_number") or certificate.certificate_serial_number,
            "graduation_year": extracted.get("graduation_year") or certificate.graduation_year,
        }
    )
    canonical_match = extracted_with_fallback_hash == certificate.certificate_hash
    id_match = bool(detected_id and detected_id.upper() == certificate.certificate_id.upper())
    reg_match = bool((extracted.get("registration_number") or "").strip().upper() == (certificate.registration_number or "").strip().upper())
    semantic_match = id_match or reg_match
    # Strict anti-tamper policy:
    # A verifier-uploaded file is considered authentic only when it exactly matches
    # one issued artifact (original upload / generated college / generated combined).
    overall_match = exact_source_match

    request.session["uploaded_pdf_check"] = {
        "uploaded_hash": extracted_with_fallback_hash,
        "uploaded_file_sha256": uploaded_file_sha256,
        "match_with_db_hash": str(canonical_match).upper(),
        "exact_source_match": str(exact_source_match).upper(),
        "semantic_match": str(semantic_match).upper(),
        "overall_match": str(overall_match).upper(),
        "extracted_name": extracted.get("student_name", "-"),
        "extracted_registration_number": extracted.get("registration_number", "-"),
        "extracted_course": extracted.get("course", "-"),
        "detected_certificate_id": detected_id or "-",
    }

    verify_url = reverse("public_verify_page", kwargs={"certificate_id": certificate.certificate_id})
    if not overall_match:
        # Make tampered verdict deterministic even if session payload is unavailable.
        return redirect(f"{verify_url}?uploaded_mismatch=1")
    return redirect(verify_url)
