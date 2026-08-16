"""Serializers for blockchain evidence API responses."""

from rest_framework import serializers

from .models import BlockchainRecord


class BlockchainRecordSerializer(serializers.ModelSerializer):
    class Meta:
        model = BlockchainRecord
        fields = "__all__"
