#!/usr/bin/env python3
"""Rebuild the unified ArduPilot documentation site from immutable snapshots.

The importer is deliberately deterministic: sources are copied once into ``sources``
and every generated file is derived from those copies.  Run it from docs-site with
``python scripts/import_docs.py``.  ``--refresh-sources`` is an explicit opt-in for
creating a new snapshot; normal runs refuse to modify an existing snapshot.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
SOURCES = ROOT / "sources"
CONTENT = ROOT / "content" / "docs"
PUBLIC = ROOT / "public" / "docs"

WIKI_SECTIONS = ("sub", "dev", "mavproxy")
SECTION_DESCRIPTIONS = {
    "sub": "ArduSub vehicle documentation",
    "dev": "ArduPilot developer documentation",
    "mavproxy": "MAVProxy ground-control documentation",
    "mavlink": "MAVLink protocol and dialect reference",
    "ardupilot": "ArduPilot project documentation and release notes",
    "api": "Generated ArduPilot API reference",
}
SECTION_TITLES = {
    "sub": "ArduSub",
    "dev": "Developer documentation",
    "mavproxy": "MAVProxy",
    "mavlink": "MAVLink",
    "ardupilot": "ArduPilot project",
    "api": "API reference",
}
AGENT_FILENAMES = {"agent.md", "agents.md", "claude.md"}


def is_agent_file(path: Path) -> bool:
    return path.name.casefold() in AGENT_FILENAMES


def remove_agent_files() -> None:
    """Keep upstream LLM-agent instruction files out of source snapshots."""
    if not SOURCES.exists():
        return
    for path in SOURCES.rglob("*"):
        if path.is_file() and is_agent_file(path):
            path.unlink()


def copy_tree_once(src: Path, dst: Path, refresh: bool = False) -> None:
    if dst.exists() and not refresh:
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False)


def copy_selected_files(repo: Path, dst: Path, paths: Iterable[str], refresh: bool = False) -> int:
    count = 0
    for rel in paths:
        source = repo / rel
        if not source.is_file():
            continue
        target = dst / rel
        if target.exists() and not refresh:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        count += 1
    return count


def snapshot_sources(refresh: bool = False) -> dict:
    """Create source snapshots and return an inventory of copied inputs."""
    SOURCES.mkdir(parents=True, exist_ok=True)
    remove_agent_files()
    inventory: dict[str, list[str]] = {}
    wiki = WORKSPACE / "ardupilot_wiki"
    for section in WIKI_SECTIONS:
        source_dir = wiki / section / "source"
        destination = SOURCES / "wiki" / section / "source"
        copy_tree_once(source_dir, destination, refresh)
        inventory[f"wiki/{section}"] = sorted(
            str(p.relative_to(destination)) for p in destination.rglob("*") if p.is_file() and not is_agent_file(p)
        )

    ardupilot = WORKSPACE / "ardupilot"
    tracked = subprocess.check_output(["git", "-C", str(ardupilot), "ls-files"], text=True).splitlines()
    # Authored Markdown, docs, READMEs, and release notes are the portable project corpus.
    selected = [
        p
        for p in tracked
        if not is_agent_file(Path(p))
        and (p.lower().endswith((".md", ".markdown")) or p.startswith("docs/") or p.endswith("ReleaseNotes.txt"))
    ]
    inventory["ardupilot"] = sorted(selected)
    copy_selected_files(ardupilot, SOURCES / "ardupilot", selected, refresh)

    mavlink = WORKSPACE / "mavlink"
    mtracked = subprocess.check_output(["git", "-C", str(mavlink), "ls-files"], text=True).splitlines()
    mselected = [
        p
        for p in mtracked
        if not is_agent_file(Path(p))
        and (p.lower().endswith(".md") or p.startswith("message_definitions/v1.0/") or p == "doc/mavlink_xml_to_markdown.py")
    ]
    inventory["mavlink"] = sorted(mselected)
    copy_selected_files(mavlink, SOURCES / "mavlink", mselected, refresh)

    # Snapshot API source inputs separately so API output is reproducible without a checkout.
    api_inputs: list[str] = []
    for base in (ardupilot / "ArduSub", ardupilot / "libraries"):
        for p in base.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".h", ".hpp", ".cpp", ".c"}:
                rel = p.relative_to(ardupilot)
                api_inputs.append(str(rel))
                target = SOURCES / "api-inputs" / rel
                if not target.exists() or refresh:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(p, target)
    inventory["api-inputs"] = sorted(api_inputs)

    # Shared wiki images are referenced by many sections.
    shared_images = wiki / "images"
    if shared_images.exists():
        copy_tree_once(shared_images, SOURCES / "wiki-images", refresh)
        inventory["wiki-images"] = sorted(
            str(p.relative_to(SOURCES / "wiki-images"))
            for p in (SOURCES / "wiki-images").rglob("*")
            if p.is_file() and not is_agent_file(p)
        )

    remove_agent_files()
    (SOURCES / "inventory.json").write_text(json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return inventory


def checksum_manifest() -> dict[str, str]:
    result = {}
    for p in sorted(SOURCES.rglob("*")):
        if p.is_file() and p.name not in {"checksums.json", "inventory.json"}:
            result[str(p.relative_to(SOURCES))] = hashlib.sha256(p.read_bytes()).hexdigest()
    (SOURCES / "checksums.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result


def clean_generated() -> None:
    for p in (CONTENT, PUBLIC):
        if p.exists():
            for child in p.iterdir():
                if child.name == ".source":
                    continue
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        p.mkdir(parents=True, exist_ok=True)


def title_from_name(name: str) -> str:
    name = re.sub(r"[_-]+", " ", name).strip()
    return re.sub(r"\b\w", lambda m: m.group(0).upper(), name) or "ArduPilot Documentation"


def yaml_frontmatter(title: str, description: str) -> str:
    title = title.replace("\\", "\\\\").replace('"', '\\"')
    description = description.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'---\ntitle: "{title}"\ndescription: "{description}"\n---\n\n'


def page_name(rel: Path) -> str:
    if rel.stem.lower() in {"index", "home"}:
        return "index.mdx"
    return rel.with_suffix(".mdx").name


def route_for(section: str, rel: Path) -> str:
    noext = rel.with_suffix("")
    parts = [p for p in noext.parts if p not in {"index", "home"}]
    return "/docs/" + "/".join([section, *parts])


def collect_labels(section: str, source_root: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    for rst in source_root.rglob("*.rst"):
        rel = rst.relative_to(source_root)
        route = route_for(section, rel)
        for line in rst.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\.\.\s+_([\w.-]+):\s*$", line.strip())
            if m:
                labels[m.group(1)] = route
    return labels


def asset_url(section: str, source_root: Path, current: Path, raw: str) -> str:
    raw = raw.strip().strip("<>")
    raw = raw.replace("\\", "/")
    if raw.startswith("/images/"):
        raw = raw.removeprefix("/")
    if raw.startswith(("http://", "https://", "/")):
        return raw
    # Most wiki paths are relative to source/; resolve against the current document too.
    candidates = [source_root / raw, source_root / current.parent / raw, SOURCES / "wiki-images" / raw]
    for c in candidates:
        if c.is_file():
            c = c.resolve()
            if c.is_relative_to(source_root):
                rel = c.relative_to(source_root)
                destination = PUBLIC / section / rel
            else:
                rel = c.relative_to(SOURCES / "wiki-images")
                destination = PUBLIC / "images" / rel
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(c, destination)
            if c.is_relative_to(source_root):
                return "/docs/" + section + "/" + rel.as_posix()
            return "/docs/images/" + rel.as_posix()
    # Preserve an unresolved asset as a canonical wiki URL.
    return f"https://ardupilot.org/{section}/docs/{raw.lstrip('./')}"


def inline_rst(text: str, section: str, labels: dict[str, str]) -> str:
    # Explicit links and common Sphinx roles.
    def ref(m: re.Match) -> str:
        label, target = m.group(1), m.group(2)
        target = target.strip()
        if ":" in target:
            target_section, target_label = target.split(":", 1)
        else:
            target_section, target_label = section, target
        url = labels.get(target) or labels.get(target_label)
        if not url:
            if target_section == "common":
                url = f"https://ardupilot.org/copter/docs/{target_label}.html"
            elif target_section in WIKI_SECTIONS:
                url = f"/docs/{target_section}/{target_label}"
            else:
                url = f"https://ardupilot.org/{target_section}/docs/{target_label}.html"
        return f"[{label}]({url})"

    text = re.sub(r":ref:`([^`<>]+?)\s*<([^`]+)>`", ref, text)
    text = re.sub(r":ref:`([^`]+)`", lambda m: ref(type("M", (), {"group": lambda self, n: m.group(1) if n == 1 else m.group(1)})()), text)
    text = re.sub(r":(?:doc|any|download):`([^`<>]+?)\s*<([^`]+)>`", r"[\1](\2)", text)
    text = re.sub(r":(?:kbd|command|term|class|func|attr|const|option|envvar):`([^`]+)`", r"`\1`", text)
    # Anonymous and named RST links.
    text = re.sub(r"`([^`<>]+?)\s*<([^>]+)>`__?", r"[\1](\2)", text)
    text = re.sub(r"`([^`]+)`_", r"[\1]", text)
    text = text.replace("|br|", "<br />")
    text = re.sub(r"\|([^|]+)\|", lambda m: m.group(1), text)
    text = text.replace("\\*", "*").replace("\\_", "_")
    return text


def directive_body(lines: list[str], start: int) -> tuple[list[str], int]:
    body: list[str] = []
    previous = lines[start - 1] if start else ""
    base_indent = len(previous) - len(previous.lstrip(" \t"))
    content_indent: int | None = None
    i = start
    while i < len(lines):
        line = lines[i]
        indent = len(line) - len(line.lstrip(" \t"))
        if line.strip() and indent <= base_indent:
            break
        if line.strip() and content_indent is None:
            content_indent = indent
        body.append(line[(content_indent or (base_indent + 1)): ] if line.strip() else "")
        i += 1
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()
    return body, i


def rst_to_mdx(text: str, section: str, source_root: Path, current: Path, labels: dict[str, str], include_stack: tuple[Path, ...] = ()) -> str:
    # Sphinx roles are occasionally wrapped across source lines.
    text = re.sub(r":ref:`([^`\n]*)\n\s*([^`]+)`", lambda m: ":ref:`" + " ".join(" ".join(m.groups()).split()) + "`", text)
    text = re.sub(r":doc:`([^`\n]*)\n\s*([^`]+)`", lambda m: ":doc:`" + " ".join(" ".join(m.groups()).split()) + "`", text)
    lines = text.replace("\r\n", "\n").split("\n")
    # Substitution image definitions are handled before line conversion.
    substitutions: dict[str, str] = {}
    for i, line in enumerate(lines):
        m = re.match(r"^\.\.\s+\|([^|]+)\|\s+image::\s*(\S+)", line)
        if m:
            substitutions[m.group(1)] = asset_url(section, source_root, current, m.group(2))
    out: list[str] = []
    i = 0
    in_literal = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        # Explicit labels and substitution definitions do not render.
        if re.match(r"^\.\.\s+_[\w.-]+:\s*$", stripped) or re.match(r"^\.\.\s+\|[^|]+\|\s+image::", stripped):
            i += 1
            continue
        # Sphinx toctrees become a readable link list; navigation is generated separately.
        if re.match(r"^\.\.\s+toctree::", stripped):
            body, i = directive_body(lines, i + 1)
            entries = [x.strip().split()[0] for x in body if x.strip() and not x.strip().startswith(":")]
            if entries:
                out.append("\n".join(f"- [{title_from_name(Path(x).name)}]({route_for(section, Path(x + ('' if x.endswith('.rst') else '.rst')))})" for x in entries))
            continue
        # Include files are expanded from the immutable snapshot when available.
        inc = re.match(r"^\.\.\s+include::\s*(\S+)", stripped)
        if inc:
            include = (source_root / current.parent / inc.group(1)).resolve()
            if include.exists() and include not in include_stack:
                included = include.read_text(encoding="utf-8", errors="replace")
                out.append(rst_to_mdx(included, section, source_root, include.relative_to(source_root), labels, include_stack + (include,)))
            else:
                out.append(f"`include: {inc.group(1)}`")
            i += 1
            continue
        # Code blocks and literal blocks.
        code = re.match(r"^\.\.\s+(?:code-block|code)::\s*([^ ]*)", stripped)
        if code:
            body, i = directive_body(lines, i + 1)
            lang = code.group(1).strip() or "text"
            out.extend([f"```{lang}", *body, "```", ""])
            continue
        if stripped == "::":
            body, i = directive_body(lines, i + 1)
            out.extend(["```text", *body, "```", ""])
            continue
        next_nonblank = i + 1
        while next_nonblank < len(lines) and not lines[next_nonblank].strip():
            next_nonblank += 1
        if not stripped.startswith(".. ") and line.rstrip().endswith("::") and next_nonblank < len(lines) and lines[next_nonblank].startswith((" ", "\t")):
            body, i = directive_body(lines, i + 1)
            out.extend([inline_rst(line[:-2].rstrip(), section, labels), "", "```text", *body, "```", ""])
            continue
        # Images, figures, YouTube and raw HTML.
        image = re.match(r"^\.\.\s+(?:image|figure)::\s*(\S+)", stripped)
        if image:
            body, i = directive_body(lines, i + 1)
            alt = next((x.split(":", 1)[1].strip() for x in body if x.strip().startswith(":alt:")), Path(image.group(1)).stem)
            url = asset_url(section, source_root, current, image.group(1))
            rendered = f"![{inline_rst(alt, section, labels)}]({url})" if url.startswith("/") else f"[{inline_rst(alt, section, labels)}]({url})"
            out.append(rendered)
            caption = next((x for x in body if x.strip() and not x.strip().startswith(":") ), None)
            if caption:
                out.append(f"*{inline_rst(caption.strip(), section, labels)}*")
            continue
        yt = re.match(r"^\.\.\s+youtube::\s*([\w-]+)", stripped)
        if yt:
            out.append(f'<div className="video"><iframe src="https://www.youtube-nocookie.com/embed/{yt.group(1)}" title="YouTube video" loading="lazy" allowFullScreen /></div>')
            i += 1
            continue
        raw = re.match(r"^\.\.\s+raw::\s*(\S*)", stripped)
        if raw:
            body, i = directive_body(lines, i + 1)
            out.extend(body)
            continue
        # Admonitions retain their complete body in a Fumadocs-compatible callout.
        admon = re.match(r"^\.\.\s+(note|warning|tip|important|caution|attention)::\s*(.*)", stripped)
        if admon:
            body, i = directive_body(lines, i + 1)
            kind = "warn" if admon.group(1) in {"warning", "caution", "attention"} else "info"
            content = inline_rst("\n".join(([admon.group(2)] if admon.group(2) else []) + body), section, labels).strip()
            out.extend([f'<Callout type="{kind}">', content, "</Callout>", ""])
            continue
        # Tables are converted to a fenced text table if parsing is ambiguous; this retains every cell.
        if stripped in {".. csv-table::", ".. list-table::"} or stripped.startswith(".. csv-table::") or stripped.startswith(".. list-table::"):
            body, i = directive_body(lines, i + 1)
            out.extend(["```text", *body, "```", ""])
            continue
        # Tab directives retain their labels and indented content in markdown sections.
        tab = re.match(r"^\.\.\s+(?:tabs?|group-tab)::\s*(.*)", stripped)
        if tab:
            body, i = directive_body(lines, i + 1)
            label = tab.group(1).strip() or "Option"
            out.extend([f"### {inline_rst(label, section, labels)}", inline_rst("\n".join(body), section, labels), ""])
            continue
        # Unknown directives are preserved as a fenced block, avoiding MDX parser loss.
        unknown = re.match(r"^\.\.\s+([\w.-]+)::\s*(.*)", stripped)
        if unknown:
            body, i = directive_body(lines, i + 1)
            payload = [f".. {unknown.group(1)}:: {unknown.group(2)}", *body]
            out.extend(["```text", *payload, "```", ""])
            continue
        # RST overline/underline headings.  Avoid treating short punctuation in code/table blocks as headings.
        if i + 1 < len(lines) and line.strip() and re.match(r"^\s*(=+|-+|\^+|~+|\++|#+)\s*$", lines[i + 1]) and len(lines[i + 1].strip()) >= 3:
            level = {"=": 1, "-": 2, "^": 3, "~": 4, "+": 5, "#": 5}.get(lines[i + 1].strip()[0], 2)
            out.extend(["#" * level + " " + inline_rst(line.strip(), section, labels), ""])
            i += 2
            continue
        # Literal substitutions.
        for key, value in substitutions.items():
            line = line.replace(f"|{key}|", f"![{key}]({value})")
        out.append(inline_rst(line, section, labels))
        i += 1
    # Keep source text while ensuring MDX gets a terminating newline.
    result = "\n".join(out).replace("\n\n\n\n", "\n\n").strip()
    # A few legacy pages put an image directive inside emphasis.  Convert that
    # spelling as well so no Sphinx directive leaks into generated MDX.
    result = re.sub(r"\*\.\.\s+image::\s*([^*\s]+)\*", lambda m: f"![{Path(m.group(1)).stem}]({asset_url(section, source_root, current, m.group(1))})", result)
    # Directives nested inside an admonition/list can survive the first pass;
    # normalize those simple forms in a final deterministic sweep.
    def nested_code(match: re.Match) -> str:
        lang = match.group(1).strip() or "text"
        body = textwrap.dedent(match.group(2)).rstrip()
        return f"```{lang}\n{body}\n```"
    result = re.sub(r"(?m)^[ \t]*\.\.\s+(?:code-block|code)::\s*([^\n]*)\n((?:[ \t].*(?:\n|$))*)", nested_code, result)
    result = re.sub(r"(?m)^[ \t]*\.\.\s+(?:image|figure)::\s*(\S+)", lambda m: f"![{Path(m.group(1)).stem}]({asset_url(section, source_root, current, m.group(1))})", result)
    result = inline_rst(result, section, labels)
    return normalize_wiki_markup(safe_code_fences(result)) + "\n"


def write_page(path: Path, title: str, description: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml_frontmatter(title, description) + body, encoding="utf-8")


def safe_code_fences(body: str) -> str:
    """Use Shiki language names accepted by Fumadocs for imported code fences."""
    aliases = {"c++": "cpp", "C++": "cpp", "c#": "csharp", "C#": "csharp", "shell-session": "shell", "console": "text"}
    known = {"bash", "sh", "shell", "python", "py", "cpp", "c", "csharp", "javascript", "js", "typescript", "ts", "json", "yaml", "yml", "xml", "html", "css", "sql", "text", "txt", "plaintext", "diff", "rust", "go", "java", "powershell", "cmake", "makefile", "ini", "toml", "dockerfile"}
    def fix(match: re.Match) -> str:
        token = match.group(1)
        token = aliases.get(token, token)
        return "```" + (token if token in known else "text")
    return re.sub(r"```([^\s`\n]+)", fix, body)


def normalize_wiki_markup(body: str) -> str:
    """Escape literal angle/braces while retaining the few intentional MDX embeds."""
    protected: dict[str, str] = {}
    def protect(match: re.Match) -> str:
        key = f"__MDX_EMBED_{len(protected)}__"
        protected[key] = match.group(0)
        return key
    body = re.sub(r"</?(?:Callout|div|iframe)\b[^>]*?/?>", protect, body)
    lines: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if not in_fence:
            line = line.replace("<", "&lt;").replace(">", "&gt;")
            line = line.replace("{", "&#123;").replace("}", "&#125;")
        lines.append(line)
    body = "\n".join(lines)
    for key, value in protected.items():
        body = body.replace(key, value)
    return body


def sanitize_imported_markdown(body: str) -> str:
    """Keep authored Markdown literal while preventing accidental JSX/remote-image builds."""
    # Image processing in Fumadocs probes remote images at build time. Preserve the
    # source URL as a normal link when the asset is outside the snapshotted corpus.
    body = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"[\1](\2)", body)
    body = safe_code_fences(body)
    lines: list[str] = []
    in_fence = False
    for line in body.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        if not in_fence:
            # MDX treats arbitrary HTML-ish text and braces as JSX/expressions.
            line = line.replace("<", "&lt;").replace(">", "&gt;")
            line = line.replace("{", "&#123;").replace("}", "&#125;")
        lines.append(line)
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")


def generate_wiki() -> dict[str, dict[str, str]]:
    labels_by_section: dict[str, dict[str, str]] = {}
    for section in WIKI_SECTIONS:
        source_root = SOURCES / "wiki" / section / "source"
        labels = collect_labels(section, source_root)
        labels_by_section[section] = labels
        for rst in sorted(source_root.rglob("*.rst")):
            rel = rst.relative_to(source_root)
            destination = CONTENT / section / rel.with_suffix(".mdx")
            title = title_from_name(rel.stem)
            body = rst_to_mdx(rst.read_text(encoding="utf-8", errors="replace"), section, source_root, rel, labels)
            write_page(destination, title, SECTION_DESCRIPTIONS[section], body)
        # Copy every source asset, including shared wiki images, to public URLs.
        for asset in sorted(source_root.rglob("*")):
            if asset.is_file() and asset.suffix.lower() not in {".rst", ".py", ".inc"}:
                rel = asset.relative_to(source_root)
                target = PUBLIC / section / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset, target)
    shared = SOURCES / "wiki-images"
    if shared.exists():
        for asset in shared.rglob("*"):
            if asset.is_file():
                rel = asset.relative_to(shared)
                target = PUBLIC / "images" / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(asset, target)
    return labels_by_section


def generate_ardupilot() -> int:
    count = 0
    source = SOURCES / "ardupilot"
    for p in sorted(source.rglob("*")):
        if not p.is_file() or not (p.suffix.lower() in {".md", ".markdown", ".txt"} or "docs" in p.relative_to(source).parts):
            continue
        rel = p.relative_to(source)
        # Keep a stable, route-safe path.  ReleaseNotes.txt is Markdown-compatible text.
        destination = CONTENT / "ardupilot" / rel.with_suffix(".mdx")
        title = title_from_name(rel.stem)
        raw = p.read_text(encoding="utf-8", errors="replace")
        body = sanitize_imported_markdown(raw) if p.suffix.lower() in {".md", ".markdown", ".txt"} else "```text\n" + raw + "\n```\n"
        write_page(destination, title, "ArduPilot project documentation", body)
        count += 1
    return count


def xml_text(node: ET.Element | None) -> str:
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def generate_mavlink() -> int:
    source = SOURCES / "mavlink"
    count = 0
    for p in sorted(source.rglob("*.md")):
        rel = p.relative_to(source)
        destination = CONTENT / "mavlink" / "guides" / rel.with_suffix(".mdx")
        write_page(destination, title_from_name(rel.stem), "MAVLink protocol documentation", p.read_text(encoding="utf-8", errors="replace"))
        count += 1
    xml_root = source / "message_definitions" / "v1.0"
    for p in sorted(xml_root.glob("*.xml")):
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError as exc:
            raise SystemExit(f"Invalid MAVLink XML {p}: {exc}")
        lines = [f"# {p.stem} dialect", "", f"MAVLink dialect **{p.stem}**.", ""]
        enums = root.findall(".//enum")
        if enums:
            lines += ["## Enumerations", ""]
            for enum in enums:
                name = enum.attrib.get("name", "")
                lines += [f"### `{name}`", ""]
                desc = xml_text(enum.find("description"))
                if desc: lines += [desc, ""]
                for entry in enum.findall("entry"):
                    lines.append(f"- `{entry.attrib.get('name', '')}` = `{entry.attrib.get('value', '')}` — {xml_text(entry.find('description'))}")
                lines.append("")
        messages = root.findall(".//message")
        if messages:
            lines += ["## Messages", ""]
            for msg in sorted(messages, key=lambda n: int(n.attrib.get("id", "0"))):
                lines += [f"### {msg.attrib.get('name', '')} (ID {msg.attrib.get('id', '')})", ""]
                desc = xml_text(msg.find("description"))
                if desc: lines += [desc, ""]
                fields = msg.findall("field")
                if fields:
                    lines += ["| Name | Type | Description |", "| --- | --- | --- |"]
                    for field in fields:
                        lines.append(f"| `{field.attrib.get('name', '')}` | `{field.attrib.get('type', '')}` | {xml_text(field)} |")
                    lines.append("")
        destination = CONTENT / "mavlink" / "dialects" / f"{p.stem}.mdx"
        write_page(destination, f"MAVLink {p.stem} dialect", "Generated reference for the MAVLink XML dialect.", "\n".join(lines))
        count += 1
    return count


def parse_doxygen_xml(xml_dir: Path, output: Path) -> int:
    count = 0
    for p in sorted(xml_dir.glob("*.xml")):
        try:
            root = ET.parse(p).getroot()
        except ET.ParseError:
            continue
        for compound in root.findall(".//compounddef"):
            kind = compound.attrib.get("kind", "")
            if kind not in {"class", "struct", "namespace", "file"}:
                continue
            name = xml_text(compound.find("compoundname")) or p.stem
            members = compound.findall(".//memberdef")
            lines = [f"# {name}", "", xml_text(compound.find("briefdescription")), ""]
            if members:
                lines += ["## Members", ""]
                for member in members:
                    mname = xml_text(member.find("name"))
                    sig = xml_text(member.find("definition")) or mname
                    desc = xml_text(member.find("detaileddescription")) or xml_text(member.find("briefdescription"))
                    lines += [f"### `{sig}`", "", desc, ""]
            write_page(output / f"{re.sub(r'[^A-Za-z0-9_.-]+', '_', name)}.mdx", name, "Generated ArduPilot API reference.", "\n".join(lines))
            count += 1
    return count


def generate_api() -> int:
    output = CONTENT / "api"
    output.mkdir(parents=True, exist_ok=True)
    # Prefer checked-in/generated Doxygen XML if present.  Otherwise create deterministic API pages
    # from the snapshotted ArduSub and library headers, so builds never require Doxygen.
    xml_dir = SOURCES / "generated" / "doxygen" / "xml"
    if not xml_dir.exists():
        xml_dir = SOURCES / "doxygen" / "xml"
    count = parse_doxygen_xml(xml_dir, output) if xml_dir.exists() else 0
    if count:
        return count
    inputs = SOURCES / "api-inputs"
    for p in sorted(inputs.rglob("*.h")):
        rel = p.relative_to(inputs)
        text = p.read_text(encoding="utf-8", errors="replace")
        symbols = []
        for m in re.finditer(r"^\s*(?:class|struct|enum)\s+([A-Za-z_][\w]*)|^\s*([A-Za-z_][\w:]*)\s*\([^;{}]*\)\s*;", text, re.MULTILINE):
            symbols.append(m.group(1) or m.group(2))
        title = rel.stem
        lines = [f"# {title}", "", f"Source: `{rel.as_posix()}`", "", "## Declarations", ""]
        if symbols:
            lines.extend(f"- `{s}`" for s in sorted(set(symbols)))
        else:
            lines.append("No public declarations were detected in this header.")
        destination = output / rel.with_suffix(".mdx")
        write_page(destination, title, "Generated ArduPilot API reference.", "\n".join(lines))
        count += 1
    if not count:
        write_page(output / "index.mdx", "ArduPilot API", SECTION_DESCRIPTIONS["api"], "API inputs are not available in this source snapshot.")
        count = 1
    return count


def generate_doxygen_xml() -> None:
    """Optionally refresh XML with Doxygen when the executable is installed."""
    doxygen = shutil.which("doxygen")
    if not doxygen:
        return
    output = SOURCES / "generated" / "doxygen"
    output.mkdir(parents=True, exist_ok=True)
    config = output / "Doxyfile"
    config.write_text(
        "\n".join([
            f"OUTPUT_DIRECTORY = {output}",
            "GENERATE_XML = YES",
            "XML_OUTPUT = xml",
            f"INPUT = {SOURCES / 'api-inputs' / 'ArduSub'} {SOURCES / 'api-inputs' / 'libraries'}",
            "RECURSIVE = YES",
            "EXTRACT_ALL = YES",
            "QUIET = YES",
        ]) + "\n",
        encoding="utf-8",
    )
    subprocess.run([doxygen, str(config)], check=True)


def _toctree_entries(rst: Path) -> list[str]:
    """Read the ordered targets from Sphinx toctrees without rewriting source text."""
    lines = rst.read_text(encoding="utf-8", errors="replace").splitlines()
    entries: list[str] = []
    in_tree = False
    for line in lines:
        if re.match(r"^\s*\.\.\s+toctree::", line):
            in_tree = True
            continue
        if not in_tree:
            continue
        if line.strip() and not line.startswith((" ", "\t")):
            in_tree = False
            continue
        value = line.strip()
        if not value or value.startswith(":"):
            continue
        # Named entries use ``Title <target>``; navigation uses the target path.
        named = re.search(r"<([^>]+)>\s*$", value)
        entries.append((named.group(1) if named else value).strip())
    return entries


def source_page_order(section: str) -> list[Path]:
    source_root = SOURCES / "wiki" / section / "source"
    all_pages = {p.relative_to(source_root).with_suffix(""): p for p in source_root.rglob("*.rst")}
    order: list[Path] = []
    visited: set[Path] = set()

    def resolve(current: Path, target: str) -> Path | None:
        target = target.split("#", 1)[0].strip().replace("\\", "/")
        if not target or target.startswith("http"):
            return None
        candidate = (current.parent / target).as_posix()
        candidate_path = Path(candidate)
        if candidate_path.suffix != ".rst":
            candidate_path = candidate_path.with_suffix(".rst")
        try:
            rel = candidate_path.resolve().relative_to(source_root.resolve())
        except ValueError:
            return None
        if rel.with_suffix("") in all_pages:
            return rel.with_suffix("")
        index = rel / "index.rst"
        return index.with_suffix("") if index.with_suffix("") in all_pages else None

    def visit(rel: Path) -> None:
        if rel in visited or rel not in all_pages:
            return
        visited.add(rel)
        order.append(rel)
        rst = all_pages[rel]
        for target in _toctree_entries(rst):
            child = resolve(rst, target)
            if child:
                visit(child)

    root = Path("index")
    visit(root)
    for rel in sorted(all_pages):
        visit(rel)
    return order


def nav_group(section: str, directory: Path, item: str) -> str | None:
    """Return a human topic heading for a folder's otherwise flat orphan pages."""
    if directory.name == "docs":
        value = item.lower().replace("_", "-")
        if section == "dev":
            if any(x in value for x in ("build", "setup", "install", "getting", "where-to-get")):
                return "Getting started and building"
            if any(x in value for x in ("sitl", "simulation", "gazebo", "simulator", "xplane", "realflight")):
                return "Simulation and testing"
            if any(x in value for x in ("mavlink", "mavproxy", "ros", "companion", "dronecan", "gcs")):
                return "Integrations and communication"
            if any(x in value for x in ("code", "porting", "library", "parameter", "firmware", "architecture")):
                return "Code and architecture"
            if any(x in value for x in ("release", "ci", "test", "debug", "profil")):
                return "Testing and releases"
            return "Developer guides"
        if section == "sub":
            if any(x in value for x in ("setup", "introduction", "first", "configuration", "hardware")):
                return "Setup and hardware"
            if any(x in value for x in ("failsafe", "arming", "mode", "tuning", "pilot", "control")):
                return "Vehicle operation and tuning"
            return "Reference and advanced topics"
        if section == "mavproxy":
            return "MAVProxy guides"
    if section == "ardupilot" and directory == CONTENT / "ardupilot":
        if item in {"ArduCopter", "ArduPlane", "ArduSub", "Rover", "AntennaTracker"}:
            return "Vehicle projects"
        if item in {"libraries", "Tools"}:
            return "Libraries and tools"
        if item in {"README", "BUILD", "CODE_OF_CONDUCT"}:
            return "Project overview"
        return "Repository documentation"
    if section == "mavlink" and directory == CONTENT / "mavlink":
        return "MAVLink reference"
    if section == "api" and directory == CONTENT / "api":
        return "API namespaces"
    return None


