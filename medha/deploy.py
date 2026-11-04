"""Modal deployment: single-container FastAPI + SQLite on a Volume.

Deploy: ``uv run modal deploy medha/deploy.py``

Single container (max_containers=1): SQLite cannot take concurrent
writers from multiple containers, and Modal Volumes need explicit commits
(see docs/DECISIONS.md D5 + Volume.md: last-writer-wins, no locking).
Mutations commit the volume at request boundaries (see medha/api/routes.py).
"""

import modal

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install(
        "fastapi>=0.115",
        "uvicorn>=0.30",
        "pydantic>=2.7",
        "pydantic-settings>=2.3",
        "openai>=1.40",
        "modal>=1.0",
        "rank-bm25>=0.2.2",
        "pandas>=2.2",
        "openpyxl>=3.1",
        "pdfplumber>=0.11",
        "pypdf>=4.0",
        "matplotlib>=3.9",
        "httpx>=0.27",
        "pyyaml>=6.0",
        "python-multipart>=0.0.9",
    )
    .add_local_python_source("medha")
)

app = modal.App("medha")
data_vol = modal.Volume.from_name("medha-data", create_if_missing=True)
secrets = [modal.Secret.from_name("medha-secrets")]


@app.function(
    image=image,
    volumes={"/data": data_vol},
    secrets=secrets,
    timeout=900,
    max_containers=1,
)
@modal.asgi_app()
def api():
    import os

    os.environ.setdefault("MEDHA_DB_PATH", "/data/medha.db")
    os.environ.setdefault("MEDHA_SKILLS_DIR", "/data/skills")
    os.environ.setdefault("MEDHA_VERIFY_DIR", "/data/verify_store")
    os.environ.setdefault("LLM_CACHE_DIR", "/data/llm_cache")
    os.environ.setdefault("COST_LOG_PATH", "/data/cost.jsonl")

    from medha.api.app import create_app

    return create_app(commit=data_vol.commit)
