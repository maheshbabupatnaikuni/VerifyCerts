from django.contrib import admin

from .models import BlockchainRecord


@admin.register(BlockchainRecord)
class BlockchainRecordAdmin(admin.ModelAdmin):
    list_display = ("certificate_id", "transaction_hash", "block_number", "status", "timestamp")
    search_fields = ("certificate_id", "transaction_hash")
    list_filter = ("status",)
