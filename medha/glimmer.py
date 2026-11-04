"""Self-hosted Muse Glimmer on Modal: vLLM, OpenAI-compatible.

Deploy: ``uv run modal deploy medha/glimmer.py``
Warm up + verify the DEPLOYED app: ``uv run modal run medha/glimmer.py``
Then set GLIMMER_BASE_URL to the printed URL (+ ``/v1``).

Config (see docs/DECISIONS.md D2): single A100-80GB (H100 preferred when
available), BF16, weights in a Modal Volume, scale-to-zero with a
configurable idle window (``GLIMMER_SCALEDOWN_WINDOW_S``, default 900s
for bench/demo; read at deploy time). Auth is a vLLM API key:
``GLIMMER_API_KEY`` from medha-secrets is exported as ``VLLM_API_KEY``
(never passed via argv — vLLM logs its args) and sent by clients as
``Authorization: Bearer``.
"""

from __future__ import annotations

import os

import modal

MODEL_ID = "meta-models/Muse-Glimmer-30B"
SERVED_NAME = "glimmer"
VLLM_PORT = 8000

# vLLM pins (see docs/DECISIONS.md D2): v0.28.0 was the first release with
# native `muse_glimmer` support; 0.33.0 is the latest verified on PyPI and
# pairs with transformers >=5.16.1,<5.21.0, so 5.20.2 is the newest allowed.
vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install("vllm==0.33.0", "transformers==5.20.2")
    # hf-xet only: HF_HUB_ENABLE_HF_TRANSFER is NOT set because hf_transfer
    # isn't installed and the flag crashes the config download at boot.
    #
    # Every cache dir below lives on a Modal Volume (see `volumes=`) so cold
    # starts reuse weights + JIT artifacts instead of re-downloading and
    # recompiling. TRITON/FLASHINFER vars must be explicit: vLLM only
    # redirects them under its cache root on the torch.compile path, which
    # --enforce-eager disables.
    .env(
        {
            "HF_XET_HIGH_PERFORMANCE": "1",
            "HF_HOME": "/root/.cache/huggingface",
            "VLLM_CACHE_DIR": "/root/.cache/vllm",
            "VLLM_CACHE_ROOT": "/root/.cache/vllm",
            "TRITON_CACHE_DIR": "/root/.cache/vllm/triton",
            "FLASHINFER_WORKSPACE_BASE": "/root/.cache/vllm/flashinfer",
            "VLLM_FLASHINFER_AUTOTUNE_CACHE_DIR": "/root/.cache/vllm/flashinfer_autotune",
        }
    )
)

app = modal.App("medha-glimmer")
hf_cache_vol = modal.Volume.from_name("medha-hf-cache", create_if_missing=True)
vllm_cache_vol = modal.Volume.from_name("medha-vllm-cache", create_if_missing=True)
secrets = [modal.Secret.from_name("medha-secrets")]


