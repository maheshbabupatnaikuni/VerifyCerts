"""Blockchain evidence API routes."""

from django.urls import path

from .views import BlockchainRecordByCertificateView, BlockchainRecordListView

urlpatterns = [
    path("blockchain-transactions", BlockchainRecordListView.as_view(), name="blockchain-transactions"),
    path("blockchain-transaction/<str:certificate_id>", BlockchainRecordByCertificateView.as_view(), name="blockchain-transaction"),
]
