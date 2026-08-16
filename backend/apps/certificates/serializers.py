"""DRF serializers for certificate APIs (upload, search, approve/revoke, resend)."""

from rest_framework import serializers

from .models import Certificate, Student, University


class UniversitySerializer(serializers.ModelSerializer):
    """Serializer for university master data."""
    class Meta:
        model = University
        fields = "__all__"


class StudentSerializer(serializers.ModelSerializer):
    """Serializer for student master data."""
    class Meta:
        model = Student
        fields = "__all__"


class CertificateSerializer(serializers.ModelSerializer):
    """Full certificate serializer used by admin and public certificate-detail APIs."""
    class Meta:
        model = Certificate
        fields = "__all__"
        read_only_fields = [
            "certificate_id",
            "certificate_hash",
            "qr_code",
            "created_at",
            "extracted_data",
            "ipfs_cid",
            "confidence_score",
            "college_certificate_pdf",
            "combined_certificate_pdf",
        ]


class CertificateUploadSerializer(serializers.Serializer):
    """Validates a single certificate upload payload."""
    university_id = serializers.IntegerField(required=False)
    pdf_file = serializers.FileField()

    def validate_pdf_file(self, value):
        name = (value.name or "").lower()
        if not name.endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp")):
            raise serializers.ValidationError("Allowed files: PDF, PNG, JPG, JPEG, WEBP.")
        return value


class BatchUploadSerializer(serializers.Serializer):
    """Validates batch uploads and enforces supported file types."""
    university_id = serializers.IntegerField(required=False)
    files = serializers.ListField(child=serializers.FileField(), allow_empty=False)

    def validate_files(self, files):
        allowed = (".pdf", ".png", ".jpg", ".jpeg", ".webp")
        invalid = [f.name for f in files if not (f.name or "").lower().endswith(allowed)]
        if invalid:
            raise serializers.ValidationError(
                f"Invalid files found: {', '.join(invalid[:5])}. Allowed: PDF, PNG, JPG, JPEG, WEBP."
            )
        return files


class RevokeCertificateSerializer(serializers.Serializer):
    """Payload schema for certificate revoke action."""
    certificate_id = serializers.CharField()
    reason = serializers.CharField(required=False, allow_blank=True)


class ApproveCertificateSerializer(serializers.Serializer):
    """Payload schema for certificate approve action."""
    certificate_id = serializers.CharField()


class ResendEmailSerializer(serializers.Serializer):
    """Payload schema for manual resend email action."""
    certificate_id = serializers.CharField()
    target_email = serializers.EmailField(required=False, allow_blank=True)
