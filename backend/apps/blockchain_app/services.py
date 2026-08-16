import json
import time
from pathlib import Path

from django.conf import settings
from django.db import transaction
from web3 import Web3
from web3.exceptions import Web3RPCError

from certificates.models import Certificate
from .models import BlockchainRecord


class BlockchainClient:
    """
    Single gateway for all blockchain interactions used by the app.

    Responsibilities:
    - Load contract ABI and build a contract object.
    - Estimate transaction cost for storing certificate hash.
    - Write certificate hash to chain (when signer is configured).
    - Read certificate hash from chain for verification.
    - Create local fallback records for revoke/offline scenarios.
    """

    def __init__(self):
        # Pull all chain settings from Django settings so no hard-coded secrets live in code.
        self.provider_uri = settings.WEB3_PROVIDER_URI
        self.contract_address = settings.CONTRACT_ADDRESS
        self.issuer_private_key = settings.ISSUER_PRIVATE_KEY
        self.issuer_wallet_address = settings.ISSUER_WALLET_ADDRESS
        self.chain_id = settings.CHAIN_ID
        self.contract_abi = self._load_abi(settings.CONTRACT_ABI_PATH)
        self.web3 = Web3(Web3.HTTPProvider(self.provider_uri)) if self.provider_uri else None

    @staticmethod
    def _load_abi(path: str):
        """Read ABI JSON from disk. Returns [] if not found so caller can handle gracefully."""
        abi_path = Path(path)
        if abi_path.exists():
            with abi_path.open("r", encoding="utf-8") as abi_file:
                return json.load(abi_file)
        return []

    def _contract(self):
        """
        Build and return a web3 contract instance only when all required pieces are ready.
        Returns None for any missing configuration/connection.
        """
        if not (self.web3 and self.contract_address and self.contract_abi and self.web3.is_connected()):
            return None
        return self.web3.eth.contract(address=Web3.to_checksum_address(self.contract_address), abi=self.contract_abi)

    def estimate_store_cost(self, certificate_id: str, cert_hash: str) -> dict:
        """Estimate gas and balance sufficiency for storeCertificate(certificate_id, cert_hash)."""
        contract = self._contract()
        if not contract:
            return {"ok": False, "reason": "Blockchain is not connected or contract is not configured."}
        if not (self.issuer_wallet_address and self.web3):
            return {"ok": False, "reason": "Issuer wallet is not configured."}
        try:
            sender = Web3.to_checksum_address(self.issuer_wallet_address)
            gas = contract.functions.storeCertificate(certificate_id, cert_hash).estimate_gas({"from": sender})
            gas_price = self.web3.eth.gas_price
            required_wei = gas * gas_price
            balance_wei = self.web3.eth.get_balance(sender)
            return {
                "ok": True,
                "gas": gas,
                "gas_price_wei": int(gas_price),
                "required_wei": int(required_wei),
                "required_pol": float(self.web3.from_wei(required_wei, "ether")),
                "balance_wei": int(balance_wei),
                "balance_pol": float(self.web3.from_wei(balance_wei, "ether")),
                "has_funds": bool(balance_wei >= required_wei),
            }
        except Exception as exc:
            return {"ok": False, "reason": f"Cost estimation failed: {exc.__class__.__name__}"}

    def _fallback_record(self, certificate_id: str, cert_hash: str, status: str = "stored") -> BlockchainRecord:
        """
        Create an explicit local/off-chain record.
        Used to keep audit trail continuity when chain write is intentionally not performed.
        """
        tx_hash = f"offchain-{certificate_id}-{int(time.time())}"
        block_number = int(time.time())
        return BlockchainRecord.objects.create(
            certificate_id=certificate_id,
            transaction_hash=tx_hash,
            block_number=block_number,
            hash=cert_hash,
            issuer_address=self.issuer_wallet_address or "local-signer",
            status=status,
        )

    @transaction.atomic
    def store_certificate_hash(self, certificate_id: str, cert_hash: str) -> BlockchainRecord:
        """
        Persist certificate hash on blockchain smart contract and mirror that in BlockchainRecord.

        Flow:
        1) Safety checks (duplicate, config, signer, funds).
        2) Build + sign + broadcast transaction.
        3) Wait for receipt.
        4) Save DB record with tx/block metadata.
        """
        if BlockchainRecord.objects.filter(certificate_id=certificate_id, status="stored").exists():
            raise ValueError("Certificate already stored on blockchain record table.")

        contract = self._contract()
        if not contract:
            raise RuntimeError("Blockchain is not connected or contract configuration is incomplete.")
        if not (self.issuer_private_key and self.issuer_wallet_address):
            raise RuntimeError("Signer wallet configuration is incomplete.")

        cost = self.estimate_store_cost(certificate_id, cert_hash)
        if cost.get("ok") and not cost.get("has_funds"):
            raise RuntimeError(
                f"Insufficient POL for anchor. Need ~{cost.get('required_pol'):.6f} POL, available {cost.get('balance_pol'):.6f} POL."
            )

        nonce = self.web3.eth.get_transaction_count(self.issuer_wallet_address)
        txn = contract.functions.storeCertificate(certificate_id, cert_hash).build_transaction(
            {
                "chainId": self.chain_id,
                "gas": 300000,
                "gasPrice": self.web3.eth.gas_price,
                "nonce": nonce,
            }
        )
        try:
            signed_txn = self.web3.eth.account.sign_transaction(txn, private_key=self.issuer_private_key)
            tx_hash = self.web3.eth.send_raw_transaction(signed_txn.raw_transaction)
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        except Web3RPCError as exc:
            raise RuntimeError(f"Chain RPC rejected transaction: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Chain transaction failed: {exc.__class__.__name__}") from exc

        return BlockchainRecord.objects.create(
            certificate_id=certificate_id,
            transaction_hash=f"0x{tx_hash.hex()}",
            block_number=receipt.blockNumber,
            hash=cert_hash,
            issuer_address=self.issuer_wallet_address,
            status="stored",
        )

    def revoke_certificate(self, certificate_id: str) -> BlockchainRecord:
        """Current revoke behavior stores a local revoke marker for audit and UX continuity."""
        cert = Certificate.objects.get(certificate_id=certificate_id)
        return self._fallback_record(certificate_id, cert.certificate_hash, status="revoked")

    def get_chain_hash(self, certificate_id: str) -> str | None:
        """Read hash for certificate_id from chain; return None if missing/unreachable."""
        contract = self._contract()
        if contract:
            try:
                hash_value, _timestamp, _issuer, exists = contract.functions.getCertificate(certificate_id).call()
                if exists and hash_value:
                    return str(hash_value)
            except Exception:
                pass
        return None
