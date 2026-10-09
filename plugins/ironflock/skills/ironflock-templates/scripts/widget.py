#!/usr/bin/env python3
"""Show an IronFlock widget's configuration schema, as published on npm.

Every widget is an npm package (@record-evolution/<package>) that ships
src/definition-schema.json (the shape of its chartconfig, with descriptions
written for agents) and src/default-data.json (a working example). This script
reads them for the version tagged `latest` and caches them by exact version.

    widget.py widget-gauge                      compact outline of every property
    widget.py widget-gauge --path dataseries    outline of one subtree
    widget.py widget-gauge --full               the raw definition-schema.json
    widget.py widget-gauge --example            the raw default-data.json
    widget.py widget-gauge --version 1.7.30     a specific release
    widget.py widget-scada --when symbol=cylindrical-tank
                                                hide properties whose `condition`
                                                does not match (big schemas)
    widget.py widget-scada --symbols tank       SCADA symbols matching "tank": id,
                                                size, aspectRatio, bindings

Standard library only, so plain `python3 widget.py ...` works.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

NPM_LATEST = "https://registry.npmjs.org/@record-evolution%2F{pkg}/latest"
CDN_FILE = "https://cdn.jsdelivr.net/npm/@record-evolution/{pkg}@{version}/src/{name}"
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "ironflock-templates"


class FetchError(Exception):
    pass


def fetch_bytes(url: str) -> bytes:
    """GET a URL. Falls back to curl when Python has no usable CA bundle
    (common with the python.org macOS installer), never on an HTTP error."""
    try:
        context = None
        try:
            import certifi  # type: ignore[import-not-found]

            context = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            pass
        with urllib.request.urlopen(url, timeout=30, context=context) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise FetchError(f"{url}: HTTP {exc.code}") from exc
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        if not shutil.which("curl"):
            raise FetchError(f"{url}: {exc}") from exc
        done = subprocess.run(
            ["curl", "-fsSL", "--max-time", "30", url], capture_output=True
        )
        if done.returncode != 0:
            raise FetchError(f"{url}: {done.stderr.decode().strip() or exc}") from exc
        return done.stdout


def fetch_json(url: str):
    return json.loads(fetch_bytes(url))


def latest_version(package: str) -> str:
    try:
        return fetch_json(NPM_LATEST.format(pkg=package))["version"]
    except FetchError as exc:
        raise FetchError(
            f"cannot resolve {package}: {exc}. Package names look like widget-gauge; "
            "see references/widget-catalog.md for the list."
        ) from exc


def widget_file(package: str, version: str, name: str):
    """Return a parsed file from the package's src/ folder, cached by version
    (a published npm version is immutable, so the cache never goes stale)."""
    path = CACHE / f"{package}@{version}" / name
    if path.exists():
        return json.loads(path.read_text())
    data = fetch_json(CDN_FILE.format(pkg=package, version=version, name=name))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    except OSError:
        pass  # a read-only home is no reason to fail
    return data


def load_schema(package: str, version: str | None = None) -> tuple[str, dict]:
    version = version or latest_version(package)
    return version, widget_file(package, version, "definition-schema.json")


# --------------------------------------------------------------------------
# Outline rendering
# --------------------------------------------------------------------------


def _type_of(node: dict) -> str:
    t = node.get("type")
    if isinstance(t, list):
        t = "|".join(t)
    if t == "array" and isinstance(node.get("items"), dict):
        inner = node["items"].get("type")
        if inner and inner not in ("object", "array"):
            return f"array of {inner}"
    return t or "any"


def _first_sentence(text: str, limit: int = 160) -> str:
    text = " ".join(str(text).split())
    for stop in (". ", "? ", "! "):
        if stop in text:
            text = text.split(stop, 1)[0] + stop.strip()
            break
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _values(values: list, limit: int) -> str:
    shown = ", ".join(json.dumps(v) for v in values[:limit])
    return shown + (f", … +{len(values) - limit} more (--full --path)" if len(values) > limit else "")


def _flags(node: dict, required: bool, condition_met: bool = False) -> str:
    flags = []
    if required:
        flags.append("required")
    if node.get("dataDrivenDisabled"):
        flags.append("dataDrivenDisabled")
    if "enum" in node:
        flags.append("enum: " + _values(node["enum"], 12))
    if "default" in node:
        flags.append(f"default: {json.dumps(node['default'])}")
    cond = node.get("condition")
    if isinstance(cond, dict) and not condition_met:
        flags.append(
            f"shown if {cond.get('relativePath')} in [{_values(cond.get('showIfValueIn') or [], 6)}]"
        )
    return f" [{'; '.join(flags)}]" if flags else ""


def condition_target(path: list[str], relative: str) -> str:
    """Resolve a condition's relativePath against the property's own path.
    Each `..` steps out of one level, starting with the property itself:
    `../multiChart` on dataseries.[].value is dataseries.[].multiChart."""
    target = list(path)
    for part in relative.split("/"):
        if part == "..":
            if target:
                target.pop()
        elif part not in ("", "."):
            target.append(part)
    return ".".join(p for p in target if p != "[]")


def hidden(node: dict, path: list[str], when: dict[str, str]) -> bool:
    cond = node.get("condition")
    if not when or not isinstance(cond, dict) or not cond.get("relativePath"):
        return False
    target = condition_target(path, cond["relativePath"])
    if target not in when:
        return False
    allowed = [str(v).lower() for v in cond.get("showIfValueIn") or []]
    return when[target].lower() not in allowed


def outline(
    node: dict,
    name: str = "",
    depth: int = 0,
    required: bool = False,
    path: list[str] | None = None,
    when: dict[str, str] | None = None,
) -> list[str]:
    lines = []
    pad = "  " * depth
    path = path or []
    when = when or {}
    if name and hidden(node, path, when):
        return []
    if name:
        desc = node.get("description") or node.get("title") or ""
        cond = node.get("condition")
        met = isinstance(cond, dict) and condition_target(path, cond.get("relativePath", "")) in when
        line = f"{pad}{name}: {_type_of(node)}{_flags(node, required, met)}"
        if desc:
            line += f" — {_first_sentence(desc)}"
        lines.append(line)
        depth += 1
    props = node.get("properties")
    if isinstance(props, dict):
        req = set(node.get("required") or [])
        ordered = sorted(props.items(), key=lambda kv: (kv[1].get("order", 1e9), kv[0]))
        for key, child in ordered:
            if isinstance(child, dict):
                lines += outline(child, key, depth, key in req, path + [key], when)
    items = node.get("items")
    if isinstance(items, dict) and (items.get("properties") or items.get("items")):
        lines.append(f"{'  ' * depth}[] item: {_type_of(items)}")
        lines += outline(items, "", depth + 1, False, path + ["[]"], when)
    return lines


def subtree(schema: dict, dotted: str) -> dict:
    node = schema
    for part in dotted.split("."):
        if part in ("[]", "items"):
            node = node.get("items", {})
            continue
        props = node.get("properties")
        if not isinstance(props, dict) and isinstance(node.get("items"), dict):
            node = node["items"]  # let "dataseries.data" step through arrays
            props = node.get("properties")
        if not isinstance(props, dict) or part not in props:
            raise KeyError(dotted)
        node = props[part]
    return node


def print_symbols(package: str, version: str, query: str) -> int:
    if package != "widget-scada":
        print("error: --symbols only applies to widget-scada", file=sys.stderr)
        return 1
    catalog = widget_file(package, version, "symbol-catalog.json")
    q = query.lower()
    rows = [
        s
        for s in catalog.get("symbols", [])
        if not q
        or q in " ".join([s.get("id", ""), s.get("label", ""), s.get("category", ""), *s.get("searchTags", [])]).lower()
    ]
    print(f"{package}@{version}: {len(rows)} symbol(s). aspectRatio is height/width; widgetSize is the natural size in grid cells.")
    for s in rows:
        bindings = ", ".join(f"{k}:{v}" for k, v in (s.get("bindings") or {}).items())
        print(
            f"\n{s['id']} — {s.get('label', '')} [{s.get('category', '')}]"
            f"\n  widgetSize {s.get('widgetSizeX')}x{s.get('widgetSizeY')}, aspectRatio {s.get('aspectRatio')}"
            f"{', clickable' if s.get('clickable') else ''}"
            f"\n  bindings: {bindings or '-'}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("package", help="widget package name, e.g. widget-gauge")
    parser.add_argument("--version", help="exact version (default: npm latest)")
    parser.add_argument("--path", help="dotted property path to show only a subtree")
    parser.add_argument(
        "--when",
        action="append",
        default=[],
        metavar="PROP=VALUE",
        help="hide properties whose condition excludes this value (repeatable)",
    )
    parser.add_argument(
        "--symbols",
        nargs="?",
        const="",
        metavar="QUERY",
        help="widget-scada only: list symbols whose id, label, category or tags contain QUERY",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--full", action="store_true", help="print the raw definition schema")
    mode.add_argument("--example", action="store_true", help="print default-data.json")
    args = parser.parse_args()

    try:
        version, schema = load_schema(args.package, args.version)
        if args.symbols is not None:
            return print_symbols(args.package, version, args.symbols)
        if args.example:
            print(json.dumps(widget_file(args.package, version, "default-data.json"), indent=2))
            return 0
        node = subtree(schema, args.path) if args.path else schema
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyError:
        print(f"error: no property path {args.path!r} in {args.package}", file=sys.stderr)
        return 1

    if args.full:
        print(json.dumps(node, indent=2, ensure_ascii=False))
        return 0
    print(f"{args.package}@{version}" + (f" — {args.path}" if args.path else ""))
    if not args.path and schema.get("description"):
        print(_first_sentence(schema["description"], 400))
    print()
    when = dict(w.split("=", 1) for w in args.when if "=" in w)
    base = [p for p in args.path.split(".")] if args.path else []
    print("\n".join(outline(node, path=base, when=when)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
