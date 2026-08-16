import shutil
import tempfile
from datetime import timedelta
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from certificates.models import Certificate, Student, University
from verification.models import VerificationLog
from verification.services import evaluate_certificate


class VerificationServiceTests(TestCase):
    def setUp(self):
        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")
        self.student = Student.objects.create(
            university=self.uni,
            name="Student Two",
            registration_number="EXU2025002",
            department="CSE",
            year=2025,
        )

    def _certificate(self, **overrides):
        data = {
            "university": self.uni,
            "student": self.student,
            "certificate_id": "EXU-2025-CSE-000010",
            "student_name": "Student Two",
            "registration_number": "EXU2025002",
            "course": "B.Tech",
            "department": "CSE",
            "university_name": "Example University",
            "certificate_serial_number": "PC010",
            "graduation_year": 2025,
            "certificate_hash": "hash_ok",
            "status": "active",
            "pdf_file": SimpleUploadedFile("cert.pdf", b"%PDF-1.4\n"),
        }
        data.update(overrides)
        return Certificate.objects.create(**data)

    @patch("verification.services.generate_hash", return_value="hash_ok")
    @patch("verification.services.BlockchainClient.get_chain_hash", return_value="hash_ok")
    def test_evaluate_certificate_valid(self, _chain, _gen):
        cert = self._certificate()
        result, _message = evaluate_certificate(cert)
        self.assertEqual(result, "valid")

    @patch("verification.services.generate_hash", return_value="hash_ok")
    @patch("verification.services.BlockchainClient.get_chain_hash", return_value="")
    def test_evaluate_certificate_invalid_when_no_chain_hash(self, _chain, _gen):
        cert = self._certificate()
        result, _message = evaluate_certificate(cert)
        self.assertEqual(result, "invalid")

    @patch("verification.services.generate_hash", return_value="hash_ok")
    @patch("verification.services.BlockchainClient.get_chain_hash", return_value="hash_other")
    def test_evaluate_certificate_tampered_when_hash_mismatch(self, _chain, _gen):
        cert = self._certificate()
        result, _message = evaluate_certificate(cert)
        self.assertEqual(result, "tampered")

    def test_evaluate_certificate_needs_review_state(self):
        cert = self._certificate(status="needs_review")
        result, _message = evaluate_certificate(cert)
        self.assertEqual(result, "needs_review")

    def test_evaluate_certificate_revoked_state(self):
        cert = self._certificate(status="revoked")
        result, _message = evaluate_certificate(cert)
        self.assertEqual(result, "revoked")

    def test_evaluate_certificate_expired_state(self):
        cert = self._certificate(expiry_date=timezone.now().date() - timedelta(days=1), status="active")
        result, _message = evaluate_certificate(cert)
        self.assertEqual(result, "expired")
        cert.refresh_from_db()
        self.assertEqual(cert.status, "expired")


class VerificationViewTests(TestCase):
    def setUp(self):
        self.tmp_media = tempfile.mkdtemp(prefix="verify_media_")
        self.media_override = override_settings(MEDIA_ROOT=self.tmp_media)
        self.media_override.enable()

        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")
        self.student = Student.objects.create(
            university=self.uni,
            name="Student Two",
            registration_number="EXU2025002",
            department="CSE",
            year=2025,
        )
        self.cert = Certificate.objects.create(
            university=self.uni,
            student=self.student,
            certificate_id="EXU-2025-CSE-000010",
            student_name="Student Two",
            registration_number="EXU2025002",
            course="B.Tech",
            department="CSE",
            university_name="Example University",
            certificate_serial_number="PC010",
            graduation_year=2025,
            certificate_hash="hash_ok",
            status="active",
            pdf_file=SimpleUploadedFile("cert.pdf", b"%PDF-1.4\noriginal"),
        )
        self.client = Client()

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.tmp_media, ignore_errors=True)

    @patch("verification.services.generate_hash", return_value="hash_ok")
    @patch("verification.services.BlockchainClient.get_chain_hash", return_value="hash_ok")
    def test_verify_certificate_api_valid(self, _chain, _hash):
        response = self.client.get(reverse("verify-certificate", kwargs={"certificate_id": self.cert.certificate_id}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "VALID")

    def test_public_verify_page_not_found(self):
        response = self.client.get(reverse("public_verify_page", kwargs={"certificate_id": "NOPE-1"}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Certificate not found")

    @patch("verification.views.OCRService.extract_fields")
    @patch("verification.views.generate_hash", return_value="hash_ok")
    def test_verify_uploaded_pdf_redirects_to_matched_certificate(self, _hash, extract_mock):
        extract_mock.return_value = {
            "student_name": "Student Two",
            "registration_number": "EXU2025002",
            "course": "B.Tech",
            "certificate_serial_number": "PC010",
            "graduation_year": 2025,
            "raw_text": "EXU-2025-CSE-000010",
        }

        upload = SimpleUploadedFile("upload.pdf", b"%PDF-1.4\noriginal")
        response = self.client.post(reverse("verify_uploaded_pdf"), {"pdf_file": upload})
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.cert.certificate_id, response.url)

    def test_verify_uploaded_pdf_rejects_invalid_extension(self):
        upload = SimpleUploadedFile("bad.exe", b"MZ")
        response = self.client.post(reverse("verify_uploaded_pdf"), {"pdf_file": upload})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please upload a valid file")

    @patch("verification.views.BlockchainClient.get_chain_hash", return_value="hash_ok")
    @patch("verification.views.generate_hash", return_value="hash_ok")
    def test_public_verify_creates_verification_log(self, _hash, _chain):
        before = VerificationLog.objects.count()
        self.client.get(reverse("public_verify_page", kwargs={"certificate_id": self.cert.certificate_id}))
        self.assertEqual(VerificationLog.objects.count(), before + 1)

    @patch("verification.views.BlockchainClient.get_chain_hash", return_value="hash_ok")
    @patch("verification.views.OCRService.extract_fields")
    @patch("verification.views.generate_hash", return_value="hash_ok")
    def test_verify_uploaded_pdf_requires_exact_file_match_for_valid(self, _hash, extract_mock, _chain):
        extract_mock.return_value = {
            "student_name": "Student Two",
            "registration_number": "EXU2025002",
            "course": "B.Tech",
            "certificate_serial_number": "PC010",
            "graduation_year": 2025,
            "raw_text": "EXU-2025-CSE-000010",
        }

        # Different bytes than original stored PDF -> should be treated as tampered at main result banner.
        upload = SimpleUploadedFile("tampered.pdf", b"%PDF-1.4\ntampered-content")
        response = self.client.post(reverse("verify_uploaded_pdf"), {"pdf_file": upload}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "TAMPERED")
        self.assertContains(response, "does not match the issued certificate artifact")
