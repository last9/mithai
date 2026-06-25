"""Tests for the bundled Kubernetes skill."""

import json
from pathlib import Path
from types import SimpleNamespace

from mithai.core.skill_loader import load_skills, validate_skill
from mithai.core.config import get_skill_paths
import skills.kubernetes.tools as k8s


def _result(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def test_kubernetes_skill_loads_and_validates():
    skill_dir = Path("skills/kubernetes")

    assert validate_skill(skill_dir) == []

    skills = load_skills([Path("skills")])
    skill = skills["kubernetes"]
    assert skill.verify is True
    assert {tool.name for tool in skill.tools} == {
        "get_pods",
        "get_deployments",
        "get_events",
        "get_logs",
        "describe_resource",
    }


def test_kubernetes_skill_is_discovered_from_default_skill_paths():
    skills = load_skills(get_skill_paths({}))

    assert "kubernetes" in skills


def test_get_pods_uses_configured_namespace_context_and_kubeconfig(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _result(stdout='{"items": [{"metadata": {"name": "api-1"}}]}')

    monkeypatch.setattr(k8s.shutil, "which", lambda _: "/usr/bin/kubectl")
    monkeypatch.setattr(k8s.subprocess, "run", fake_run)

    out = json.loads(k8s.handle(
        "get_pods",
        {},
        {"config": {
            "default_namespace": "prod",
            "context": "kind-kind",
            "kubeconfig": "/tmp/kubeconfig",
        }},
    ))

    assert out["ok"] is True
    assert out["items"][0]["metadata"]["name"] == "api-1"
    assert calls[0][0] == [
        "kubectl",
        "--kubeconfig",
        "/tmp/kubeconfig",
        "--context",
        "kind-kind",
        "get",
        "pods",
        "-o",
        "json",
        "-n",
        "prod",
    ]
    assert "shell" not in calls[0][1]


def test_get_events_all_namespaces(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _result(stdout='{"items": []}')

    monkeypatch.setattr(k8s.shutil, "which", lambda _: "/usr/bin/kubectl")
    monkeypatch.setattr(k8s.subprocess, "run", fake_run)

    out = json.loads(k8s.handle(
        "get_events",
        {"all_namespaces": True},
        {"config": {"default_namespace": "prod"}},
    ))

    assert out["ok"] is True
    assert "--all-namespaces" in calls[0]
    assert "-n" not in calls[0]


def test_get_logs_validates_names_and_clips_output(monkeypatch):
    long_logs = "x" * 21000

    monkeypatch.setattr(k8s.shutil, "which", lambda _: "/usr/bin/kubectl")
    monkeypatch.setattr(k8s.subprocess, "run", lambda *_, **__: _result(stdout=long_logs))

    out = json.loads(k8s.handle(
        "get_logs",
        {"pod": "api-1", "container": "web", "tail_lines": 5000},
        {"config": {"default_namespace": "prod"}},
    ))

    assert out["ok"] is True
    assert out["truncated"] is True
    assert out["logs"].endswith("...[truncated]")
    assert out["command"][-2:] == ["--tail", "1000"]

    bad = json.loads(k8s.handle("get_logs", {"pod": "../secret"}, {"config": {}}))
    assert bad["ok"] is False
    assert "invalid pod name" in bad["error"]


def test_describe_resource_rejects_unsupported_resource(monkeypatch):
    monkeypatch.setattr(k8s.shutil, "which", lambda _: "/usr/bin/kubectl")

    out = json.loads(k8s.handle(
        "describe_resource",
        {"resource_type": "secrets", "name": "prod-secret"},
        {"config": {}},
    ))

    assert out["ok"] is False
    assert out["error"] == "unsupported resource_type"


def test_missing_kubectl_returns_error(monkeypatch):
    monkeypatch.setattr(k8s.shutil, "which", lambda _: None)

    out = json.loads(k8s.handle(
        "get_deployments",
        {"namespace": "prod"},
        {"config": {}},
    ))

    assert out["ok"] is False
    assert out["error"] == "kubectl not found"