@app.server(
    image=vllm_image,
    gpu=["H100", "A100-80GB"],  # single GPU: H100 first, else A100-80GB
    # Deploy-time env so bench/demo can hold the GPU without a code edit.
    scaledown_window=int(os.environ.get("GLIMMER_SCALEDOWN_WINDOW_S", "900")),
    startup_timeout=600,  # cold start incl. weight download can take minutes
    volumes={
        "/root/.cache/huggingface": hf_cache_vol,
        "/root/.cache/vllm": vllm_cache_vol,
    },
    secrets=secrets,  # HF_TOKEN (weights) + GLIMMER_API_KEY (VLLM_API_KEY)
    port=VLLM_PORT,
    target_concurrency=4,
    min_containers=0,
    unauthenticated=True,  # Modal proxy open; VLLM_API_KEY guards /v1/*
)
class GlimmerServer:
    @modal.enter()
    def start(self) -> None:
        import os
        import subprocess
        import threading

        api_key = os.environ.get("GLIMMER_API_KEY", "")
        if not api_key:
            raise RuntimeError("GLIMMER_API_KEY is missing from medha-secrets")
        # Never via argv: vLLM logs non-default args (incl. --api-key) in plain
        # text. VLLM_API_KEY env is the documented equivalent, never logged.
        os.environ["VLLM_API_KEY"] = api_key
        cmd = [
            "vllm",
            "serve",
            MODEL_ID,
            "--served-model-name",
            SERVED_NAME,
            "--host",
            "0.0.0.0",
            "--port",
            str(VLLM_PORT),
            "--dtype",
            "bfloat16",
            "--tensor-parallel-size",
            "1",
            "--enforce-eager",  # fast cold boots for scale-to-zero
            # Channel separation (vLLM Glimmer recipe): without BOTH parsers,
            # to=self reasoning leaks into message.content. The reasoning
            # parser routes it to message.reasoning_content instead.
            "--reasoning-parser",
            "muse_glimmer",
            "--tool-call-parser",
            "muse_glimmer",
            "--uvicorn-log-level=info",
            # NOTE: no --async-scheduling. It was a V0-engine opt-in flag;
            # the V1 engine (default since vLLM 0.8) schedules asynchronously
            # already, and the explicit flag is rejected by newer servers.
            # NOTE: no --generation-config auto (recipe default). It would
            # apply the model's temperature 1.0 server-side; our client
            # always sends an explicit per-request temperature instead.
            # NOTE: no --enable-auto-tool-choice. We make no tool calls; the
            # tool-call parser is present only so channels don't collapse.
        ]
        # Defensive: argv holds no secrets, but blank the key if it ever does.
        shown = ["[REDACTED]" if a == api_key else a for a in cmd]
        print("launch:", " ".join(shown), flush=True)
        self.process = subprocess.Popen(cmd)  # noqa: S603 - fixed argv, no shell
        # Persist caches once the server is healthy (weights fully
        # downloaded). Without an explicit commit, the next cold boot
        # re-downloads everything. Daemon thread: never blocks shutdown.
        watcher = threading.Thread(target=_commit_when_healthy, args=(api_key,), daemon=True)
        watcher.start()

    @modal.exit()
    def stop(self) -> None:
        # Capture anything written while serving (e.g. autotune results).
        _commit_caches("exit")
        self.process.terminate()


def _commit_caches(phase: str) -> None:
    for vol, label in ((hf_cache_vol, "hf"), (vllm_cache_vol, "vllm")):
        try:
            vol.commit()
        except Exception as e:
            print(f"cache commit ({phase}/{label}): FAILED ({e})", flush=True)
        else:
            print(f"cache commit ({phase}/{label}): ok", flush=True)


def _commit_when_healthy(api_key: str) -> None:
    import time
    import urllib.request

    deadline = time.time() + 570
    health = urllib.request.Request(
        f"http://127.0.0.1:{VLLM_PORT}/health",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=10) as r:
                if r.status == 200:
                    break
        except Exception as e:
            print(f"cache commit (boot): waiting for health ({e})", flush=True)
        time.sleep(10)
    else:
        print("cache commit (boot): server never became healthy; skipping", flush=True)
        return
    _commit_caches("boot")


@app.local_entrypoint()
async def main() -> None:
    """Warm up + smoke-test the DEPLOYED endpoint: /health then one chat.

    Resolves the deployed server's URL via the Modal API, so this never
    starts an ephemeral -dev server. Prints the URL to use for
    GLIMMER_BASE_URL.
    """
    import json
    import os
    import time
    import urllib.request

    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    api_key = os.environ.get("GLIMMER_API_KEY", "")
    if not api_key:
        raise SystemExit("set GLIMMER_API_KEY in your shell or .env first")
    auth = {"Authorization": f"Bearer {api_key}"}

    server = modal.Server.from_name("medha-glimmer", "GlimmerServer")
    url = await server.get_url.aio()
    if not url:
        raise SystemExit(
            "deployed server has no URL yet — run `uv run modal deploy medha/glimmer.py` first"
        )
    print(f"server: {url}")
    deadline = time.time() + 570
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url + "/health", headers=auth)
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status == 200:
                    break
        except Exception as e:
            print(f"waiting for health... ({e})")
            time.sleep(10)
    else:
        raise SystemExit("health check timed out")
    print("health: ok")

    payload = json.dumps(
        {"model": SERVED_NAME, "messages": [{"role": "user", "content": "Reply: pong"}]}
    ).encode()
    req = urllib.request.Request(
        url + "/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", **auth},
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        body = json.loads(r.read())
    text = body["choices"][0]["message"]["content"]
    assert "pong" in text.lower(), text
    print(f"chat: ok ({text.strip()!r}); set GLIMMER_BASE_URL={url}/v1")
