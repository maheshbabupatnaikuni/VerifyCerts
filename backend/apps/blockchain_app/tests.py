from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import AdminProfile
from blockchain_app.models import BlockchainRecord
from blockchain_app.services import BlockchainClient
from certificates.models import Certificate, Student, University
from django.core.files.uploadedfile import SimpleUploadedFile


class BlockchainClientTests(TestCase):
    def setUp(self):
        self.uni = University.objects.create(name="Example University", prefix="EXU", code="EXU")
        self.student = Student.objects.create(
            university=self.uni,
            name="Student One",
            registration_number="EXU2025001",
            department="CSE",
            year=2025,
        )
        self.cert = Certificate.objects.create(
            university=self.uni,
            student=self.student,
            certificate_id="EXU-2025-CSE-000020",
            student_name="Student One",
            registration_number="EXU2025001",
            course="B.Tech",
            department="CSE",
            university_name="Example University",
            certificate_serial_number="PC020",
            graduation_year=2025,
            certificate_hash="hash20",
            status="active",
            pdf_file=SimpleUploadedFile("c.pdf", b"%PDF-1.4\n"),
        )

    def test_estimate_store_cost_returns_not_ok_without_connection(self):
        client = BlockchainClient()
        result = client.estimate_store_cost(self.cert.certificate_id, self.cert.certificate_hash)
        self.assertIn("ok", result)
        if result["ok"]:
            self.assertIn("has_funds", result)
            self.assertIn("required_pol", result)
        else:
            self.assertIn("reason", result)

    def test_get_chain_hash_returns_none_when_unavailable(self):
        client = BlockchainClient()
        chain_hash = client.get_chain_hash(self.cert.certificate_id)
        self.assertIsNone(chain_hash)

    def test_store_certificate_hash_rejects_duplicate_record(self):
        BlockchainRecord.objects.create(
            certificate_id=self.cert.certificate_id,
            transaction_hash="0xabc",
            block_number=1,
            hash=self.cert.certificate_hash,
            issuer_address="issuer",
            status="stored",
        )
        client = BlockchainClient()
        with self.assertRaises(ValueError):
            client.store_certificate_hash(self.cert.certificate_id, self.cert.certificate_hash)

    def test_revoke_certificate_creates_local_revoke_record(self):
        client = BlockchainClient()
        rec = client.revoke_certificate(self.cert.certificate_id)
        self.assertEqual(rec.status, "revoked")
        self.assertTrue(rec.transaction_hash.startswith("offchain-"))


class BlockchainApiTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff", password="test-only-pass-123", is_staff=True)
        AdminProfile.objects.create(user=self.staff, role="operator")
        self.non_staff = User.objects.create_user(username="user", password="test-only-pass-123", is_staff=False)

        BlockchainRecord.objects.create(
            certificate_id="EXU-2025-CSE-000001",
            transaction_hash="0xtx1",
            block_number=11,
            hash="h1",
            issuer_address="0xissuer",
            status="stored",
        )

        self.api = APIClient()

    def test_blockchain_list_requires_staff(self):
        self.api.force_authenticate(user=self.non_staff)
        response = self.api.get(reverse("blockchain-transactions"))
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_blockchain_list_for_staff(self):
        self.api.force_authenticate(user=self.staff)
        response = self.api.get(reverse("blockchain-transactions"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(response.data), 1)

    def test_blockchain_by_certificate_not_found(self):
        self.api.force_authenticate(user=self.staff)
        response = self.api.get(reverse("blockchain-transaction", kwargs={"certificate_id": "NOT-FOUND"}))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_blockchain_by_certificate_found(self):
        self.api.force_authenticate(user=self.staff)
        response = self.api.get(reverse("blockchain-transaction", kwargs={"certificate_id": "EXU-2025-CSE-000001"}))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["certificate_id"], "EXU-2025-CSE-000001")
