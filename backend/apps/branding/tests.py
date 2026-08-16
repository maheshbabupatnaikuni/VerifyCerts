from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.utils import timezone

from accounts.models import AdminProfile
from blockchain_app.models import BlockchainRecord
from certificates.models import Certificate, Student, University
from verification.models import VerificationLog


class DashboardHomeTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(username="staff", password="test-only-pass-123", is_staff=True)
        AdminProfile.objects.create(user=self.staff, role="operator")
        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")
        self.student = Student.objects.create(
            university=self.uni,
            name="Student Two",
            registration_number="EXU2025002",
            department="CSE",
            year=2025,
        )

    def _make_certificate(self, certificate_id: str, cert_hash: str, status: str = "active", archived_duplicate: bool = False):
        extracted_data = {}
        if archived_duplicate:
            extracted_data["archived_duplicate"] = True
        return Certificate.objects.create(
            university=self.uni,
            student=self.student,
            certificate_id=certificate_id,
            student_name="Student Two",
            registration_number="EXU2025002",
            course="B.Tech",
            department="CSE",
            university_name="Example University",
            certificate_serial_number=f"PC-{certificate_id[-4:]}",
            graduation_year=2025,
            certificate_hash=cert_hash,
            status=status,
            extracted_data=extracted_data,
            pdf_file=SimpleUploadedFile(f"{certificate_id}.pdf", b"%PDF-1.4\n"),
        )

    def test_dashboard_home_uses_grouped_metrics_and_excludes_archived_duplicates(self):
        self._make_certificate("EXU-2025-CSE-000001", "same_hash")
        self._make_certificate("EXU-2025-CSE-000002", "same_hash", status="needs_review", archived_duplicate=True)
        self._make_certificate("EXU-2025-CSE-000003", "other_hash", status="needs_review")
        self._make_certificate("EXU-2025-CSE-000004", "other_hash_2", status="active")
        VerificationLog.objects.create(
            certificate=Certificate.objects.get(certificate_id="EXU-2025-CSE-000001"),
            verifier_ip="127.0.0.1",
            result="valid",
        )

        self.client.login(username="staff", password="test-only-pass-123")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cert_count"], 3)
        self.assertEqual(response.context["active_count"], 2)
        self.assertEqual(response.context["review_count"], 1)
        self.assertEqual(response.context["verification_count"], 1)

    def test_dashboard_home_public_user_has_verifier_message(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["is_admin"])
        self.assertIn("Awaiting QR, ID, or PDF", response.context["assistant_context_message"])


class DashboardSearchAndChainTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(username="staff", password="test-only-pass-123", is_staff=True)
        AdminProfile.objects.create(user=self.staff, role="operator")
        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")
        self.student = Student.objects.create(
            university=self.uni,
            name="Student Two",
            registration_number="EXU2025002",
            department="CSE",
            year=2025,
        )
        self.client.login(username="staff", password="test-only-pass-123")

    def _make_certificate(self, idx: int):
        return Certificate.objects.create(
            university=self.uni,
            student=self.student,
            certificate_id=f"EXU-2025-CSE-{idx:06d}",
            student_name="Student Two",
            registration_number="EXU2025002",
            course="B.Tech",
            department="CSE",
            university_name="Example University",
            certificate_serial_number=f"PC{idx}",
            graduation_year=2025,
            certificate_hash=f"hash{idx}",
            status="active",
            pdf_file=SimpleUploadedFile(f"c{idx}.pdf", b"%PDF-1.4\n"),
        )

    def test_search_recent_limits_view_to_25_items(self):
        for i in range(30):
            cert = self._make_certificate(i + 1)
            cert.created_at = timezone.now() + timezone.timedelta(seconds=i)
            cert.save(update_fields=["created_at"])

        response = self.client.get("/dashboard/search/?recent=1")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(response.context["group_result_count"], 25)

    def test_blockchain_dashboard_shows_only_real_chain_transactions(self):
        cert = self._make_certificate(1)
        BlockchainRecord.objects.create(
            certificate_id=cert.certificate_id,
            transaction_hash="offchain-EXU-2025-CSE-000001-12345",
            block_number=12345,
            hash=cert.certificate_hash,
            issuer_address="local",
            status="stored",
        )
        BlockchainRecord.objects.create(
            certificate_id=cert.certificate_id,
            transaction_hash="0x1234567890abcdef",
            block_number=10,
            hash=cert.certificate_hash,
            issuer_address="0xissuer",
            status="stored",
        )

        response = self.client.get("/dashboard/blockchain/")
        self.assertEqual(response.status_code, 200)
        records = list(response.context["records"])
        self.assertEqual(len(records), 1)
        self.assertTrue(records[0].transaction_hash.startswith("0x"))
