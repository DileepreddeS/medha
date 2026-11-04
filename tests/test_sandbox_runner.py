"""ModalSandboxRunner talks to Modal correctly; FakeSandboxRunner never runs code."""

import base64
import json

from medha.sandbox.runner import FakeSandboxRunner, ModalSandboxRunner


class _Stream:
    def __init__(self, text):
        self._text = text

    def read(self):
        return self._text


class _Proc:
    def __init__(self, stdout="", stderr=""):
        self.stdout = _Stream(stdout)
        self.stderr = _Stream(stderr)

    def wait(self):
        return 0


class _FS:
    def __init__(self):
        self.files = {}

    def write_text(self, content, path):
        self.files[path] = content

    def read_text(self, path):
        return self.files[path]


class _Sandbox:
    def __init__(self, result_envelope):
        self.filesystem = _FS()
        self._envelope = result_envelope
        self.exec_calls = []
        self.terminated = False

    def exec(self, *args, timeout=None):
        self.exec_calls.append((args, timeout))
        self.filesystem.files["/work/result.json"] = json.dumps(self._envelope)
        return _Proc(stdout="out", stderr="")

    def terminate(self):
        self.terminated = True


class _Modal:
    def __init__(self, sandbox):
        self._sandbox = sandbox
        self.create_kwargs = None

        class App:
            @staticmethod
            def lookup(name, create_if_missing=False):
                assert name == "medha-sandboxes" and create_if_missing
                return "app"

        class Sandbox:
            @staticmethod
            def create(**kwargs):
                self.create_kwargs = kwargs
                return self._sandbox

        self.App = App
        self.Sandbox = Sandbox


def test_modal_runner_wires_sandbox_correctly(monkeypatch):
    sb = _Sandbox(
        {"ok": True, "result": {"n": 1}, "files": {"r.md": base64.b64encode(b"hi").decode()}}
    )
    fake_modal = _Modal(sb)
    monkeypatch.setitem(__import__("sys").modules, "modal", fake_modal)
    r = ModalSandboxRunner(image="img")
    out = r.run_skill("code", {"in.csv": b"a,b\n1,2\n"})
    assert out.ok and out.result == {"n": 1} and out.files == {"r.md": b"hi"}
    assert sb.terminated
    assert fake_modal.create_kwargs["block_network"] is True
    assert fake_modal.create_kwargs["cpu"] == 1
    assert fake_modal.create_kwargs["memory"] == 1024
    assert fake_modal.create_kwargs["workdir"] == "/work"
    manifest = json.loads(sb.filesystem.files["/work/manifest.json"])
    assert manifest["mode"] == "run"
    assert base64.b64decode(manifest["inputs_b64"]["in.csv"]) == b"a,b\n1,2\n"
    assert sb.filesystem.files["/work/skill.py"] == "code"
    assert sb.exec_calls[0][0][:2] == ("python", "/work/_medha_run.py")


def test_modal_runner_test_mode_and_missing_result(monkeypatch):
    sb = _Sandbox({"ok": True, "tests": {"passed": ["test_a"], "failed": []}, "files": {}})
    monkeypatch.setitem(__import__("sys").modules, "modal", _Modal(sb))
    r = ModalSandboxRunner(image="img")
    rep = r.run_tests("code", "testcode", {})
    assert rep.ok and rep.passed == ["test_a"] and rep.total == 1

    sb2 = _Sandbox({"ok": False})
    sb2.filesystem.files["/work/result.json"] = "not json{"
    real_exec = _Sandbox.exec

    def bad_exec(self, *a, **k):
        self.exec_calls.append((a, k.get("timeout")))
        return _Proc()

    monkeypatch.setattr(_Sandbox, "exec", bad_exec)
    monkeypatch.setitem(__import__("sys").modules, "modal", _Modal(sb2))
    out = ModalSandboxRunner(image="img").run_skill("code", {})
    assert out.ok is False and "no result.json" in out.error
    monkeypatch.setattr(_Sandbox, "exec", real_exec)


def test_fake_runner_is_scripted_and_never_executes():
    f = FakeSandboxRunner()
    assert f.run_skill("anything", {}).ok is True
    assert f.run_tests("x", "y", {}).passed == ["test_default"]
    assert len(f.calls) == 2
