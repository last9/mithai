"""The docs CI TLS probe passes only after observing the revoked chain rejected.

The real script runs under Node with `fetch` replaced by synthetic outcomes, so no
network request is made: a number is an HTTP status, a string is a thrown error code.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
PROBE = ROOT / "docs-site/scripts/check-node-tls.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def run_probe(tmp_path, revoked, trusted):
    preload = tmp_path / "synthetic-fetch.mjs"
    preload.write_text(
        f"const results = {json.dumps([revoked, trusted])};\n"
        "globalThis.fetch = async () => {\n"
        "  const value = results.shift();\n"
        '  if (typeof value === "number") return { status: value };\n'
        '  throw new TypeError("fetch failed", {\n'
        '    cause: Object.assign(new Error("synthetic failure"), { code: value }),\n'
        "  });\n"
        "};\n"
    )
    return subprocess.run(
        [NODE, "--import", str(preload), str(PROBE)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize(
    ("revoked", "trusted", "expected"),
    [
        ("CERT_REVOKED", 200, 0),
        ("ENOTFOUND", 200, 1),
        ("ECONNREFUSED", 200, 1),
        ("UNABLE_TO_GET_ISSUER_CERT_LOCALLY", 200, 1),
        (200, 200, 1),
        ("CERT_REVOKED", "ENOTFOUND", 1),
    ],
)
def test_probe_requires_cert_revoked_and_trusted_host(tmp_path, revoked, trusted, expected):
    result = run_probe(tmp_path, revoked, trusted)
    assert result.returncode == expected, result.stdout + result.stderr


def test_probe_reports_unexpected_revoked_host_error(tmp_path):
    result = run_probe(tmp_path, "ENOTFOUND", 200)
    assert "::error::expected CERT_REVOKED, got ENOTFOUND" in result.stdout
