#!/usr/bin/env python3
"""Audit source snapshots, generated coverage, and sibling checkout cleanliness."""
from __future__ import annotations
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SOURCES = ROOT / "sources"
CONTENT = ROOT / "content" / "docs"


def git_status(repo: Path) -> list[str]:
    return subprocess.check_output(["git", "-C", str(repo), "status", "--short"], text=True).splitlines()


def main() -> None:
    inventory = json.loads((SOURCES / "inventory.json").read_text())
    checksums = json.loads((SOURCES / "checksums.json").read_text())
    errors: list[str] = []
    for rel, expected in checksums.items():
        path = SOURCES / rel
        if not path.is_file():
            errors.append(f"missing snapshot: {rel}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"snapshot changed: {rel}")

    for section in ("sub", "dev", "mavproxy"):
        expected = sum(1 for p in inventory[f"wiki/{section}"] if p.endswith(".rst"))
        actual = len(list((CONTENT / section).rglob("*.mdx")))
        if expected != actual:
            errors.append(f"{section}: expected {expected} RST pages, found {actual} MDX pages")
    expected_ardupilot = sum(1 for p in inventory["ardupilot"] if p.lower().endswith((".md", ".markdown", ".txt")) or p.startswith("docs/"))
    actual_ardupilot = len(list((CONTENT / "ardupilot").rglob("*.mdx")))
    if expected_ardupilot + 1 != actual_ardupilot:  # generated section landing page
        errors.append(f"ardupilot: expected {expected_ardupilot + 1} pages, found {actual_ardupilot}")
    expected_mavlink = sum(1 for p in inventory["mavlink"] if p.lower().endswith(".md") or p.startswith("message_definitions/v1.0/"))
    actual_mavlink = len(list((CONTENT / "mavlink").rglob("*.mdx")))
    if expected_mavlink + 1 != actual_mavlink:  # generated section landing page
        errors.append(f"mavlink: expected {expected_mavlink + 1} pages, found {actual_mavlink}")
    if len(list((CONTENT / "mavlink" / "dialects").glob("*.mdx"))) != 19:
        errors.append("MAVLink dialect coverage is not 19 XML inputs")
    if not list((CONTENT / "api").rglob("*.mdx")):
        errors.append("API output is empty")

    # Only the user's pre-existing common/ deletions are allowed in the wiki checkout.
    wiki_changes = git_status(WORKSPACE / "ardupilot_wiki")
    for repo_name in ("ardupilot", "mavlink"):
        if git_status(WORKSPACE / repo_name):
            errors.append(f"{repo_name} checkout changed")
    if any("common/source/docs/" not in line for line in wiki_changes):
        errors.append("ardupilot_wiki has changes outside pre-existing common/ deletions")

    if errors:
        raise SystemExit("\n".join(errors))
    print("Migration audit passed: snapshots, page coverage, 19 MAVLink dialects, API output, and source checkouts verified")


if __name__ == "__main__":
    main()
