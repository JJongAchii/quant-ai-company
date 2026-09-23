"""Disposable parser subprocess. No credentials, OCR, attachments or PDF actions."""

import io
import json
import resource
import sys


def main():
    # Set limits before importing the parser or touching untrusted bytes. Production is Linux.
    if sys.platform != "linux":
        raise ValueError("quant_pdf_requires_linux_limits")
    resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (50, 50))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    from pypdf import PdfReader

    raw = sys.stdin.buffer.read(20 * 1024 * 1024 + 1)
    if len(raw) > 20 * 1024 * 1024 or not raw.startswith(b"%PDF-"):
        raise ValueError("invalid_pdf")
    reader = PdfReader(io.BytesIO(raw), strict=True)
    if reader.is_encrypted or not 1 <= len(reader.pages) <= 200:
        raise ValueError("encrypted_or_oversized_pdf")
    pages, total, truncated = [], 0, False
    for index, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        available = max(0, min(20000, 500000 - total))
        truncated = truncated or len(text) > available
        text = text[:available]
        total += len(text)
        pages.append({"location": f"PDF p.{index + 1}", "text": text})
    if total < 500 or sum(len(p["text"].strip()) < 40 for p in pages) > len(pages) // 3:
        raise ValueError("scanned_or_insufficient_pdf_text")
    print(json.dumps({"pages": pages, "truncated": truncated}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Do not leak document paths, binary data or parser logs in receipts.
        sys.exit(2)
