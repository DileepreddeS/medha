"""PDF fixtures: structural validity + text roundtrip through pypdf."""

import re

import pytest

from bench.pdfgen import make_pdf


def test_structure_valid():
    data = make_pdf(["INVOICE #1001", "TOTAL: 214.50"])
    assert data.startswith(b"%PDF-1.4")
    assert data.rstrip().endswith(b"%%EOF")
    m = re.search(rb"startxref\s+(\d+)", data)
    assert m and data[int(m.group(1)) :].startswith(b"xref")
    for i in range(1, 6):
        assert f"{i} 0 obj".encode() in data


def test_text_roundtrip_with_pypdf():
    pytest.importorskip("pypdf")
    import io

    from pypdf import PdfReader

    data = make_pdf(["INVOICE #1001", "Supplier: Acme Beans", "TOTAL: 214.50"])
    text = PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert "INVOICE #1001" in text and "214.50" in text


def test_parens_escaped():
    data = make_pdf(["a(b)c"])
    assert b"a\\(b\\)c" in data
