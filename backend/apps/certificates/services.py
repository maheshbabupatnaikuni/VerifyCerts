import hashlib
import io
import os
import re
from datetime import date
from difflib import SequenceMatcher
from typing import Any

import cv2
import fitz
import numpy as np
import pytesseract
import qrcode
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.mail import EmailMessage
from django.db import transaction
from django.db.models import Case, IntegerField, Value, When
from django.utils import timezone

from blockchain_app.services import BlockchainClient
from blockchain_app.services_ipfs import upload_to_ipfs_if_enabled
from .models import Certificate, CertificateEvent, Student, University

# Configure pytesseract binary path from settings (Windows path in your environment).
pytesseract.pytesseract.tesseract_cmd = getattr(settings, "TESSERACT_CMD", pytesseract.pytesseract.tesseract_cmd)

FIELD_PATTERNS = {
    "student_name": r"(?:Student Name|Name)\s*[:\-]\s*([A-Za-z .]+)",
    "registration_number": r"(?:Registration Number|Reg(?:\.|istration)? No|Hall\s*Ticket\s*No|HallTicket\s*No|Roll No|HT No|S\.No)\s*[:\-]?\s*([A-Za-z0-9\-/]+)",
    "course": r"(?:Course|Program)\s*[:\-]\s*([A-Za-z0-9 .,\-&]+)",
    "department": r"(?:Department|Dept)\s*[:\-]\s*([A-Za-z0-9 .\-&]+)",
    "university_name": r"(?:University|Institute|Board of Technical Education)\s*[:\-]?\s*([A-Za-z0-9 .\-&]+)",
    "certificate_serial_number": r"(?:Serial Number|Certificate No|Certificate Number|SI\.?\s*No|Sl\.?\s*No|PC\.?\s*No|Provisional Certificate No)\s*[:\-]?\s*([A-Za-z0-9\-/]+)",
    "issue_date": r"(?:Issue Date|Date of Issue)\s*[:\-]\s*([0-9]{1,2}[\-/][0-9]{1,2}[\-/][0-9]{2,4})",
    "graduation_year": r"(?:Graduation Year|Year)\s*[:\-]\s*([0-9]{4})",
}


def _normalize_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _detect_file_kind(filename: str) -> str:
    ext = os.path.splitext((filename or "").lower())[1]
    if ext == ".pdf":
        return "pdf"
    if ext in {".png", ".jpg", ".jpeg", ".webp"}:
        return "image"
    return "unknown"


def _estimate_page_count(binary: bytes) -> int:
    try:
        doc = fitz.open(stream=binary, filetype="pdf")
        count = len(doc)
        doc.close()
        return max(1, count)
    except Exception:
        return 1


def _normalize_common_ocr_typos(value: str) -> str:
    if not value:
        return value
    corrections = {
        "technolociy": "technology",
        "ngineering": "engineering",
        "gurajada": "gurajada",
    }
    out = value
    for wrong, right in corrections.items():
        out = re.sub(rf"\b{re.escape(wrong)}\b", right, out, flags=re.IGNORECASE)
    return out


def _is_placeholder_name(value: str | None) -> bool:
    if not value:
        return True
    lowered = _normalize_spaces(value).lower()
    return lowered in {"unknown", "na", "n/a", "student", "not available"}


def derive_institution_email(registration_number: str | None) -> str:
    reg = (registration_number or "").strip().replace(" ", "")
    if not reg:
        return ""
    return f"{reg}@{settings.INSTITUTION_EMAIL_DOMAIN}"