def generate_meta() -> int:
    count = 0
    section_order = ["sub", "mavproxy", "dev", "mavlink", "ardupilot", "api"]
    root_pages = ["---User guides---", "sub", "mavproxy", "---Development---", "dev", "---Protocols---", "mavlink", "---Project documentation---", "ardupilot", "---API reference---", "api"]
    (CONTENT / "meta.json").write_text(json.dumps({"pages": root_pages, "pagesIndex": "index", "description": "Unified ArduPilot documentation"}, indent=2) + "\n", encoding="utf-8")
    count += 1
    section_ranks = {section: {p: i for i, p in enumerate(source_page_order(section))} for section in WIKI_SECTIONS}

    for directory in sorted(CONTENT.rglob("*")):
        if not directory.is_dir() or directory.name == ".source":
            continue
        section = next((s for s in ("sub", "dev", "mavproxy", "mavlink", "ardupilot", "api") if directory == CONTENT / s or CONTENT / s in directory.parents), None)
        if not section:
            continue
        files = [p for p in directory.iterdir() if p.is_file() and p.suffix in {".mdx", ".md"} and p.stem != "index"]
        folders = [p for p in directory.iterdir() if p.is_dir() and p.name != ".source" and any(p.rglob("*.mdx"))]
        rank = section_ranks.get(section, {})
        def child_rank(p: Path) -> tuple[int, str]:
            rel = p.relative_to(CONTENT / section).with_suffix("")
            if p.is_dir():
                candidates = [v for k, v in rank.items() if Path(k).parts[:len(rel.parts)] == rel.parts]
                return (min(candidates) if candidates else 10**9, p.name.lower())
            return (rank.get(rel, 10**9), p.stem.lower())
        children = sorted(files + folders, key=child_rank)
        pages: list[str] = []
        seen_groups: set[str] = set()
        for child in children:
            name = child.stem if child.is_file() else child.name
            group_name = nav_group(section, directory, name)
            if group_name and group_name not in seen_groups:
                pages.append(f"---{group_name}---")
                seen_groups.add(group_name)
            pages.append(name)
        if not pages and not (directory / "index.mdx").exists():
            continue
        metadata: dict[str, object] = {"pages": pages, "defaultOpen": True}
        if (directory / "index.mdx").exists():
            metadata["pagesIndex"] = "index"
        if directory == CONTENT / section:
            metadata["title"] = SECTION_TITLES.get(section, title_from_name(section))
            metadata["description"] = SECTION_DESCRIPTIONS.get(section, "")
        (directory / "meta.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        count += 1
    return count


def generate_root_index() -> None:
    write_page(
        CONTENT / "index.mdx",
        "Unified ArduPilot Documentation",
        "ArduPilot, ArduSub, MAVProxy, MAVLink, project, and API documentation.",
        """Explore the unified ArduPilot documentation library.

## Sections

- [ArduSub](/docs/sub)
- [Developer documentation](/docs/dev)
- [MAVProxy](/docs/mavproxy)
- [MAVLink](/docs/mavlink)
- [ArduPilot project](/docs/ardupilot)
- [API reference](/docs/api)
""",
    )


def generate_section_indexes() -> None:
    """Create consistent landing pages for sections that originate outside the wiki."""
    landing = {
        "mavlink": ("MAVLink", "Protocol guides and generated references for every included MAVLink dialect.", [
            "- [Guides](guides)", "- [Dialect reference](dialects)",
        ]),
        "ardupilot": ("ArduPilot project documentation", "Project guides, vehicle documentation, tools, libraries, and release notes.", [
            "- [Project overview](README)", "- [Vehicle projects](ArduCopter)", "- [Libraries](libraries)", "- [Tools](Tools)", "- [Build and configuration](docs)",
        ]),
        "api": ("ArduPilot API reference", "Generated reference for ArduSub and ArduPilot libraries.", [
            "- [ArduSub API](ArduSub)", "- [Library APIs](libraries)",
        ]),
    }
    for section, (title, description, links) in landing.items():
        path = CONTENT / section / "index.mdx"
        write_page(path, title, description, "\n".join([description, "", "## Browse this section", "", *links, ""]))


def verify() -> None:
    errors: list[str] = []
    for p in CONTENT.rglob("*.mdx"):
        text = p.read_text(encoding="utf-8")
        if ".. code-block::" in text or ".. image::" in text or ".. toctree::" in text:
            errors.append(f"unconverted RST directive in {p}")
        if not text.startswith("---\n"):
            errors.append(f"missing frontmatter in {p}")
        for image in re.findall(r"!\[[^]]*\]\((/docs/[^)]+)\)", text):
            if image.startswith("/docs/images/"):
                target = PUBLIC / image.removeprefix("/docs/images/")
            else:
                target = PUBLIC / image.removeprefix("/docs/")
            if not target.exists() and not image.startswith("/docs/ardupilot/"):
                errors.append(f"missing asset {image} referenced by {p}")
    if errors:
        raise SystemExit("\n".join(errors[:50]) + (f"\n... {len(errors)-50} more" if len(errors) > 50 else ""))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh-sources", action="store_true", help="replace immutable snapshots from the three checkouts")
    parser.add_argument("--with-doxygen", action="store_true", help="refresh generated Doxygen XML when doxygen is installed")
    parser.add_argument("--verify", action="store_true", help="validate generated content without rebuilding")
    args = parser.parse_args()
    if args.verify:
        verify()
        print("Documentation conversion checks passed")
        return
    inventory = snapshot_sources(args.refresh_sources)
    checksum_manifest()
    clean_generated()
    if args.with_doxygen:
        generate_doxygen_xml()
    generate_wiki()
    ardupilot_count = generate_ardupilot()
    mavlink_count = generate_mavlink()
    api_count = generate_api()
    generate_root_index()
    generate_section_indexes()
    meta_count = generate_meta()
    checksum_manifest()
    verify()
    print(f"Imported {sum(len(v) for v in inventory.values())} source inputs; generated {ardupilot_count} ArduPilot, {mavlink_count} MAVLink, {api_count} API pages and {meta_count} navigation files.")


if __name__ == "__main__":
    main()
