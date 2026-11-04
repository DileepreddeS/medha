"""Pinned sandbox image for skill verification/execution.

No runtime pip installs; versions pinned for reproducibility (spec §4).
"""

import modal

SKILL_IMAGE = modal.Image.from_registry("python:3.12-slim").uv_pip_install(
    "pandas==2.2.2",
    "numpy==1.26.4",
    "openpyxl==3.1.5",
    "pdfplumber==0.11.4",
    "pypdf==4.3.1",
    "matplotlib==3.9.2",
)