class OCRService:
    """
    OCR extraction engine with multi-variant preprocessing.

    Key idea:
    - First try native PDF text extraction (fast and accurate when embedded text exists).
    - If weak, run image OCR on multiple threshold variants and pick best scored result.
    """

    TESSERACT_CONFIGS = ["--oem 3 --psm 6", "--oem 3 --psm 4", "--oem 1 --psm 11"]
    TESSERACT_CONFIGS_ALT = ["--oem 3 --psm 11", "--oem 3 --psm 12", "--oem 1 --psm 3"]

    @staticmethod
    def _text_quality_score(text: str) -> int:
        """Heuristic score to rank OCR outputs and avoid noisy garbage text."""
        if not text:
            return 0

        alnum = sum(ch.isalnum() for ch in text)
        printable = sum(ch.isprintable() for ch in text)
        ratio = (alnum / max(1, printable))

        score = int(ratio * 20)
        checks = [
            r"cert\w*\s+that",
            r"diploma",
            r"engg|engineering",
            r"\b[0-9]{7,12}\b",
            r"\b20[0-9]{2}\b",
            r"son|daughter|s/o|d/o",
        ]
        for pattern in checks:
            if re.search(pattern, text, re.IGNORECASE):
                score += 3

        # Penalize OCR garbage-like symbol runs.
        if re.search(r"[^\w\s]{6,}", text):
            score -= 8
        return score

    @staticmethod
    def _preprocess_variants(image: np.ndarray) -> list[np.ndarray]:
        """Generate multiple grayscale/threshold views to improve OCR across different certificate styles."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        denoised = cv2.fastNlMeansDenoising(gray, h=10)
        blur = cv2.GaussianBlur(denoised, (3, 3), 0)
        otsu = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        adaptive = cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 35, 11)
        return [gray, otsu, adaptive]

    @classmethod
    def _ocr_image_best(cls, image: np.ndarray) -> str:
        """Run OCR across variants+configs and return best candidate by quality score."""
        candidates: list[tuple[int, str]] = []
        for variant in cls._preprocess_variants(image):
            for config in cls.TESSERACT_CONFIGS:
                text = pytesseract.image_to_string(variant, config=config)
                candidates.append((cls._text_quality_score(text), text))

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1] if candidates else ""

    @classmethod
    def _ocr_image_best_alt(cls, image: np.ndarray) -> str:
        """Alternate OCR profile used for auto-reprocess on low-confidence outputs."""
        candidates: list[tuple[int, str]] = []
        for variant in cls._preprocess_variants(image):
            for config in cls.TESSERACT_CONFIGS_ALT:
                text = pytesseract.image_to_string(variant, config=config)
                candidates.append((cls._text_quality_score(text), text))
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1] if candidates else ""

    @classmethod
    def _extract_regions_text(cls, image: np.ndarray) -> str:
        """Region-based OCR pass (header/body/footer) to recover fields missed in full-page scan."""
        h, w = image.shape[:2]
        regions = [
            image[0 : int(h * 0.40), 0:w],
            image[int(h * 0.35) : int(h * 0.75), 0:w],
            image[int(h * 0.70) : h, 0:w],
        ]
        return "\n".join(cls._ocr_image_best(reg) for reg in regions)

    @classmethod
    def extract_text(cls, pdf_bytes: bytes, alternate: bool = False) -> str:
        """
        Extract raw text from input bytes.

        Supports:
        - PDF (native text + OCR fallback)
        - Image bytes (direct OCR decode fallback)
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            native_parts = []
            ocr_parts = []

            for page in doc[: min(len(doc), 2)]:
                native = page.get_text("text") or ""
                if native.strip():
                    native_parts.append(native)

            native_text = _normalize_spaces("\n".join(native_parts))
            # Prefer native text if it looks meaningful.
            if cls._text_quality_score(native_text) >= 16 and len(native_text) > 80:
                doc.close()
                return native_text

            # OCR pass only when native extraction is weak.
            for page in doc[: min(len(doc), 2)]:
                for scale in ([2.8, 2.2] if alternate else [2.0]):
                    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                    img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        if alternate:
                            ocr_parts.append(cls._ocr_image_best_alt(img))
                        else:
                            ocr_parts.append(cls._ocr_image_best(img))
                        ocr_parts.append(cls._extract_regions_text(img))

            ocr_text = _normalize_spaces("\n".join(ocr_parts))

            # One extra high-res attempt only when OCR is still weak.
            if cls._text_quality_score(ocr_text) < 14:
                for page in doc[:1]:
                    pix = page.get_pixmap(matrix=fitz.Matrix(2.8, 2.8), alpha=False)
                    img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
                    if img is not None:
                        ocr_text = _normalize_spaces(ocr_text + "\n" + cls._ocr_image_best(img))

            doc.close()

            # If OCR quality is high, use it; else keep merged fallback.
            if cls._text_quality_score(ocr_text) >= 14:
                return ocr_text

            return _normalize_spaces((native_text + "\n" + ocr_text).strip())
        except Exception:
            pass

        np_data = np.frombuffer(pdf_bytes, np.uint8)
        image = cv2.imdecode(np_data, cv2.IMREAD_COLOR)
        if image is not None:
            return cls._ocr_image_best_alt(image) if alternate else cls._ocr_image_best(image)

        return pdf_bytes.decode("utf-8", errors="ignore")

    @classmethod
    def extract_fields(cls, pdf_bytes: bytes, alternate: bool = False) -> dict[str, Any]:
        """
        Parse semantic fields from extracted OCR/native text using regex + fallback heuristics.

        Returns dictionary containing:
        - raw_text
        - recognized fields (name, reg_no, course, dept, year, etc.)
        - additional metadata useful for review.
        """
        text = cls.extract_text(pdf_bytes, alternate=alternate)
        extracted: dict[str, Any] = {"raw_text": text}

        for key, pattern in FIELD_PATTERNS.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                extracted[key] = _normalize_spaces(match.group(1))

        if "student_name" not in extracted:
            cert_line = re.search(
                r"(?:This\s+is\s+to\s+cert\w*\s+that|This\s+cert\w*\s+that|that)\s+([A-Za-z ./]{3,140}?)\s+(?:S/o|D/o|Son|Daughter|Son/Daughter|has|having)",
                text,
                re.IGNORECASE,
            )
            if cert_line:
                extracted["student_name"] = cert_line.group(1)

        if "student_name" not in extracted:
            formal_name = re.search(
                r"(?:Mr\.?/Ms\.?|Mr\.?|Ms\.?)\s*\.?\s*([A-Za-z ]{3,120})\s+(?:Son|Daughter|Son/Daughter)",
                text,
                re.IGNORECASE,
            )
            if formal_name:
                extracted["student_name"] = formal_name.group(1)

        if "course" not in extracted:
            diploma_match = re.search(r"(Diploma\s*(?:in)?\s+[A-Za-z &]+?(?:ENGG|ENGINEERING))", text, re.IGNORECASE)
            if diploma_match:
                extracted["course"] = diploma_match.group(1)

        if "department" not in extracted and extracted.get("course"):
            extracted["department"] = extracted["course"].replace("Diploma", "").replace("in", "", 1).strip()

        if "registration_number" not in extracted:
            reg_guess = re.search(r"\b([0-9]{7,12})\b", text)
            if reg_guess:
                extracted["registration_number"] = reg_guess.group(1)

        if "registration_number" not in extracted:
            hallticket = re.search(r"Hall\s*Ticket\s*No\.?\s*[:\-]?\s*([A-Za-z0-9\-]+)", text, re.IGNORECASE)
            if hallticket:
                extracted["registration_number"] = hallticket.group(1)

        if "certificate_serial_number" not in extracted:
            pc_no = re.search(r"PC\.?\s*No\.?\s*[:\-]?\s*([A-Za-z0-9\-]+)", text, re.IGNORECASE)
            if pc_no:
                extracted["certificate_serial_number"] = pc_no.group(1)
                extracted["original_pc_number"] = pc_no.group(1)
            else:
                extracted["original_pc_number"] = None

        if "certificate_serial_number" not in extracted:
            sl_no = re.search(r"(?:SI|Sl)\.?\s*No\.?\s*[:\-]?\s*([A-Za-z0-9\-]+)", text, re.IGNORECASE)
            if sl_no:
                extracted["certificate_serial_number"] = sl_no.group(1)
                extracted["university_serial_number"] = sl_no.group(1)

        if "university_serial_number" not in extracted:
            sl_no = re.search(r"(?:SI|Sl)\.?\s*No\.?\s*[:\-]?\s*([A-Za-z0-9\-]+)", text, re.IGNORECASE)
            if sl_no:
                extracted["university_serial_number"] = sl_no.group(1)

        if "original_pc_number" not in extracted:
            pc_no = re.search(r"PC\.?\s*No\.?\s*[:\-]?\s*([A-Za-z0-9\-]+)", text, re.IGNORECASE)
            if pc_no:
                extracted["original_pc_number"] = pc_no.group(1)

        if "graduation_year" not in extracted:
            month_year = re.search(
                r"(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+(20\d{2})",
                text,
                re.IGNORECASE,
            )
            if month_year:
                extracted["graduation_year"] = int(month_year.group(1))
            else:
                years = [int(y) for y in re.findall(r"(20\d{2})", text)]
                valid_years = [y for y in years if 2000 <= y <= date.today().year + 1]
                if valid_years:
                    extracted["graduation_year"] = max(valid_years)

        if "student_name" not in extracted:
            declares_name = re.search(r"declares[^A-Za-z]{0,20}([A-Za-z ]{3,80}?)\s+a\s+(?:Bachelor|Master)", text, re.IGNORECASE)
            if declares_name:
                extracted["student_name"] = declares_name.group(1)

        if "course" not in extracted:
            degree_match = re.search(r"\b(Bachelor\s+of\s+Technology|Master\s+of\s+Technology|B\.\s*Tech|M\.\s*Tech)\b", text, re.IGNORECASE)
            if degree_match:
                extracted["course"] = degree_match.group(1)

        if "department" not in extracted:
            branch_match = re.search(r"Branch\s+([A-Za-z &]{3,})", text, re.IGNORECASE)
            if branch_match:
                extracted["department"] = re.split(r"\b(?:DGPA|Year|Dated)\b", _normalize_spaces(branch_match.group(1)), maxsplit=1)[0].strip()

        if "department" not in extracted:
            paren_dept = re.search(r"B\.?\s*TECH\s*\(([^)]+)\)", text, re.IGNORECASE)
            if paren_dept:
                extracted["department"] = _normalize_spaces(paren_dept.group(1))
                if extracted.get("course") and extracted["course"].upper().startswith("B.TECH"):
                    extracted["course"] = f"B.Tech ({extracted['department']})"

        if extracted.get("university_name"):
            extracted["university_name"] = re.split(r"\b(?:Branch|DGPA|Year|Dated)\b", extracted["university_name"], maxsplit=1)[0].strip()
            extracted["university_name"] = _normalize_common_ocr_typos(extracted["university_name"])

        if not extracted.get("university_name") or extracted.get("university_name", "").upper().startswith("OF "):
            institute_line = re.search(r"([A-Za-z ]+INSTITUTE OF [A-Za-z ]+)", text, re.IGNORECASE)
            if institute_line:
                extracted["university_name"] = _normalize_spaces(institute_line.group(1)).title()
                extracted["university_name"] = _normalize_common_ocr_typos(extracted["university_name"])

        if extracted.get("student_name"):
            cleaned_name = re.sub(r"[^A-Za-z .]", "", extracted["student_name"])
            cleaned_name = _normalize_spaces(cleaned_name)
            cleaned_name = re.sub(r"^mr\.?\s*ms\.?\s*", "", cleaned_name, flags=re.IGNORECASE)
            cleaned_name = re.sub(r"^mr\.?\s*/?\s*ms\.?\s*", "", cleaned_name, flags=re.IGNORECASE)
            cleaned_name = re.sub(r"^(mr|ms|mrs)\.?\s+", "", cleaned_name, flags=re.IGNORECASE)
            cleaned_name = re.sub(r"\b[A-Za-z]\b$", "", cleaned_name).strip()
            extracted["student_name"] = cleaned_name.title()

        if extracted.get("course"):
            course_val = _normalize_spaces(extracted["course"])
            extracted["course"] = re.sub(r"^Diplomain\s+", "Diploma in ", course_val, flags=re.IGNORECASE)
            extracted["course"] = _normalize_common_ocr_typos(extracted["course"])

        return extracted


