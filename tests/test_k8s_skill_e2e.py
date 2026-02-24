"""
End-to-end test: k8s self-healing skill with real LLM calls.

Prerequisites:
  - A running Kubernetes cluster (e.g. minikube)
  - A 'mithai-test' namespace with broken pods for healing scenarios
  - ANTHROPIC_API_KEY set in the environment

Run with:
  ANTHROPIC_API_KEY=sk-... uv run python tests/test_k8s_skill_e2e.py
"""

import sys
from pathlib import Path

from mithai.core.config import load_config
from mithai.cli.run_cmd import _create_llm, _create_state
from mithai.core.engine import Engine
from mithai.adapters.cli import CLIAdapter
from mithai.adapters.base import IncomingMessage


def make_engine(config: dict) -> Engine:
    config.setdefault("skills", {})["paths"] = ["./skills"]
    return Engine(config, _create_llm(config), _create_state(config))


def chat(engine: Engine, adapter: CLIAdapter, text: str) -> str:
    return engine.handle(
        IncomingMessage(text=text, channel_id="e2e-test", user_id="tester", platform="cli"),
        adapter,
    )


DIVIDER = "─" * 70

TURNS = [
    # --- Self-healing scenarios ---
    (
        "Turn 1: Cluster scan",
        "Scan the mithai-test namespace for any issues and give me a full summary.",
    ),
    (
        "Turn 2: CrashLoopBackOff — previous logs",
        "Check crash-pod in mithai-test. Get the previous container logs to see why it crashed.",
    ),
    (
        "Turn 3: Pending diagnosis",
        "Why is pending-pod stuck in Pending in mithai-test? Check node capacity.",
    ),
    # --- Extended tools ---
    (
        "Turn 4: Cluster health score",
        "Give me the overall health score of the cluster and explain each issue found.",
    ),
    (
        "Turn 5: Security audit",
        "Run a security audit on the kube-system namespace and tell me what the critical findings are.",
    ),
    (
        "Turn 6: Manifest generation",
        "Generate a production-ready deployment manifest for an app called 'api-server' in the 'production' namespace.",
    ),
    (
        "Turn 7: Resource usage",
        "What is the current CPU and memory usage across all nodes?",
    ),
]


def run():
    print(f"\n{DIVIDER}")
    print("  mithai k8s — end-to-end test")
    print(f"{DIVIDER}\n")

    config = load_config(Path("config.yaml"))
    engine = make_engine(config)
    adapter = CLIAdapter()

    for label, prompt in TURNS:
        print(f"\n{'=' * 70}")
        print(f"  {label}")
        print(f"{'=' * 70}")
        print(f"\nyou> {prompt}\n")
        response = chat(engine, adapter, prompt)
        print(f"mithai> {response}\n")

    print(f"\n{DIVIDER}")
    print("  End-to-end test complete")
    print(f"{DIVIDER}\n")


if __name__ == "__main__":
    run()
