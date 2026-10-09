#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pyyaml>=6.0", "jsonschema>=4.18"]
# ///
"""Validate an IronFlock app's .ironflock templates before the platform sees them.

    validate.py [APP_DIR]            APP_DIR or APP_DIR/.ironflock (default: .)
    validate.py [APP_DIR] --offline  skip checks that need widget schemas from npm

Every template file present is checked against its JSON Schema (the copies in
../schemas, identical to https://ironflock.com/schemas/<name>/v1.yml), then
against the rules the platform only enforces at install or render time:
fleetdb's data-template checks, cross-file references (board data bindings must
name real tables and columns), and each board widget's config against that
widget's published definition schema.

Prints one line per finding as `LEVEL file:line path: message` and exits 1 if
there is any ERROR. Warnings point at things that are legal but probably wrong.

Needs PyYAML and jsonschema. Without them it re-runs itself through `uv run`
(which installs both from the header above); otherwise install them with
`python3 -m pip install pyyaml jsonschema`.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def _bootstrap() -> None:
    try:
        import jsonschema  # noqa: F401
        import yaml  # noqa: F401

        return
    except ImportError:
        pass
    if shutil.which("uv") and not os.environ.get("IRONFLOCK_VALIDATE_REEXEC"):
        os.environ["IRONFLOCK_VALIDATE_REEXEC"] = "1"
        script = str(Path(__file__).resolve())
        os.execvp("uv", ["uv", "run", "--quiet", "--script", script, *sys.argv[1:]])
    sys.exit(
        "validate.py needs PyYAML and jsonschema. Run it with `uv run validate.py`, "
        "or install them: python3 -m pip install pyyaml jsonschema"
    )


_bootstrap()

import argparse  # noqa: E402
import re  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Iterator  # noqa: E402

import yaml  # noqa: E402
from jsonschema.exceptions import best_match  # noqa: E402
from jsonschema.validators import validator_for  # noqa: E402

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE.parent / "schemas"
sys.path.insert(0, str(HERE))

TEMPLATES = ["data-template", "board-template", "env-template", "port-template", "ai-template"]


# --------------------------------------------------------------------------
# YAML loading. The platform parses templates with js-yaml (YAML 1.2), where
# `yes`/`no`/`on`/`off` are strings, dates stay strings and a bare `=` is just
# a string. PyYAML follows YAML 1.1, so align it — otherwise `valueList: [on,
# off]` would turn into booleans here, and the `operator: =` the board editor
# writes would not load at all.
# --------------------------------------------------------------------------


class Yaml12Loader(yaml.SafeLoader):
    pass


Yaml12Loader.yaml_implicit_resolvers = {
    first: [
        (tag, rx)
        for tag, rx in resolvers
        if tag not in ("tag:yaml.org,2002:bool", "tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:value")
    ]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
Yaml12Loader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


@dataclass
class Doc:
    file: str
    data: Any
    root: yaml.Node | None

    def line(self, path: list) -> int | None:
        node = self.root
        if node is None:
            return None
        for part in path:
            if isinstance(node, yaml.MappingNode):
                for key, value in node.value:
                    if key.value == str(part):
                        node = value
                        break
                else:
                    break
            elif isinstance(node, yaml.SequenceNode) and isinstance(part, int):
                if part >= len(node.value):
                    break
                node = node.value[part]
            else:
                break
        return node.start_mark.line + 1


@dataclass
class Issue:
    level: str
    file: str
    line: int | None
    path: list
    message: str


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)

    def add(self, level: str, doc: Doc, path: list, message: str) -> None:
        self.issues.append(Issue(level, doc.file, doc.line(path), list(path), message))

    def error(self, doc: Doc, path: list, message: str) -> None:
        self.add("ERROR", doc, path, message)

    def warn(self, doc: Doc, path: list, message: str) -> None:
        self.add("WARN", doc, path, message)


def fmt_path(path: list) -> str:
    out = ""
    for part in path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(root)"


def duplicate_keys(node: yaml.Node, path: list) -> Iterator[tuple[list, str]]:
    if isinstance(node, yaml.MappingNode):
        seen = set()
        for key, value in node.value:
            name = key.value
            if name in seen:
                yield path + [name], f"key '{name}' appears twice; YAML keeps only the last one"
            seen.add(name)
            yield from duplicate_keys(value, path + [name])
    elif isinstance(node, yaml.SequenceNode):
        for i, item in enumerate(node.value):
            yield from duplicate_keys(item, path + [i])


def load(path: Path, report: Report) -> Doc | None:
    text = path.read_text(encoding="utf-8")
    try:
        root = yaml.compose(text, Loader=Yaml12Loader)
        data = yaml.load(text, Loader=Yaml12Loader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        report.issues.append(
            Issue("ERROR", path.name, mark.line + 1 if mark else None, [], f"not valid YAML: {exc}")
        )
        return None
    doc = Doc(path.name, data, root)
    if root is not None:
        for dup_path, message in duplicate_keys(root, []):
            report.error(doc, dup_path, message)
    return doc


# --------------------------------------------------------------------------
# JSON Schema
# --------------------------------------------------------------------------


def check_schema(name: str, doc: Doc, report: Report) -> bool:
    schema_file = SCHEMAS / name / "v1.yml"
    if not schema_file.exists():
        report.warn(doc, [], f"no schema bundled for {name}; structural check skipped")
        return True
    schema = yaml.load(schema_file.read_text(encoding="utf-8"), Loader=Yaml12Loader)
    validator = validator_for(schema)(schema)
    ok = True
    for err in sorted(validator.iter_errors(doc.data), key=lambda e: list(e.absolute_path)):
        # A failed anyOf reports one summary error ("is not valid under any of
        # the given schemas"); best_match descends to the branch that explains it.
        best = best_match([err])
        message = re.sub(r"\bNone\b", "null", re.sub(r"\bTrue\b", "true", re.sub(r"\bFalse\b", "false", best.message)))
        report.error(doc, list(best.absolute_path), message)
        ok = False
    first_line = (doc.root.start_mark.buffer or "").split("\n", 1)[0] if doc.root else ""
    expected = f"https://ironflock.com/schemas/{name}/v1.yml"
    if expected not in first_line:
        report.warn(
            doc,
            [],
            f"first line should be `# yaml-language-server: $schema={expected}` "
            "so editors validate the file",
        )
    return ok


# --------------------------------------------------------------------------
# data-template.yml — mirrors the checks the project database service (FleetDB)
# runs when the app is installed, which otherwise surface only then.
# --------------------------------------------------------------------------

# pg-escape 0.2.0 reserved.txt (MIT): fleetdb refuses any table, transform or
# column name that pg-escape would have to quote.
PG_RESERVED = set(
    """aes128 aes256 all allowoverwrite analyse analyze and any array as asc authorization
    backup between binary blanksasnull both bytedict case cast check collate column
    constraint create credentials cross current_date current_time current_timestamp
    current_user current_user_id default deferrable deflate defrag delta delta32k desc
    disable distinct do else emptyasnull enable encode encrypt encryption end except
    explicit false for foreign freeze from full globaldict256 globaldict64k grant group
    gzip having identity ignore ilike in initially inner intersect into is isnull join
    leading left like limit localtime localtimestamp lun luns lzo lzop minus mostly13
    mostly32 mostly8 natural new not notnull null nulls off offline offset old on only
    open or order outer overlaps parallel partition percent placing primary raw
    readratio recover references rejectlog resort restore right select session_user
    similar some sysdate system table tag tdes text255 text32k then to top trailing
    true truncatecolumns union unique user using verbose wallet when where with without""".split()
)
SYSTEM_COLUMNS = {"device_key", "authid"}
FIXED_UNITS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}


@dataclass
class DataModel:
    """What a board may bind to: own tables and transforms with their columns."""

    tables: dict[str, dict[str, str]] = field(default_factory=dict)  # name -> col -> dataType
    transforms: set[str] = field(default_factory=set)
    consumes: set[str] = field(default_factory=set)
    present: bool = False


def check_name(doc: Doc, path: list, name: Any, what: str, report: Report) -> None:
    if not isinstance(name, str):
        return
    if name in PG_RESERVED:
        report.error(doc, path, f"{what} '{name}' is an SQL reserved word; the platform refuses it")
    if len(name.encode()) > 63:
        report.error(doc, path, f"{what} '{name}' is longer than 63 bytes")


def check_data(doc: Doc, report: Report) -> DataModel:
    model = DataModel(present=True)
    data = doc.data if isinstance(doc.data, dict) else {}
    root = data.get("data") if isinstance(data.get("data"), dict) else {}
    seen_relations: set[str] = set()

    for i, table in enumerate(root.get("tables") or []):
        if not isinstance(table, dict):
            continue
        tpath = ["data", "tables", i]
        name = table.get("tablename")
        check_name(doc, tpath + ["tablename"], name, "table name", report)
        if name in seen_relations:
            report.error(doc, tpath + ["tablename"], f"'{name}' is declared twice")
        seen_relations.add(name)
        cols: dict[str, str] = {}
        secrets = set()
        for j, col in enumerate(table.get("columns") or []):
            if not isinstance(col, dict):
                continue
            cpath = tpath + ["columns", j, "id"]
            cid = col.get("id")
            check_name(doc, cpath, cid, "column id", report)
            if isinstance(cid, str) and cid.endswith("__bidx"):
                report.error(doc, cpath, "the suffix '__bidx' is reserved")
            if cid in cols:
                report.error(doc, cpath, f"column '{cid}' is declared twice in '{name}'")
            cols[cid] = col.get("dataType")
            if col.get("secret"):
                secrets.add(cid)
                if cid == "tsp":
                    report.error(doc, cpath, "the tsp column cannot be secret")
        for k, key in enumerate(table.get("maintainLatestFlagFor") or []):
            kpath = tpath + ["maintainLatestFlagFor", k]
            if key == "tsp":
                report.error(doc, kpath, "tsp cannot be an entity key: every row would be its own entity")
            elif key not in cols and key not in SYSTEM_COLUMNS:
                report.error(doc, kpath, f"entity key '{key}' is not a column of '{name}'")
            elif key in secrets:
                report.error(doc, kpath, f"secret column '{key}' cannot be an entity key")
        ds = table.get("downsample")
        if isinstance(ds, dict) and isinstance(ds.get("bucket"), str):
            m = re.match(r"^(\d+) (second|minute|hour|day)s?$", ds["bucket"])
            if m:
                seconds = int(m.group(1)) * FIXED_UNITS[m.group(2)]
                if seconds > 86400:
                    report.error(doc, tpath + ["downsample", "bucket"], "bucket exceeds 1 day")
                elif 86400 % seconds:
                    report.error(doc, tpath + ["downsample", "bucket"], "bucket must divide 1 day evenly")
        if isinstance(ds, dict) and isinstance(name, str) and len(f"{name}_ds".encode()) > 63:
            report.error(doc, tpath + ["tablename"], f"'{name}_ds' (the downsampled copy) exceeds 63 bytes")
        if isinstance(name, str):
            model.tables[name] = {**cols, "device_key": "bigint", "authid": "string"}

    for i, transform in enumerate(root.get("transforms") or []):
        if not isinstance(transform, dict):
            continue
        tpath = ["data", "transforms", i]
        name = transform.get("tablename")
        check_name(doc, tpath + ["tablename"], name, "transform name", report)
        if name in seen_relations:
            report.error(doc, tpath + ["tablename"], f"'{name}' is declared twice")
        seen_relations.add(name)
        schedule = transform.get("schedule")
        if isinstance(schedule, str) and len(schedule.split()) not in (5, 6):
            report.error(doc, tpath + ["schedule"], f"'{schedule}' is not a cron expression")
        if isinstance(name, str):
            model.transforms.add(name)
            model.tables[name] = {
                c.get("id"): c.get("dataType")
                for c in transform.get("columns") or []
                if isinstance(c, dict)
            }

    seen_apps = set()
    for i, consume in enumerate(data.get("consumes") or []):
        app = str((consume or {}).get("app", "")).lower()
        if app in seen_apps:
            report.error(doc, ["consumes", i, "app"], f"app '{app}' is listed twice")
        seen_apps.add(app)
        model.consumes.add(app)
    return model


# --------------------------------------------------------------------------
# env / port / ai
# --------------------------------------------------------------------------


def check_env(doc: Doc, report: Report) -> None:
    if not isinstance(doc.data, dict):
        return
    for name, var in doc.data.items():
        if not isinstance(var, dict):
            continue
        kind, default = var.get("type"), var.get("defaultValue")
        if default is not None:
            wrong = (
                (kind == "numeric" and (isinstance(default, bool) or not isinstance(default, (int, float))))
                or (kind == "boolean" and not isinstance(default, bool))
                or (kind in ("text", "textarea") and not isinstance(default, str))
            )
            if wrong:
                report.warn(doc, [name, "defaultValue"], f"defaultValue {default!r} does not fit type '{kind}'")
            values = var.get("valueList")
            if isinstance(values, list) and values and default not in values:
                report.warn(doc, [name, "defaultValue"], "defaultValue is not one of valueList")
            if kind == "numeric" and isinstance(default, (int, float)) and not isinstance(default, bool):
                if isinstance(var.get("min"), (int, float)) and default < var["min"]:
                    report.warn(doc, [name, "defaultValue"], "defaultValue is below min")
                if isinstance(var.get("max"), (int, float)) and default > var["max"]:
                    report.warn(doc, [name, "defaultValue"], "defaultValue is above max")
        if name in ("DEVICE_NAME", "DEVICE_SERIAL_NUMBER", "SWARM_KEY", "DEVICE_KEY", "APP_KEY"):
            report.error(doc, [name], f"{name} is set by the platform; choose another name")


def check_port(doc: Doc, report: Report) -> None:
    ports = (doc.data or {}).get("ports") if isinstance(doc.data, dict) else None
    if not isinstance(ports, list):
        return
    mains = [i for i, p in enumerate(ports) if isinstance(p, dict) and p.get("main")]
    for i in mains[1:]:
        report.error(doc, ["ports", i, "main"], "only one port can be main: true")
    seen = {}
    for i, p in enumerate(ports):
        if not isinstance(p, dict):
            continue
        if p.get("port") in seen:
            report.error(doc, ["ports", i, "port"], f"port {p.get('port')} is declared twice")
        seen[p.get("port")] = i
        if p.get("remote_port_environment") and p.get("protocol", "http") not in ("tcp", "udp"):
            report.warn(
                doc,
                ["ports", i, "remote_port_environment"],
                "remote_port_environment is only set for tcp and udp ports",
            )


def check_ai(doc: Doc, report: Report, data: DataModel) -> None:
    if not isinstance(doc.data, dict):
        return
    agents = {k: v for k, v in doc.data.items() if isinstance(v, dict)}
    mains = [k for k, v in agents.items() if v.get("main")]
    if agents and not mains:
        report.error(doc, [], "no agent has main: true, so the IronFlock assistant cannot reach any of them")
    elif len(mains) > 1:
        report.warn(doc, [mains[1], "main"], "more than one main agent; normally exactly one is main")
    for name, agent in agents.items():
        tools = agent.get("tools") if isinstance(agent.get("tools"), dict) else {}
        for tname, tool in tools.items():
            if not isinstance(tool, dict):
                continue
            tpath = [name, "tools", tname]
            if ("topic" in tool) == ("delegate" in tool):
                report.error(doc, tpath, "a tool needs exactly one of topic or delegate")
            target = tool.get("delegate")
            if target is not None and target not in agents:
                report.error(doc, tpath + ["delegate"], f"no agent named '{target}' in this file")
            elif target == name:
                report.error(doc, tpath + ["delegate"], "an agent cannot delegate to itself")
        prompt, budget = agent.get("system_prompt"), agent.get("max_context_tokens")
        if isinstance(prompt, str) and isinstance(budget, int) and len(prompt) / 4 > budget / 2:
            report.warn(doc, [name, "system_prompt"], "system_prompt uses over half of max_context_tokens")
        if agent.get("data_access") and not data.present:
            report.warn(doc, [name, "data_access"], "data_access is on but there is no data-template.yml")


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def find_dir(arg: str) -> Path:
    path = Path(arg).resolve()
    if path.name == ".ironflock" and path.is_dir():
        return path
    return path / ".ironflock"


def run(folder: Path, offline: bool) -> Report:
    report = Report()
    docs: dict[str, Doc] = {}
    for name in TEMPLATES:
        file = folder / f"{name}.yml"
        if not file.exists():
            if (folder / f"{name}.yaml").exists():
                report.issues.append(
                    Issue("ERROR", f"{name}.yaml", None, [], f"rename to {name}.yml; the platform reads only .yml")
                )
            continue
        doc = load(file, report)
        if doc is None:
            continue
        if doc.data is None:
            report.error(doc, [], "file is empty")
            continue
        check_schema(name, doc, report)
        docs[name] = doc

    data = check_data(docs["data-template"], report) if "data-template" in docs else DataModel()
    if "env-template" in docs:
        check_env(docs["env-template"], report)
    if "port-template" in docs:
        check_port(docs["port-template"], report)
    if "ai-template" in docs:
        check_ai(docs["ai-template"], report, data)
    if "board-template" in docs:
        import board_checks

        board_checks.check_board(docs["board-template"], report, data, offline)
    if not docs and not report.issues:
        report.issues.append(Issue("WARN", str(folder), None, [], "no templates found"))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("app_dir", nargs="?", default=".", help="app folder or its .ironflock folder")
    parser.add_argument("--offline", action="store_true", help="skip checks that fetch widget schemas")
    args = parser.parse_args()

    folder = find_dir(args.app_dir)
    if not folder.is_dir():
        print(f"error: {folder} does not exist", file=sys.stderr)
        return 2
    report = run(folder, args.offline)
    order = {"ERROR": 0, "WARN": 1}
    for issue in sorted(report.issues, key=lambda i: (i.file, order[i.level], i.line or 0)):
        where = f"{issue.file}:{issue.line}" if issue.line else issue.file
        print(f"{issue.level:5} {where} {fmt_path(issue.path)}: {issue.message}")
    errors = sum(1 for i in report.issues if i.level == "ERROR")
    warnings = len(report.issues) - errors
    print(f"\n{errors} error(s), {warnings} warning(s) in {folder}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