def record_certificate_event(certificate: Certificate, event_type: str, message: str = "", metadata: dict[str, Any] | None = None) -> None:
    """Append a timeline event for end-to-end audit trail and reviewer visibility."""
    CertificateEvent.objects.create(
        certificate=certificate,
        event_type=event_type,
        message=message[:255],
        metadata=metadata or {},
    )


def compute_confidence(extracted: dict[str, Any]) -> tuple[float, list[str]]:
    """
    Compute OCR extraction confidence from required fields.

    Returns:
    - confidence score in range [0, 1]
    - warnings list for missing/weak fields
    """
    warnings: list[str] = []
    score = 0.0
    weights = {
        "student_name": 0.20,
        "registration_number": 0.20,
        "course": 0.20,
        "department": 0.10,
        "graduation_year": 0.15,
        "university_name": 0.05,
        "certificate_serial_number": 0.05,
        "issue_date": 0.05,
    }

    for field, wt in weights.items():
        if extracted.get(field):
            score += wt
        else:
            warnings.append(f"Missing {field}")

    score = max(0.0, min(1.0, score))
    return round(score, 3), warnings


class AIEnhancementService:
    """Placeholder hooks for advanced AI modules (layout detection, forgery checks)."""
    @staticmethod
    def detect_layout(_pdf_bytes: bytes) -> dict[str, Any]:
        return {"layout": "default", "confidence": 0.5}

    @staticmethod
    def detect_forgery(_pdf_bytes: bytes) -> dict[str, Any]:
        return {"is_suspicious": False, "confidence": 0.2, "signals": []}


def parse_issue_date(raw_date: str | None):
    if not raw_date:
        return None
    for sep in ["-", "/"]:
        parts = raw_date.split(sep)
        if len(parts) == 3:
            day, month, year = parts
            if len(year) == 2:
                year = f"20{year}"
            try:
                return date(int(year), int(month), int(day))
            except ValueError:
                return None
    return None


