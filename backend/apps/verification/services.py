"""Core verification decision logic used by API and public verification pages."""

from datetime import date

from blockchain_app.services import BlockchainClient
from certificates.models import Certificate
from certificates.services import generate_hash
from .models import VerificationLog


def evaluate_certificate(certificate: Certificate) -> tuple[str, str]:
    """
    Compute final verification status with message by combining:
    - certificate lifecycle state
    - recomputed canonical hash
    - on-chain hash lookup
    """
    if certificate.status == "needs_review":
        return "needs_review", "Certificate is pending manual review before final verification."

    if certificate.status == "revoked":
        return "revoked", "Certificate has been revoked by issuer."

    if certificate.expiry_date and certificate.expiry_date < date.today():
        if certificate.status != "expired":
            certificate.status = "expired"
            certificate.save(update_fields=["status"])
        return "expired", "Certificate has expired."

    recomputed_hash = generate_hash(
        {
            "student_name": certificate.student_name,
            "registration_number": certificate.registration_number,
            "course": certificate.course,
            "certificate_serial_number": certificate.certificate_serial_number,
            "graduation_year": certificate.graduation_year,
        }
    )
    db_hash = (certificate.certificate_hash or "").strip()

    if db_hash and recomputed_hash != db_hash:
        return "needs_review", "Certificate record changed after issuance. Re-approval required."

    canonical_hash = db_hash or recomputed_hash
    chain_hash = BlockchainClient().get_chain_hash(certificate.certificate_id)

    if not chain_hash:
        return "invalid", "Certificate has no live on-chain proof yet."

    if canonical_hash != chain_hash:
        return "tampered", "TAMPERED CERTIFICATE DETECTED"

    return "valid", "Certificate is valid and blockchain verified."


def log_verification(certificate: Certificate, verifier_ip: str, result: str) -> None:
    """Write verification event to audit log table."""
    VerificationLog.objects.create(certificate=certificate, verifier_ip=verifier_ip or "0.0.0.0", result=result)
