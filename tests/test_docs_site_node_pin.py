"""The docs site's Node version is pinned once and every other surface agrees with it.

Node 22.23.4 is the first 22.x release that rejects TLS chains through the compromised
eMudhra intermediates (nodejs/node#66871), so nothing may fall back to an older 22.x.
"""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DOCS_SITE = ROOT / "docs-site"
FIXED_NODE = "22.23.4"


def test_docs_workflow_reads_node_from_nvmrc():
    workflow = yaml.safe_load((ROOT / ".github/workflows/docs.yml").read_text())
    steps = workflow["jobs"]["build-and-deploy"]["steps"]
    setup_node = [s for s in steps if s.get("uses", "").startswith("actions/setup-node@")]
    assert len(setup_node) == 1
    assert setup_node[0]["with"]["node-version-file"] == "docs-site/.nvmrc"
    assert "node-version" not in setup_node[0]["with"]


def test_nvmrc_pins_first_fixed_node_release():
    assert (DOCS_SITE / ".nvmrc").read_text().strip() == FIXED_NODE


def test_engines_floor_is_pinned_release():
    package = json.loads((DOCS_SITE / "package.json").read_text())
    assert package["engines"] == {"node": f"^{FIXED_NODE}"}


def test_lockfile_root_engines_match_package_json():
    package = json.loads((DOCS_SITE / "package.json").read_text())
    lock = json.loads((DOCS_SITE / "package-lock.json").read_text())
    assert lock["packages"][""]["engines"] == package["engines"]