def generate_certificate_id(university: University, department: str, graduation_year: int) -> str:
    """Generate unique institution-scoped certificate ID with configured format."""
    dept_code = "".join(ch for ch in department.upper() if ch.isalnum())[:4] or "GEN"
    with transaction.atomic():
        sequence = (
            Certificate.objects.select_for_update()
            .filter(university=university, graduation_year=graduation_year, department__iexact=department)
            .count()
            + 1
        )
    return settings.CERTIFICATE_ID_FORMAT.format(prefix=university.prefix, year=graduation_year, dept=dept_code, seq=sequence)


def generate_hash(payload: dict[str, Any]) -> str:
    """Canonical SHA256 hash over core certificate identity fields."""
    base_data = "|".join([
        payload.get("student_name", ""),
        payload.get("registration_number", ""),
        payload.get("course", ""),
        payload.get("certificate_serial_number", ""),
        str(payload.get("graduation_year", "")),
    ])
    return hashlib.sha256(base_data.encode("utf-8")).hexdigest()


def build_qr_image(certificate_id: str) -> ContentFile:
    """Create QR code pointing to public verification URL for this certificate."""
    verify_url = f"{settings.VERIFY_BASE_URL.rstrip('/')}/{certificate_id}"
    qr_img = qrcode.make(verify_url)
    buffer = io.BytesIO()
    qr_img.save(buffer, format="PNG")
    return ContentFile(buffer.getvalue(), name=f"{certificate_id}.png")


def _build_college_certificate_pdf_bytes(certificate: Certificate, qr_bytes: bytes | None = None) -> bytes:
    from blockchain_app.models import BlockchainRecord
    from branding.models import BrandingConfigModel

    def _line(page_obj, yv: float, label: str, value: str, label_size: int = 12, value_size: int = 13):
        page_obj.insert_text((78, yv), f"{label}:", fontsize=label_size, fontname="helv", color=(0.12, 0.16, 0.28))
        page_obj.insert_textbox(
            fitz.Rect(260, yv - 14, 520, yv + 44),
            value or "-",
            fontsize=value_size,
            fontname="helv",
            color=(0.08, 0.12, 0.23),
        )

    branding = BrandingConfigModel.objects.first()
    # Keep wording chain-agnostic in the generated report so stale network names
    # (for example legacy Mumbai/Amoy labels) never appear in student artifacts.
    network_name = "Institution Verification Ledger"
    tx = (
        BlockchainRecord.objects.filter(certificate_id=certificate.certificate_id, status="stored")
        .order_by("-created_at")
        .first()
    )
    tx_hash = tx.transaction_hash if tx else "Pending blockchain transaction"

    serial_number = (
        (certificate.extracted_data or {}).get("university_serial_number")
        or certificate.certificate_serial_number
        or "-"
    )
    original_pc_number = (
        (certificate.extracted_data or {}).get("original_pc_number")
        or certificate.certificate_serial_number
        or "-"
    )

    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4 points

    page.draw_rect(fitz.Rect(16, 16, 579, 826), color=(0.68, 0.75, 0.86), width=2)
    page.draw_rect(fitz.Rect(28, 28, 567, 814), color=(0.42, 0.54, 0.72), width=1.5)
    page.draw_rect(fitz.Rect(40, 40, 555, 802), color=(0.85, 0.9, 0.96), width=0.8)

    page.draw_rect(fitz.Rect(56, 54, 539, 166), color=(0.86, 0.9, 0.96), fill=(0.97, 0.98, 1), width=1)
    page.insert_text((220, 78), "VerifyCerts", fontsize=28, fontname="helv", color=(0.08, 0.22, 0.45))
    page.insert_text(
        (76, 111),
        (branding.college_name if branding else certificate.university_name) or "Example University",
        fontsize=14,
        fontname="helv",
        color=(0.08, 0.14, 0.28),
    )
    page.insert_text((122, 135), "Blockchain Certificate Verification System", fontsize=10.5, fontname="helv", color=(0.14, 0.2, 0.35))
    page.insert_text((64, 160), "DIGITAL CERTIFICATE AUTHENTICITY REPORT", fontsize=17, fontname="times-bold", color=(0.1, 0.18, 0.35))

    page.draw_rect(fitz.Rect(58, 182, 537, 532), color=(0.74, 0.84, 0.94), fill=(0.98, 0.99, 1), width=1.2)
    page.insert_text((78, 222), "Student Name", fontsize=12, fontname="helv", color=(0.12, 0.16, 0.28))
    page.insert_textbox(
        fitz.Rect(78, 232, 530, 276),
        (certificate.student_name or "-"),
        fontsize=17,
        fontname="times-bold",
        color=(0.08, 0.12, 0.22),
    )

    _line(page, 282, "Registration Number", certificate.registration_number, value_size=16)
    _line(page, 327, "Course", certificate.course, value_size=14)
    _line(page, 392, "Institution", certificate.university_name, value_size=14)
    _line(page, 437, "Graduation Year", str(certificate.graduation_year), value_size=18)

    page.draw_line(fitz.Point(70, 462), fitz.Point(525, 462), color=(0.8, 0.84, 0.9), width=1)
    _line(page, 488, "Certificate Serial Number", str(serial_number), value_size=15)
    _line(page, 513, "Original Certificate PC Number", str(original_pc_number), value_size=15)
    _line(page, 538, "Verification Certificate ID", certificate.certificate_id, value_size=15)

    page.draw_rect(fitz.Rect(58, 550, 537, 688), color=(0.74, 0.84, 0.94), fill=(0.96, 0.98, 1), width=1.2)
    page.draw_rect(fitz.Rect(58, 550, 537, 575), color=(0.55, 0.66, 0.8), fill=(0.55, 0.66, 0.8), width=0)
    page.insert_text((174, 568), "BLOCKCHAIN VERIFICATION", fontsize=15, fontname="helv", color=(1, 1, 1))

    page.insert_text((78, 602), "Certificate Hash", fontsize=12, fontname="helv", color=(0.12, 0.16, 0.28))
    page.insert_textbox(fitz.Rect(240, 586, 400, 642), certificate.certificate_hash, fontsize=11, fontname="helv", color=(0.08, 0.12, 0.23))
    page.insert_text((78, 635), "Verification Network", fontsize=12, fontname="helv", color=(0.12, 0.16, 0.28))
    page.insert_text((240, 635), network_name, fontsize=14, fontname="helv", color=(0.19, 0.29, 0.66))
    page.insert_text((78, 662), "Transaction Hash", fontsize=12, fontname="helv", color=(0.12, 0.16, 0.28))
    page.insert_textbox(fitz.Rect(240, 648, 400, 686), tx_hash, fontsize=11, fontname="helv", color=(0.08, 0.12, 0.23))

    verify_url = f"{settings.VERIFY_BASE_URL.rstrip('/')}/{certificate.certificate_id}"
    page.insert_text((78, 705), "CERTIFICATE VERIFIED", fontsize=17, fontname="helv", color=(0.12, 0.46, 0.28))
    page.insert_text((78, 728), "This certificate record is authentic and registered on blockchain.", fontsize=11, fontname="helv", color=(0.18, 0.21, 0.3))
    page.insert_textbox(
        fitz.Rect(78, 740, 536, 760),
        f"Verify URL: {verify_url}",
        fontsize=8.5,
        fontname="helv",
        color=(0.08, 0.3, 0.53),
    )

    if qr_bytes is None and certificate.qr_code:
        qr_bytes = b""
        try:
            if getattr(certificate.qr_code, "file", None):
                qr_bytes = certificate.qr_code.file.read()
            else:
                certificate.qr_code.open("rb")
                qr_bytes = certificate.qr_code.read()
                certificate.qr_code.close()
        except Exception:
            qr_bytes = b""

    if qr_bytes:
        page.insert_image(fitz.Rect(420, 596, 520, 696), stream=qr_bytes)
        page.insert_text((425, 712), certificate.certificate_id, fontsize=8, fontname="helv", color=(0.08, 0.12, 0.23))

    page.draw_line(fitz.Point(58, 760), fitz.Point(537, 760), color=(0.8, 0.84, 0.9), width=1)
    page.insert_text((78, 778), "Generated by VerifyCerts", fontsize=10, fontname="times-italic", color=(0.2, 0.2, 0.28))
    page.insert_text(
        (78, 796),
        f"{(branding.college_name if branding else certificate.university_name) or 'Example University'}",
        fontsize=10,
        fontname="helv",
        color=(0.08, 0.14, 0.28),
    )

    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def _build_combined_certificate_pdf_bytes(certificate: Certificate, college_pdf_bytes: bytes) -> bytes:
    combined = fitz.open()

    if certificate.pdf_file:
        try:
            certificate.pdf_file.open("rb")
            original_bytes = certificate.pdf_file.read()
            certificate.pdf_file.close()
            original_doc = fitz.open(stream=original_bytes, filetype="pdf")
            combined.insert_pdf(original_doc)
            original_doc.close()
        except Exception:
            # Keep combined document generation resilient even if an older source PDF is malformed.
            pass

    college_doc = fitz.open(stream=college_pdf_bytes, filetype="pdf")
    combined.insert_pdf(college_doc)
    college_doc.close()

    output = combined.tobytes()
    combined.close()
    return output


