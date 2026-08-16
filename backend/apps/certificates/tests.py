import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import AdminProfile
from blockchain_app.models import BlockchainRecord
from certificates.models import Certificate, Student, University, UploadJob, validate_certificate_input
from certificates.views import resolve_university_or_error


class CertificateValidationTests(TestCase):
    def test_validate_certificate_input_accepts_supported_extensions(self):
        file_obj = SimpleUploadedFile("ok.pdf", b"pdf")
        validate_certificate_input(file_obj)  # no exception

    def test_validate_certificate_input_rejects_unsupported_extensions(self):
        file_obj = SimpleUploadedFile("bad.exe", b"bin")
        with self.assertRaises(Exception):
            validate_certificate_input(file_obj)


class UniversityResolveTests(TestCase):
    def test_resolve_with_explicit_id(self):
        uni = University.objects.create(name="U1", prefix="U1", code="U1")
        found, err = resolve_university_or_error(uni.id)
        self.assertEqual(found.id, uni.id)
        self.assertIsNone(err)

    def test_resolve_with_no_active_university(self):
        found, err = resolve_university_or_error(None)
        self.assertIsNone(found)
        self.assertIn("No active university", err)

    def test_resolve_with_multiple_active_universities(self):
        University.objects.create(name="U1", prefix="U1", code="U1")
        University.objects.create(name="U2", prefix="U2", code="U2")
        found, err = resolve_university_or_error(None)
        self.assertIsNone(found)
        self.assertIn("Multiple active universities", err)


@override_settings(MAX_UPLOAD_SIZE_MB=1)
class UploadApiTests(TestCase):
    def setUp(self):
        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")
        self.staff = User.objects.create_user(username="staff", password="test-only-pass-123", is_staff=True)
        AdminProfile.objects.create(user=self.staff, role="operator")
        self.non_staff = User.objects.create_user(username="user", password="test-only-pass-123", is_staff=False)
        self.api = APIClient()

    def test_upload_requires_staff(self):
        self.api.force_authenticate(user=self.non_staff)
        f = SimpleUploadedFile("a.pdf", b"%PDF-1.4\n")
        response = self.api.post(reverse("upload-certificate"), {"pdf_file": f}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @patch("certificates.views.threading.Thread")
    def test_upload_single_creates_job_and_starts_thread(self, thread_cls):
        thread_instance = MagicMock()
        thread_cls.return_value = thread_instance

        self.api.force_authenticate(user=self.staff)
        f = SimpleUploadedFile("a.pdf", b"%PDF-1.4\n")
        response = self.api.post(reverse("upload-certificate"), {"pdf_file": f}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        token = response.data["job_token"]
        self.assertTrue(UploadJob.objects.filter(token=token).exists())
        thread_instance.start.assert_called_once()

    def test_upload_rejects_invalid_extension(self):
        self.api.force_authenticate(user=self.staff)
        f = SimpleUploadedFile("a.exe", b"MZ")
        response = self.api.post(reverse("upload-certificate"), {"pdf_file": f}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_upload_job_status_endpoint(self):
        self.api.force_authenticate(user=self.staff)
        job = UploadJob.objects.create(token="tok1", kind="single", status="processing", progress=40, step="OCR")
        response = self.api.get(reverse("upload-job-status", kwargs={"token": job.token}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["progress"], 40)


class ProcessCertificateTests(TestCase):
    def setUp(self):
        self.tmp_media = tempfile.mkdtemp(prefix="verifycerts_test_media_")
        self.media_override = override_settings(MEDIA_ROOT=self.tmp_media)
        self.media_override.enable()
        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")

    def tearDown(self):
        self.media_override.disable()
        shutil.rmtree(self.tmp_media, ignore_errors=True)

    def _make_source_file(self, name="cert.pdf"):
        return SimpleUploadedFile(name, b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n", content_type="application/pdf")

    @patch("certificates.services.send_certificate_notification")
    @patch("certificates.services.generate_student_documents")
    @patch("certificates.services.upload_to_ipfs_if_enabled", return_value="")
    @patch("certificates.services.OCRService.extract_fields")
    def test_process_certificate_creates_active_certificate(
        self,
        extract_fields_mock,
        _ipfs_mock,
        _docs_mock,
        _send_mock,
    ):
        from certificates.services import process_certificate_file

        extract_fields_mock.return_value = {
            "student_name": "Student One",
            "registration_number": "EXU2025001",
            "course": "B.Tech",
            "department": "CSE",
            "university_name": "Example University",
            "certificate_serial_number": "PC123",
            "graduation_year": 2025,
            "raw_text": "sample",
        }

        cert = process_certificate_file(self._make_source_file(), self.uni, force_blockchain=False)
        self.assertEqual(cert.status, "active")
        self.assertEqual(cert.registration_number, "EXU2025001")
        self.assertTrue(cert.certificate_hash)

    @patch("certificates.services.send_certificate_notification")
    @patch("certificates.services.generate_student_documents")
    @patch("certificates.services.upload_to_ipfs_if_enabled", return_value="")
    @patch("certificates.services.OCRService.extract_fields")
    def test_process_certificate_reuses_duplicate_hash_record(
        self,
        extract_fields_mock,
        _ipfs_mock,
        _docs_mock,
        _send_mock,
    ):
        from certificates.services import process_certificate_file

        extract_fields_mock.return_value = {
            "student_name": "Student One",
            "registration_number": "EXU2025001",
            "course": "B.Tech",
            "department": "CSE",
            "university_name": "Example University",
            "certificate_serial_number": "PC123",
            "graduation_year": 2025,
            "raw_text": "sample",
        }

        cert1 = process_certificate_file(self._make_source_file("c1.pdf"), self.uni, force_blockchain=False)
        cert2 = process_certificate_file(self._make_source_file("c2.pdf"), self.uni, force_blockchain=False)

        self.assertEqual(cert1.id, cert2.id)
        self.assertEqual(Certificate.objects.count(), 1)


class ApproveAndRevokeApiTests(TestCase):
    def setUp(self):
        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")
        self.student = Student.objects.create(
            university=self.uni,
            name="Test User",
            registration_number="EXU2025011",
            department="CSE",
            year=2025,
        )
        self.cert = Certificate.objects.create(
            university=self.uni,
            student=self.student,
            certificate_id="EXU-2025-CSE-000001",
            student_name="Test User",
            registration_number="EXU2025011",
            course="B.Tech",
            department="CSE",
            university_name="Example University",
            certificate_serial_number="PC001",
            graduation_year=2025,
            certificate_hash="abc123",
            status="needs_review",
            pdf_file=SimpleUploadedFile("cert.pdf", b"%PDF-1.4\n"),
        )
        self.staff = User.objects.create_user(username="staff", password="test-only-pass-123", is_staff=True)
        AdminProfile.objects.create(user=self.staff, role="operator")
        self.api = APIClient()
        self.api.force_authenticate(user=self.staff)

    @patch("certificates.views.BlockchainClient.store_certificate_hash")
    def test_approve_certificate_success(self, store_mock):
        store_mock.return_value = None
        response = self.api.post(reverse("approve-certificate"), {"certificate_id": self.cert.certificate_id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.cert.refresh_from_db()
        self.assertEqual(self.cert.status, "active")

    def test_revoke_certificate_creates_revoke_record(self):
        self.cert.status = "active"
        self.cert.save(update_fields=["status"])
        response = self.api.post(reverse("revoke-certificate"), {"certificate_id": self.cert.certificate_id}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(BlockchainRecord.objects.filter(certificate_id=self.cert.certificate_id, status="revoked").exists())
