from django.core.management.base import BaseCommand, CommandError

from certificates.models import Certificate
from certificates.services import build_qr_image, generate_student_documents


class Command(BaseCommand):
    help = "Regenerate QR and issued PDFs for certificates using current VERIFY_BASE_URL."

    def add_arguments(self, parser):
        parser.add_argument("--certificate-id", dest="certificate_id", type=str, help="Regenerate only one certificate ID")
        parser.add_argument("--limit", dest="limit", type=int, default=0, help="Limit number of records (0 = all)")

    def handle(self, *args, **options):
        certificate_id = options.get("certificate_id")
        limit = options.get("limit") or 0

        qs = Certificate.objects.order_by("-created_at")
        if certificate_id:
            qs = qs.filter(certificate_id=certificate_id)
            if not qs.exists():
                raise CommandError(f"Certificate not found: {certificate_id}")
        if limit > 0:
            qs = qs[:limit]

        count = 0
        failed = 0
        for cert in qs:
            try:
                qr_file = build_qr_image(cert.certificate_id)
                qr_bytes = qr_file.read()
                qr_file.seek(0)
                cert.qr_code.save(f"{cert.certificate_id}.png", qr_file, save=False)
                generate_student_documents(cert, qr_bytes=qr_bytes)
                cert.save(update_fields=["qr_code", "college_certificate_pdf", "combined_certificate_pdf"])
                count += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f"Skipped {cert.certificate_id}: {exc}")

        self.stdout.write(self.style.SUCCESS(f"Regenerated issued docs for {count} certificate(s), skipped {failed}."))