def generate_student_documents(certificate: Certificate, qr_bytes: bytes | None = None) -> None:
    """Generate college verification PDF and combined (university+verification) PDF artifacts."""
    college_pdf_bytes = _build_college_certificate_pdf_bytes(certificate, qr_bytes=qr_bytes)
    combined_pdf_bytes = _build_combined_certificate_pdf_bytes(certificate, college_pdf_bytes)

    certificate.college_certificate_pdf.save(
        f"{certificate.certificate_id}_college.pdf",
        ContentFile(college_pdf_bytes),
        save=False,
    )
    certificate.combined_certificate_pdf.save(
        f"{certificate.certificate_id}_combined.pdf",
        ContentFile(combined_pdf_bytes),
        save=False,
    )


def ensure_generated_documents_current(certificate: Certificate) -> bool:
    """
    Keep issued/generated PDFs aligned with latest rendering template.

    Returns True when regeneration happened, False when documents are already current.
    """
    extracted = certificate.extracted_data if isinstance(certificate.extracted_data, dict) else {}
    current_version = "v3"
    previous_version = extracted.get("generated_doc_template_version")

    needs_regen = (
        previous_version != current_version
        or not certificate.college_certificate_pdf
        or not certificate.combined_certificate_pdf
    )
    if not needs_regen:
        return False

    generate_student_documents(certificate)
    extracted["generated_doc_template_version"] = current_version
    certificate.extracted_data = extracted
    certificate.save(update_fields=["college_certificate_pdf", "combined_certificate_pdf", "extracted_data"])
    record_certificate_event(
        certificate,
        "auto_reprocess",
        "Generated certificate files refreshed with latest layout.",
        {"generated_doc_template_version": current_version},
    )
    return True


