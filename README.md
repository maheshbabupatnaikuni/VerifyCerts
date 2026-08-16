# VerifyCerts

VerifyCerts is a Django application for certificate ingestion, OCR-assisted metadata extraction, SHA-256 fingerprinting, QR verification, audit history, and optional anchoring to an Ethereum-compatible network.

## Run in GitHub Codespaces

[Open VerifyCerts in GitHub Codespaces](https://codespaces.new/maheshbabupatnaikuni/VerifyCerts?quickstart=1)

Create the codespace and wait for setup to complete. Python packages, Tesseract OCR, migrations, and the administrator account are prepared automatically. The private forwarded application opens on port `8000`.

- Username: `admin`
- Password: `test-only-codespace123`

## Run locally

Python 3.11 or newer and Tesseract OCR are required.

### PowerShell

```powershell
python -m venv '.venv'
& '.\.venv\Scripts\python.exe' -m pip install -r '.\requirements.txt'
& '.\.venv\Scripts\python.exe' '.\run_app.py'
```

Open `http://127.0.0.1:8000`.

Local credentials:

- Username: `admin`
- Password: `test-only-local-admin-password`

## Capabilities

- Administrator dashboard and account roles
- Single and batch certificate ingestion
- OCR extraction with Tesseract, OpenCV, and PyMuPDF
- SHA-256 certificate fingerprints
- QR-based public verification
- Verification history and analytics
- Revocation, expiry, review, and reporting workflows
- Optional Ethereum-compatible blockchain anchoring
- Optional IPFS integration
- Configurable institution branding
- SQLite for local use and PostgreSQL configuration for hosting

## Project structure

- `backend/apps/` — Django application modules
- `core/university_verify/` — settings, routes, WSGI, and ASGI
- `frontend/` — templates and static assets
- `blockchain/` — Solidity contract and ABI artifacts
- `deployment/` — Docker, PostgreSQL, Gunicorn, and Nginx configuration
- `runtime/` — ignored generated uploads and collected static files
- `database/` — ignored local SQLite database

## Configuration

Copy `.env.example` to `.env` when custom configuration is required. Keep `.env`, private keys, uploaded certificates, databases, and generated files out of version control.

Blockchain and IPFS integration are optional. Without an RPC provider and issuer key, the core OCR, certificate-management, hashing, QR, and local verification workflows remain available.

## Validation

```powershell
& '.\.venv\Scripts\python.exe' 'manage.py' check
& '.\.venv\Scripts\python.exe' 'manage.py' test accounts certificates verification blockchain_app branding --verbosity 2
& '.\.venv\Scripts\python.exe' '.\tools\repository_audit.py'
```

Only synthetic data should be used in public development environments. Do not upload real certificates or personal records to a shared codespace.
