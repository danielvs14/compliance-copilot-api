from __future__ import annotations

import io
from typing import BinaryIO, Optional, Union

import pdfplumber


def extract_text_from_pdf(source: Union[str, bytes, BinaryIO]) -> Optional[str]:
    try:
        if isinstance(source, (bytes, bytearray)):
            buffer = io.BytesIO(source)
        elif hasattr(source, "read"):
            buffer = source
            buffer.seek(0)
        else:
            buffer = source

        with pdfplumber.open(buffer) as pdf:
            pages = [page.extract_text() or "" for page in pdf.pages]
        text = "\n".join(pages).strip()
        return text if text else None
    except Exception:
        return None