def send_certificate_notification(certificate: Certificate, target_email: str | None = None) -> str:
    """
    Email generated certificate artifacts to student/institution mailbox.

    Fallback behavior:
    - if target_email missing -> use derived institutional email from reg no
    - if primary delivery fails -> try configured fallback email
    """
    recipient = (target_email or "").strip()
    # Default path: always institutional ID-based email.
    if not recipient:
        recipient = derive_institution_email(certificate.registration_number)
    fallback_email = (getattr(settings, "CERTIFICATE_EMAIL_FALLBACK", "") or "").strip()

    if not recipient:
        recipient = fallback_email
    if not recipient:
        return ""

    verify_url = f"{settings.VERIFY_BASE_URL.rstrip('/')}/{certificate.certificate_id}"
    body = (
        f"Dear {certificate.student_name},\n\n"
        "Your verification certificate documents are generated successfully.\n\n"
        f"Certificate ID: {certificate.certificate_id}\n"
        f"Registration/HallTicket No: {certificate.registration_number}\n"
        f"Verification URL: {verify_url}\n\n"
        "Attached:\n"
        "1) College Verification Certificate (generated)\n"
        "2) Combined Certificate (University + Verification)\n"
        "3) QR code image\n\n"
        "You can print this for physical use and keep digital copies.\n\n"
        "Regards,\n"
        "VerifyCerts Certificate Services"
    )

    email = EmailMessage(
        subject=f"Verification Certificate - {certificate.certificate_id}",
        body=body,
        from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.edu"),
        to=[recipient],
    )

    def _persist_email_status(success: bool, used_email: str, note: str):
        extracted = certificate.extracted_data if isinstance(certificate.extracted_data, dict) else {}
        history = extracted.get("email_delivery_history", [])
        history.append(
            {
                "at": timezone.now().isoformat(),
                "success": bool(success),
                "recipient": used_email,
                "note": note,
            }
        )
        extracted["email_delivery_history"] = history[-20:]
        extracted["email_last_status"] = "sent" if success else "failed"
        extracted["email_last_recipient"] = used_email
        extracted["email_last_at"] = timezone.now().isoformat()
        extracted["email_retry_count"] = len([h for h in extracted["email_delivery_history"] if not h.get("success")])
        certificate.extracted_data = extracted
        certificate.save(update_fields=["extracted_data"])

    def _attach_all(email_obj: EmailMessage):
        if certificate.college_certificate_pdf:
            certificate.college_certificate_pdf.open("rb")
            email_obj.attach(
                f"{certificate.certificate_id}_college.pdf",
                certificate.college_certificate_pdf.read(),
                "application/pdf",
            )
            certificate.college_certificate_pdf.close()
        if certificate.combined_certificate_pdf:
            certificate.combined_certificate_pdf.open("rb")
            email_obj.attach(
                f"{certificate.certificate_id}_combined.pdf",
                certificate.combined_certificate_pdf.read(),
                "application/pdf",
            )
            certificate.combined_certificate_pdf.close()
        if certificate.qr_code:
            certificate.qr_code.open("rb")
            email_obj.attach(
                f"{certificate.certificate_id}_qr.png",
                certificate.qr_code.read(),
                "image/png",
            )
            certificate.qr_code.close()

    try:
        _attach_all(email)
        email.send(fail_silently=False)
        record_certificate_event(certificate, "email_sent", f"Email sent to {recipient}", {"recipient": recipient})
        _persist_email_status(True, recipient, "Primary delivery successful")
        return recipient
    except Exception:
        # If primary send fails, auto fallback to configured backup email.
        _persist_email_status(False, recipient, "Primary delivery failed")
        if fallback_email and fallback_email.lower() != recipient.lower():
            try:
                fallback = EmailMessage(
                    subject=f"Fallback Copy: Verification Certificate - {certificate.certificate_id}",
                    body=body,
                    from_email=getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@example.edu"),
                    to=[fallback_email],
                )
                _attach_all(fallback)
                fallback.send(fail_silently=False)
                record_certificate_event(
                    certificate,
                    "email_sent",
                    f"Primary email failed; sent to fallback {fallback_email}",
                    {"recipient": fallback_email, "fallback": True},
                )
                _persist_email_status(True, fallback_email, "Fallback delivery successful")
                return fallback_email
            except Exception:
                record_certificate_event(certificate, "email_failed", "Email send failed for primary and fallback")
                _persist_email_status(False, fallback_email, "Fallback delivery failed")
                return ""
        record_certificate_event(certificate, "email_failed", "Email send failed")
        return ""


def canonicalize_with_master(university: University, extracted: dict[str, Any]) -> tuple[dict[str, Any], Student | None, list[str]]:
    """
    Align OCR fields with trusted master student data when available.

    This reduces OCR noise and improves consistency before hashing/anchoring.
    """
    warnings: list[str] = []
    reg_no = extracted.get("registration_number")
    if not reg_no:
        return extracted, None, warnings

    student = Student.objects.filter(university=university, registration_number=reg_no).first()
    if not student:
        return extracted, None, warnings

    extracted["master_student_match"] = True
    ocr_name = extracted.get("student_name", "")
    master_name_placeholder = _is_placeholder_name(student.name)

    if student.is_verified:
        extracted["student_name"] = student.name
        warnings.append("Applied verified student master data")
    elif ocr_name:
        extracted["ocr_student_name"] = ocr_name
        if master_name_placeholder:
            extracted["student_name"] = ocr_name
            warnings.append("Master student name is placeholder; used OCR name")
        else:
            sim = SequenceMatcher(None, ocr_name.lower(), student.name.lower()).ratio()
            if sim >= 0.92:
                extracted["student_name"] = student.name
            else:
                warnings.append(f"Master/OCR mismatch; kept OCR name ({ocr_name})")
    else:
        if not master_name_placeholder:
            extracted["student_name"] = student.name

    if not extracted.get("department") and student.department:
        extracted["department"] = student.department
    if not extracted.get("graduation_year") and student.year:
        extracted["graduation_year"] = student.year

    return extracted, student, warnings


def upsert_student(university: University, extracted: dict[str, Any]) -> Student:
    """Create/update student record from extracted certificate fields."""
    seed_name = extracted.get("student_name", "NA")
    registration_number = extracted.get("registration_number") or f"UNKNOWN-{hashlib.md5(seed_name.encode()).hexdigest()[:8]}"
    defaults = {
        "name": extracted.get("student_name", "Unknown"),
        "department": extracted.get("department", "General"),
        "year": int(extracted.get("graduation_year") or date.today().year),
        "email": derive_institution_email(registration_number),
        "university": university,
    }
    student, _ = Student.objects.update_or_create(registration_number=registration_number, defaults=defaults)
    return student


def refresh_student_master(student: Student, extracted: dict[str, Any]) -> None:
    """Backfill weak student master fields from fresh OCR data when student is not locked as verified."""
    if student.is_verified:
        return

    updates = []
    extracted_name = extracted.get("student_name")
    if extracted_name and _is_placeholder_name(student.name):
        student.name = extracted_name
        updates.append("name")

    extracted_department = extracted.get("department")
    if extracted_department and (not student.department or student.department.lower() == "general"):
        student.department = extracted_department
        updates.append("department")

    extracted_year = extracted.get("graduation_year")
    if extracted_year and (not student.year or student.year < 2000):
        student.year = int(extracted_year)
        updates.append("year")

    if not student.email:
        auto_email = derive_institution_email(extracted.get("registration_number") or student.registration_number)
        if auto_email:
            student.email = auto_email
            updates.append("email")

    if updates:
        student.save(update_fields=updates)


