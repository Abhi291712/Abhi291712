import os
import re

try:
    import PyPDF2
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False

try:
    from docx import Document
    DOCX_SUPPORT = True
except ImportError:
    DOCX_SUPPORT = False


def extract_text(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf" and PDF_SUPPORT:
        return _read_pdf(file_path)
    elif ext in (".docx", ".doc") and DOCX_SUPPORT:
        return _read_docx(file_path)
    return ""


def _read_pdf(path: str) -> str:
    text = []
    with open(path, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text.append(page.extract_text() or "")
    return "\n".join(text)


def _read_docx(path: str) -> str:
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def extract_phone(text: str) -> str:
    match = re.search(r"(\+?\d[\d\s\-().]{7,}\d)", text)
    return match.group(1).strip() if match else ""


def extract_name(text: str) -> str:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return lines[0] if lines else ""
