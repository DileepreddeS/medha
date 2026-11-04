"""Minimal single-page text-PDF builder for bench invoice fixtures.

Computes xref offsets programmatically; output is plain ASCII PDF 1.4
readable by pypdf/pdfplumber. Kept dependency-free on purpose.
"""

from __future__ import annotations


def _escape(text: str) -> bytes:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1")


def make_pdf(lines: list[str]) -> bytes:
    """Build a one-page PDF with the given text lines (12pt Helvetica)."""
    shown = b" 0 -20 Td ".join(b"(" + _escape(line) + b") Tj" for line in lines)
    content = b"BT /F1 12 Tf 72 720 Td " + shown + b" ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]"
        b" /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
    ]
    out: list[bytes] = [b"%PDF-1.4\n"]
    offsets = [0]
    for i, body in enumerate(objects, start=1):
        offsets.append(sum(len(x) for x in out))
        out.append(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = sum(len(x) for x in out)
    out.append(f"xref\n0 {len(objects) + 1}\n".encode())
    out.append(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.append(f"{off:010d} 00000 n \n".encode())
    out.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
        + f"startxref\n{xref_pos}\n%%EOF\n".encode()
    )
    return b"".join(out)