def process_certificate_file(pdf_file, university: University, force_blockchain: bool = True) -> Certificate:
    """
    Main ingestion pipeline for a single uploaded certificate file.

    Stages:
    1) OCR extraction + canonicalization
    2) Confidence + duplicate checks
    3) Certificate and assets generation (QR, PDFs)
    4) Optional blockchain anchoring
    5) Email notification
    """
    pdf_bytes = pdf_file.read()
    source_kind = _detect_file_kind(getattr(pdf_file, "name", ""))
    extracted = OCRService.extract_fields(pdf_bytes)

    extracted, matched_student, master_warnings = canonicalize_with_master(university, extracted)
    confidence_score, confidence_warnings = compute_confidence(extracted)

    extracted["layout_meta"] = AIEnhancementService.detect_layout(pdf_bytes)
    extracted["forgery_meta"] = AIEnhancementService.detect_forgery(pdf_bytes)
    extracted["ingestion_meta"] = {
        "source_file_name": getattr(pdf_file, "name", ""),
        "source_kind": source_kind,
        "estimated_pages": _estimate_page_count(pdf_bytes),
        "duplicate_reused": False,
    }
    extracted["confidence_score"] = confidence_score
    extracted["confidence_warnings"] = master_warnings + confidence_warnings

    student = matched_student or upsert_student(university, extracted)
    refresh_student_master(student, extracted)
    grad_year = int(extracted.get("graduation_year") or date.today().year)
    department = extracted.get("department", student.department)
    certificate_id = generate_certificate_id(university, department, grad_year)

    normalized_payload = {
        "student_name": extracted.get("student_name", student.name),
        "registration_number": extracted.get("registration_number", student.registration_number),
        "course": extracted.get("course", "Unknown Course"),
        "certificate_serial_number": extracted.get("certificate_serial_number", certificate_id),
        "graduation_year": grad_year,
    }

    critical_missing = [k for k in ["student_name", "registration_number", "course", "graduation_year"] if not extracted.get(k)]
    cert_hash = generate_hash(normalized_payload)

    # Guardrail: if an equivalent certificate already exists, reuse it instead of creating
    # a new pending duplicate that can confuse verifier flows.
    reusable_existing = (
        Certificate.objects.filter(university=university, certificate_hash=cert_hash)
        .annotate(
            _status_rank=Case(
                When(status="active", then=Value(0)),
                When(status="expired", then=Value(1)),
                When(status="needs_review", then=Value(2)),
                When(status="revoked", then=Value(3)),
                default=Value(4),
                output_field=IntegerField(),
            )
        )
        .order_by("_status_rank", "-created_at")
        .first()
    )
    if reusable_existing:
        extracted_existing = reusable_existing.extracted_data or {}
        duplicate_log = extracted_existing.get("duplicate_uploads", [])
        duplicate_log.append(
            {
                "source_file_name": getattr(pdf_file, "name", ""),
                "detected_at": date.today().isoformat(),
                "reason": "Equivalent canonical hash already exists; reused existing record.",
            }
        )
        extracted_existing["duplicate_uploads"] = duplicate_log[-20:]
        extracted_existing["ingestion_meta"] = {
            **(extracted_existing.get("ingestion_meta") or {}),
            "source_file_name": getattr(pdf_file, "name", ""),
            "source_kind": source_kind,
            "estimated_pages": _estimate_page_count(pdf_bytes),
            "duplicate_reused": True,
            "reused_certificate_id": reusable_existing.certificate_id,
        }
        reusable_existing.extracted_data = extracted_existing
        reusable_existing.save(update_fields=["extracted_data"])
        record_certificate_event(
            reusable_existing,
            "auto_reprocess",
            "Duplicate upload skipped; existing certificate reused.",
            {"source_file_name": getattr(pdf_file, "name", "")},
        )
        return reusable_existing

    duplicate_candidates = list(
        Certificate.objects.filter(
            registration_number=normalized_payload["registration_number"],
            graduation_year=normalized_payload["graduation_year"],
        )
        .order_by("-created_at")
        .values_list("certificate_id", flat=True)[:5]
    )
    hash_duplicates = list(
        Certificate.objects.filter(certificate_hash=cert_hash)
        .order_by("-created_at")
        .values_list("certificate_id", flat=True)[:5]
    )
    if duplicate_candidates or hash_duplicates:
        extracted["duplicate_candidates"] = sorted(set(duplicate_candidates + hash_duplicates))
        extracted["confidence_warnings"].append("Potential duplicate detected. Review before approval.")

    needs_review = confidence_score < 0.72 or bool(critical_missing)
    if duplicate_candidates or hash_duplicates:
        needs_review = True

    cert = Certificate.objects.create(
        university=university,
        student=student,
        certificate_id=certificate_id,
        student_name=normalized_payload["student_name"],
        registration_number=normalized_payload["registration_number"],
        course=normalized_payload["course"],
        department=department,
        university_name=extracted.get("university_name", university.name),
        certificate_serial_number=normalized_payload["certificate_serial_number"],
        issue_date=parse_issue_date(extracted.get("issue_date")),
        graduation_year=grad_year,
        certificate_hash=cert_hash,
        confidence_score=confidence_score,
        status="needs_review" if needs_review else "active",
        extracted_data=extracted,
    )
    record_certificate_event(cert, "uploaded", "Certificate uploaded by admin")
    record_certificate_event(cert, "ocr_extracted", "OCR extraction completed", {"confidence": confidence_score})
    if duplicate_candidates or hash_duplicates:
        record_certificate_event(
            cert,
            "auto_reprocess",
            "Potential duplicate candidates found",
            {"candidates": extracted.get("duplicate_candidates", [])},
        )

    cert.pdf_file.save(pdf_file.name, ContentFile(pdf_bytes), save=False)
    qr_file = build_qr_image(certificate_id)
    qr_bytes = qr_file.read()
    qr_file.seek(0)
    cert.qr_code.save(f"{certificate_id}.png", qr_file, save=False)
    generate_student_documents(cert, qr_bytes=qr_bytes)
    cert.ipfs_cid = upload_to_ipfs_if_enabled(pdf_bytes, file_name=pdf_file.name) or ""
    cert.save()

    if cert.status == "needs_review" and confidence_score < 0.72:
        alt_extracted = OCRService.extract_fields(pdf_bytes, alternate=True)
        alt_extracted, _matched_alt, _warnings_alt = canonicalize_with_master(university, alt_extracted)
        alt_score, alt_warnings = compute_confidence(alt_extracted)
        if alt_score > confidence_score:
            cert.student_name = alt_extracted.get("student_name", cert.student_name)
            cert.registration_number = alt_extracted.get("registration_number", cert.registration_number)
            cert.course = alt_extracted.get("course", cert.course)
            cert.department = alt_extracted.get("department", cert.department)
            cert.university_name = alt_extracted.get("university_name", cert.university_name)
            cert.graduation_year = int(alt_extracted.get("graduation_year") or cert.graduation_year)
            cert.certificate_hash = generate_hash(
                {
                    "student_name": cert.student_name,
                    "registration_number": cert.registration_number,
                    "course": cert.course,
                    "certificate_serial_number": cert.certificate_serial_number,
                    "graduation_year": cert.graduation_year,
                }
            )
            cert.confidence_score = alt_score
            alt_extracted["confidence_score"] = alt_score
            alt_extracted["confidence_warnings"] = alt_warnings
            cert.extracted_data = alt_extracted
            if alt_score >= 0.72:
                cert.status = "active"
            cert.save(update_fields=["student_name", "registration_number", "course", "department", "university_name", "graduation_year", "certificate_hash", "confidence_score", "extracted_data", "status"])
            generate_student_documents(cert, qr_bytes=qr_bytes)
            record_certificate_event(cert, "auto_reprocess", "Auto reprocess improved confidence", {"before": confidence_score, "after": alt_score})
        else:
            record_certificate_event(cert, "auto_reprocess", "Auto reprocess attempted, no improvement", {"before": confidence_score, "after": alt_score})

    if force_blockchain and cert.status == "active":
        try:
            BlockchainClient().store_certificate_hash(certificate_id, cert.certificate_hash)
            record_certificate_event(cert, "anchored", "Anchored on blockchain")
        except Exception as exc:
            extracted_data = cert.extracted_data or {}
            warnings = extracted_data.get("confidence_warnings", [])
            warnings.append(f"Blockchain anchor failed: {exc.__class__.__name__}")
            extracted_data["confidence_warnings"] = warnings
            cert.extracted_data = extracted_data
            cert.status = "needs_review"
            cert.save(update_fields=["status", "extracted_data"])
            record_certificate_event(cert, "auto_reprocess", f"Blockchain anchor failed: {exc.__class__.__name__}")

    send_certificate_notification(cert)
    return cert


