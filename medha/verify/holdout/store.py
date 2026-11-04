"""Hidden-test storage under the verify dir (outside the author's reach).

FIREWALLED: only medha.verify.gate may use this module.
"""

from __future__ import annotations

from pathlib import Path


def holdout_dir(verify_dir: str | Path, skill_id: str, version: int) -> Path:
    return Path(verify_dir) / skill_id / f"v{version}" / "holdout"


def write_holdout(
    verify_dir: str | Path,
    skill_id: str,
    version: int,
    tests_py: str,
    inputs: dict[str, bytes],
    overwrite: bool = False,
) -> Path:
    """Persist hidden tests. Overwrite is only ever used by the gate for
    versions that have not been activated yet (author retries)."""
    dest = holdout_dir(verify_dir, skill_id, version)
    if dest.exists() and not overwrite:
        raise FileExistsError(f"holdout already stored for {skill_id} v{version}")
    (dest / "inputs").mkdir(parents=True, exist_ok=True)
    (dest / "tests.py").write_text(tests_py)
    for name, blob in inputs.items():
        (dest / "inputs" / name).write_bytes(blob)
    return dest


def read_holdout(
    verify_dir: str | Path, skill_id: str, version: int
) -> tuple[str, dict[str, bytes]]:
    dest = holdout_dir(verify_dir, skill_id, version)
    tests_py = (dest / "tests.py").read_text()
    inputs = {p.name: p.read_bytes() for p in sorted((dest / "inputs").iterdir())}
    return tests_py, inputs
