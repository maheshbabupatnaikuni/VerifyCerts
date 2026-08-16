"""Read-only admin APIs for blockchain record listings/details."""

from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminStaff
from .models import BlockchainRecord
from .serializers import BlockchainRecordSerializer


class BlockchainRecordListView(generics.ListAPIView):
    """List all blockchain records sorted by latest timestamp."""
    queryset = BlockchainRecord.objects.order_by("-timestamp")
    serializer_class = BlockchainRecordSerializer
    permission_classes = [IsAdminStaff]


class BlockchainRecordByCertificateView(APIView):
    """Fetch latest blockchain record for a specific certificate_id."""
    permission_classes = [IsAdminStaff]

    def get(self, request, certificate_id: str):
        record = BlockchainRecord.objects.filter(certificate_id=certificate_id).order_by("-timestamp").first()
        if not record:
            return Response({"detail": "Transaction not found."}, status=404)
        return Response(BlockchainRecordSerializer(record).data)
