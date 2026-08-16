import requests
from django.conf import settings


def upload_to_ipfs_if_enabled(file_bytes: bytes, file_name: str) -> str | None:
    if not settings.PINATA_JWT:
        return None

    headers = {"Authorization": f"Bearer {settings.PINATA_JWT}"}
    files = {"file": (file_name, file_bytes, "application/pdf")}
    response = requests.post("https://api.pinata.cloud/pinning/pinFileToIPFS", headers=headers, files=files, timeout=30)
    if response.ok:
        return response.json().get("IpfsHash")
    return None
