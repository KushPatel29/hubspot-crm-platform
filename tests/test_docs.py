"""Every test the README cites as holding a guarantee exists, in Python or in the HubSpot apps' JavaScript suite."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_cited_test_exists():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## What makes it production work", 1)[1].split("\n## ", 1)[0]
    cited = [name for row in section.splitlines() if row.startswith("| ") and not row.startswith("| Concern")
             for name in re.findall(r"`([^`]+)`", row.rsplit("|", 2)[-2])]
    python = {name for path in (ROOT / "tests").glob("test_*.py")
              for name in re.findall(r"^def (test_\w+)", path.read_text(encoding="utf-8"), re.MULTILINE)}
    javascript = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "hubspot" / "tests").glob("*.test.*"))
    missing = [name for name in cited if not (
        name in python if name.startswith("test_") else name.startswith("npm ") or f"it('{name}'" in javascript)]
    assert cited and not missing, missing


def test_the_live_evidence_page_is_generated_from_the_evidence_files():
    from crm_platform.evidence import DOC, render

    assert DOC.read_text(encoding="utf-8") == render(), "run python -m crm_platform.evidence"