def process_batch_files(pdf_files: list, university: University) -> list[Certificate]:
    """Batch wrapper that runs single-file pipeline for each uploaded file."""
    return [process_certificate_file(file_obj, university=university, force_blockchain=True) for file_obj in pdf_files]


def reprocess_existing_certificate(certificate: Certificate) -> Certificate:
    """
    Manual/admin-triggered re-extraction pipeline for a previously uploaded certificate.
    Keeps record in review state for explicit human confirmation.
    """
    if not certificate.pdf_file:
        raise ValueError("Certificate PDF is missing.")

    certificate.pdf_file.open("rb")
    pdf_bytes = certificate.pdf_file.read()
    certificate.pdf_file.close()

    extracted = OCRService.extract_fields(pdf_bytes)
    extracted, matched_student, master_warnings = canonicalize_with_master(certificate.university, extracted)
    confidence_score, confidence_warnings = compute_confidence(extracted)

    extracted["layout_meta"] = AIEnhancementService.detect_layout(pdf_bytes)
    extracted["forgery_meta"] = AIEnhancementService.detect_forgery(pdf_bytes)
    extracted["confidence_score"] = confidence_score
    extracted["confidence_warnings"] = master_warnings + confidence_warnings

    student = matched_student or certificate.student or upsert_student(certificate.university, extracted)
    refresh_student_master(student, extracted)

    grad_year = int(extracted.get("graduation_year") or certificate.graduation_year or date.today().year)
    department = extracted.get("department") or certificate.department or student.department or "General"
    normalized_payload = {
        "student_name": extracted.get("student_name") or student.name or "Unknown",
        "registration_number": extracted.get("registration_number") or student.registration_number,
        "course": extracted.get("course") or "Unknown Course",
        "certificate_serial_number": extracted.get("certificate_serial_number") or certificate.certificate_serial_number or certificate.certificate_id,
        "graduation_year": grad_year,
    }

    critical_missing = [k for k in ["student_name", "registration_number", "course", "graduation_year"] if not extracted.get(k)]
    needs_review = confidence_score < 0.72 or bool(critical_missing)

    certificate.student = student
    certificate.student_name = normalized_payload["student_name"]
    certificate.registration_number = normalized_payload["registration_number"]
    certificate.course = normalized_payload["course"]
    certificate.department = department
    certificate.university_name = extracted.get("university_name") or certificate.university_name or certificate.university.name
    certificate.certificate_serial_number = normalized_payload["certificate_serial_number"]
    certificate.issue_date = parse_issue_date(extracted.get("issue_date")) or certificate.issue_date
    certificate.graduation_year = grad_year
    certificate.certificate_hash = generate_hash(normalized_payload)
    certificate.confidence_score = confidence_score
    certificate.extracted_data = extracted
    # Reprocess is a review step; keep it pending for explicit human approval.
    certificate.status = "needs_review"
    generate_student_documents(certificate)
    certificate.save()
    return certificate



