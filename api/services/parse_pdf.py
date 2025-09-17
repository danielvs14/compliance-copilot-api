import pdfplumber
from typing import Optional

def extract_text_from_pdf(path: str) -> Optional[str]:
    try:
        with pdfplumber.open(path) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages).strip()
        return text if text else None
    except Exception:
        return None
