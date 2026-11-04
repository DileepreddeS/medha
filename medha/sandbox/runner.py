"""Sandbox execution. Untrusted skill code runs ONLY in Modal Sandboxes.

Protocol per run (fresh sandbox, always terminated):
1. upload ``skill.py`` + ``_medha_run.py`` + ``manifest.json`` via
   ``sb.filesystem.write_text`` (binary inputs travel as base64 text).
2. ``sb.exec("python", "/work/_medha_run.py")`` with a 60s cap.
3. read back ``/work/result.json``.

The host NEVER execs skill code; ``FakeSandboxRunner`` returns scripted
outputs without running anything.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Protocol

RUNNER_SCRIPT = '''"""Medha sandbox harness. Runs INSIDE the sandbox only."""
import base64
import json
import sys
import traceback
from pathlib import Path

WORK = Path("/work")
MAX_FILE = 2_000_000


def main() -> None:
    manifest = json.loads((WORK / "manifest.json").read_text())
    in_dir = WORK / "inputs"
    in_dir.mkdir(exist_ok=True)
    for name, b64 in manifest.get("inputs_b64", {}).items():
        (in_dir / name).write_bytes(base64.b64decode(b64))
    out_dir = WORK / "out"
    out_dir.mkdir(exist_ok=True)
    sys.path.insert(0, "/work")
    inputs = {name: str(in_dir / name) for name in manifest.get("inputs_b64", {})}
    envelope: dict = {"ok": True}
    try:
        import skill as skill_mod

        if manifest["mode"] == "run":
            result = skill_mod.run(inputs, WORK)
            json.dumps(result)  # result must be JSON-serializable
            envelope["result"] = result
        elif manifest["mode"] == "test":
            (WORK / "test_check.py").write_text(manifest["test_code"])
            ns: dict = {"skill": skill_mod, "INPUTS": inputs, "WORKDIR": WORK}
            code = compile((WORK / "test_check.py").read_text(), "test_check.py", "exec")
            exec(code, ns)
            passed, failed = [], []
            names = sorted(n for n in ns if n.startswith("test_") and callable(ns[n]))
            for tname in names:
                try:
                    ns[tname]()
                    passed.append(tname)
                except Exception:
                    failed.append({"name": tname, "error": traceback.format_exc(limit=6)})
            envelope["tests"] = {"passed": passed, "failed": failed}
        else:
            raise ValueError(f"unknown mode {manifest['mode']!r}")
        files: dict = {}
        for p in sorted(out_dir.rglob("*")):
            if p.is_file() and p.stat().st_size <= MAX_FILE:
                files[str(p.relative_to(out_dir))] = base64.b64encode(p.read_bytes()).decode()
        envelope["files"] = files
    except Exception:
        envelope = {"ok": False, "error": traceback.format_exc(limit=10)}
    (WORK / "result.json").write_text(json.dumps(envelope))


main()
'''


@dataclass
class SkillRunOutput:
    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    files: dict[str, bytes] = field(default_factory=dict)
    error: str = ""
    stdout: str = ""
    stderr: str = ""


@dataclass
class TestRunOutput:
    __test__ = False  # not a pytest test class

    passed: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and not self.failed

    @property
    def total(self) -> int:
        return len(self.passed) + len(self.failed)


class SandboxRunner(Protocol):
    def run_skill(
        self,
        skill_code: str,
        inputs: dict[str, bytes],
        extra_modules: dict[str, str] | None = None,
    ) -> SkillRunOutput: ...
    def run_tests(
        self,
        skill_code: str,
        test_code: str,
        inputs: dict[str, bytes],
        extra_modules: dict[str, str] | None = None,
    ) -> TestRunOutput: ...


class FakeSandboxRunner:
    """Scripted fake for unit tests. Never executes code."""

    def __init__(self) -> None:
        self.skill_outputs: list[SkillRunOutput] = []
        self.test_outputs: list[TestRunOutput] = []
        self.calls: list[tuple[str, str, dict[str, bytes]]] = []

    def run_skill(
        self,
        skill_code: str,
        inputs: dict[str, bytes],
        extra_modules: dict[str, str] | None = None,
    ) -> SkillRunOutput:
        self.calls.append(("run", skill_code, dict(inputs)))
        if self.skill_outputs:
            return self.skill_outputs.pop(0)
        return SkillRunOutput(ok=True, result={})

    def run_tests(
        self,
        skill_code: str,
        test_code: str,
        inputs: dict[str, bytes],
        extra_modules: dict[str, str] | None = None,
    ) -> TestRunOutput:
        self.calls.append(("test", test_code, dict(inputs)))
        if self.test_outputs:
            return self.test_outputs.pop(0)
        return TestRunOutput(passed=["test_default"])


class ModalSandboxRunner:
    """Real runner: one fresh Modal Sandbox per call, network blocked."""

    def __init__(
        self,
        app_name: str = "medha-sandboxes",
        image: Any = None,
        cpu: float = 1,
        memory: int = 1024,
        exec_timeout: int = 60,
    ) -> None:
        self.app_name = app_name
        self.image = image
        self.cpu = cpu
        self.memory = memory
        self.exec_timeout = exec_timeout

    def _create(self) -> Any:
        try:
            import modal
        except ImportError as e:
            raise RuntimeError("modal package is not installed") from e
        image = self.image
        if image is None:
            from medha.sandbox.image import SKILL_IMAGE

            image = SKILL_IMAGE
        app = modal.App.lookup(self.app_name, create_if_missing=True)
        return modal.Sandbox.create(
            app=app,
            image=image,
            block_network=True,
            cpu=self.cpu,
            memory=self.memory,
            timeout=max(120, self.exec_timeout + 60),
            workdir="/work",
        )

    def _exec(
        self,
        mode: str,
        skill_code: str,
        inputs: dict[str, bytes],
        test_code: str = "",
        extra_modules: dict[str, str] | None = None,
    ) -> tuple[dict[str, Any], str, str]:
        sb = self._create()
        try:
            sb.filesystem.write_text(skill_code, "/work/skill.py")
            for mod_name, mod_code in (extra_modules or {}).items():
                if not mod_name.replace("_", "").isalnum() or not mod_name[0].isalpha():
                    raise ValueError(f"bad module name {mod_name!r}")
                sb.filesystem.write_text(mod_code, f"/work/{mod_name}.py")
            sb.filesystem.write_text(RUNNER_SCRIPT, "/work/_medha_run.py")
            manifest = {
                "mode": mode,
                "inputs_b64": {k: base64.b64encode(v).decode() for k, v in inputs.items()},
                "test_code": test_code,
            }
            sb.filesystem.write_text(json.dumps(manifest), "/work/manifest.json")
            proc = sb.exec("python", "/work/_medha_run.py", timeout=self.exec_timeout)
            proc.wait()
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
            try:
                envelope = json.loads(sb.filesystem.read_text("/work/result.json"))
            except Exception as e:
                envelope = {
                    "ok": False,
                    "error": f"no result.json: {e}\nSTDOUT:\n{stdout}\nSTDERR:\n{stderr}",
                }
            return envelope, stdout, stderr
        finally:
            sb.terminate()

    def run_skill(
        self,
        skill_code: str,
        inputs: dict[str, bytes],
        extra_modules: dict[str, str] | None = None,
    ) -> SkillRunOutput:
        envelope, stdout, stderr = self._exec("run", skill_code, inputs, "", extra_modules)
        files = {k: base64.b64decode(v) for k, v in (envelope.get("files") or {}).items()}
        result = envelope.get("result")
        return SkillRunOutput(
            ok=bool(envelope.get("ok")),
            result=result if isinstance(result, dict) else {},
            files=files,
            error=envelope.get("error", ""),
            stdout=stdout,
            stderr=stderr,
        )

    def run_tests(
        self,
        skill_code: str,
        test_code: str,
        inputs: dict[str, bytes],
        extra_modules: dict[str, str] | None = None,
    ) -> TestRunOutput:
        envelope, stdout, stderr = self._exec("test", skill_code, inputs, test_code, extra_modules)
        tests = envelope.get("tests") or {}
        return TestRunOutput(
            passed=list(tests.get("passed", [])),
            failed=list(tests.get("failed", [])),
            error=envelope.get("error", ""),
            stdout=stdout,
            stderr=stderr,
        )
