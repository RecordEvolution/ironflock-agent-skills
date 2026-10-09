"""Board-template checks for validate.py.

The board schema (schemas/board-template/v1.yml) checks structure. This module
checks what a structural schema cannot see:

- each widget's chartconfig against the definition schema of the widget version
  it pins (unknown properties, value types, enums, where data bindings may go,
  the shape of a tableRef's format),
- every data binding against data-template.yml (tables, columns, transforms,
  consumed apps) and the query rules fleetdb applies (time window, aggregation),
- the layout: ids, pages, positions, screen coverage, navigation routes, and
  filter widgets wired to the widgets they are meant to filter.

The rules follow what the board actually does when it renders a widget and
queries its data, not the stricter or looser habits of other validators.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass, field
from typing import Any

import widget as widget_lib

PREFIXES = ("$str:", "$num:", "$bool:")
SCALAR_TYPES = {"string", "number", "integer", "boolean", "color", "textarea", "image", "actionApp", "actionDevice"}
TEXT_TYPES = {"string", "color", "textarea", "image"}
NUMERIC = {"numeric", "bigint"}
ORDERABLE = {"numeric", "bigint", "string", "timestamp"}
PATTERN_OPERATORS = {"LIKE", "ILIKE", "NOT LIKE", "NOT ILIKE"}
WINDOW_KEYS = ("windowPeriod", "windowLength", "nowMinusPeriods")
NAV_WIDGETS = {"widget-navbar", "widget-sidenav", "widget-navbutton"}
SYSTEM_TABLES = {
    "error-logs": {
        "tsp": "timestamp",
        "msg": "string",
        "source": "string",
        "level": "string",
        "device_key": "bigint",
        "user_message": "string",
    }
}
DEFAULT_COLUMNS, DEFAULT_ROWS = 96, 64
COVERAGE_TARGET = 0.9


# --------------------------------------------------------------------------
# Literal values
# --------------------------------------------------------------------------


def decode(value: str) -> tuple[str | None, Any]:
    """Decode a constant the way the renderer does (WidgetDataEngine.parseConst):
    the FIRST prefix found anywhere in the string wins, and the value is the text
    between it and any second occurrence."""
    for prefix in PREFIXES:
        if prefix in value:
            raw = value.split(prefix)[1]
            kind = prefix[1:-1]
            if kind == "num":
                return kind, js_number(raw)
            if kind == "bool":
                return kind, raw.lower() == "true"
            return kind, raw
    return None, value


def js_number(raw: str) -> float:
    text = raw.strip()
    if text == "":
        return 0.0
    try:
        if re.fullmatch(r"[+-]?0[xX][0-9a-fA-F]+", text):
            return float(int(text, 16))
        if re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", text):
            return float(text)
    except ValueError:
        pass
    return math.nan


def same(a: Any, b: Any) -> bool:
    """JavaScript ===, which never equates true with 1 the way Python does."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    return type(a) is type(b) and a == b


def format_roles(fmt: Any) -> dict:
    """Aggregation roles the renderer derives from a format: x and pivot group,
    every other column leaf is a value to aggregate."""
    roles: dict = {"x": None, "pivot": [], "metrics": []}

    def walk(value: Any, key: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)
        elif isinstance(value, str) and value and decode(value)[0] is None:
            if key == "x":
                roles["x"] = value
            elif key == "pivot":
                roles["pivot"].append(value)
            else:
                roles["metrics"].append(value)

    walk(fmt, None)
    return roles


def has_binding(value: Any) -> bool:
    if isinstance(value, dict):
        return is_ref(value) or any(has_binding(v) for v in value.values())
    if isinstance(value, list):
        return any(has_binding(v) for v in value)
    return False


def is_ref(value: Any) -> bool:
    return isinstance(value, dict) and ("valueRef" in value or "tableRef" in value)


def schema_type(node: dict) -> str | None:
    t = node.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    if t is None and isinstance(node.get("properties"), dict):
        return "object"
    if t is None and isinstance(node.get("items"), dict):
        return "array"
    return t


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------


@dataclass
class Board:
    doc: Any
    report: Any
    data: Any
    filter_params_used: dict[str, list] = field(default_factory=dict)
    time_params_used: dict[str, list] = field(default_factory=dict)
    filter_params_offered: dict[str, list] = field(default_factory=dict)
    nav_routes: list[tuple[list, str, str]] = field(default_factory=list)  # (path, route, layout_id)

    def error(self, path: list, message: str) -> None:
        self.report.error(self.doc, path, message)

    def warn(self, path: list, message: str) -> None:
        self.report.warn(self.doc, path, message)

    def columns_of(self, table: str, provider: str | None) -> dict[str, str] | None:
        """Column -> dataType for a table this board may read, or None when the
        columns cannot be known here (another app's table)."""
        if provider:
            return None
        if table in SYSTEM_TABLES:
            return SYSTEM_TABLES[table]
        return self.data.tables.get(table)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def check_board(doc, report, data, offline: bool) -> None:
    board = Board(doc, report, data)
    tpl = doc.data if isinstance(doc.data, dict) else {}
    widgets = tpl.get("widgets") if isinstance(tpl.get("widgets"), list) else []
    layout = tpl.get("layout") if isinstance(tpl.get("layout"), dict) else None

    name = tpl.get("name")
    if isinstance(name, str) and len(name.strip()) > 30:
        board.warn(["name"], "long board names can exceed the platform's 40-character limit once it adds the board key")

    if not data.present and any(has_binding(w.get("chartconfig")) for w in widgets if isinstance(w, dict)):
        board.warn([], "no data-template.yml next to this board, so table and column names cannot be checked")

    ids: dict[str, int] = {}
    for i, w in enumerate(widgets):
        if not isinstance(w, dict):
            continue
        lid = w.get("layout_id")
        if isinstance(lid, str):
            if lid in ids:
                board.error(["widgets", i, "layout_id"], f"layout_id '{lid}' is already used by widgets[{ids[lid]}]")
            ids.setdefault(lid, i)

    placements = check_layout(board, layout, ids, tpl.get("style") or {}) if layout else {}

    schema_cache: dict[tuple[str, str | None], tuple[str | None, dict | None, str | None]] = {}
    for i, w in enumerate(widgets):
        if isinstance(w, dict):
            check_widget(board, i, w, offline, schema_cache, placements)

    check_routes(board, layout, placements)
    check_filter_wiring(board)
    if offline:
        board.warn([], "--offline: widget configs were not checked against their definition schemas")


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


def iter_pages(page: dict, route: list[str], path: list):
    yield route, page, path
    subs = page.get("subRoutes")
    if isinstance(subs, dict):
        for name, child in subs.items():
            if isinstance(child, dict):
                yield from iter_pages(child, route + [str(name)], path + ["subRoutes", name])


def check_layout(board: Board, layout: dict, widget_ids: dict[str, int], style: dict) -> dict[str, list[list[str]]]:
    """Returns layout_id -> routes of the pages it is placed on."""
    placements: dict[str, list[list[str]]] = {}
    orphans: dict[str, list] = {}  # layout_id without a widget -> paths of its slots
    columns = style.get("columns") if isinstance(style.get("columns"), (int, float)) else DEFAULT_COLUMNS
    rows = style.get("rows") if isinstance(style.get("rows"), (int, float)) else DEFAULT_ROWS
    snapped = style.get("gridSnap") is not False
    overflow_y = bool(style.get("overflow"))
    overflow_x = bool(style.get("overflowX"))
    multi_page = bool(layout.get("subRoutes"))

    for route, page, path in iter_pages(layout, [], ["layout"]):
        label = "/" + "/".join(route)
        grid = page.get("gridLayout") if isinstance(page.get("gridLayout"), list) else []
        seen: set[str] = set()
        boxes = []
        has_nav = False
        has_aspect = False
        for j, pos in enumerate(grid):
            if not isinstance(pos, dict):
                continue
            ppath = path + ["gridLayout", j]
            pid = pos.get("id")
            if not isinstance(pid, str):
                continue
            if pid in seen:
                board.error(ppath + ["id"], f"'{pid}' is placed twice on page {label}")
            seen.add(pid)
            if pid not in widget_ids:
                orphans.setdefault(pid, []).append(ppath + ["id"])
                continue  # an empty slot renders nothing: no geometry to judge
            placements.setdefault(pid, []).append(route)
            if pos.get("aspectRatio"):
                has_aspect = True
            if all(isinstance(pos.get(k), (int, float)) for k in "xywh"):
                x, y, w, h = (pos[k] for k in "xywh")
                if snapped and not overflow_x and x + w > columns:
                    board.warn(ppath, f"reaches column {x + w:g} but the grid has {columns:g}; the widget is cut off")
                if snapped and not overflow_y and y + h > rows:
                    board.warn(ppath, f"reaches row {y + h:g} but the grid has {rows:g}; the widget is cut off")
                boxes.append((x, y, w, h, ppath))
        board_widgets = board.doc.data.get("widgets") or []
        for pid in seen:
            idx = widget_ids.get(pid)
            if idx is not None and isinstance(board_widgets[idx], dict):
                if board_widgets[idx].get("package_name") in NAV_WIDGETS:
                    has_nav = True

        if not grid:
            board.warn(path + ["gridLayout"], f"page {label} has no widgets")
            continue
        if snapped and not overflow_y and not overflow_x and not has_aspect and columns * rows <= 1_000_000:
            coverage = covered_fraction(boxes, int(columns), int(rows))
            if coverage < COVERAGE_TARGET:
                board.warn(
                    path + ["gridLayout"],
                    f"widgets cover {coverage:.0%} of page {label} ({columns:g}x{rows:g} grid); without "
                    "overflow the grid is scaled onto the whole screen, so the rest shows as empty space",
                )
        if multi_page and route and not has_nav:
            board.warn(path + ["gridLayout"], f"page {label} has no navigation widget, so users cannot leave it from the board")

    for pid, paths in orphans.items():
        where = f"{len(paths)} page slots refer" if len(paths) > 1 else "a page slot refers"
        board.warn(paths[0], f"{where} to layout_id '{pid}', but no widget has it, so it renders nothing")
    for lid, idx in widget_ids.items():
        if lid not in placements:
            board.warn(["widgets", idx, "layout_id"], f"widget '{lid}' is not placed on any page, so it is never shown")
    return placements


def covered_fraction(boxes: list, columns: int, rows: int) -> float:
    covered = [[False] * columns for _ in range(rows)]
    for x, y, w, h, _ in boxes:
        for r in range(max(0, math.floor(y)), min(rows, math.ceil(y + h))):
            row = covered[r]
            for c in range(max(0, math.floor(x)), min(columns, math.ceil(x + w))):
                row[c] = True
    return sum(map(sum, covered)) / float(columns * rows)


# --------------------------------------------------------------------------
# Widgets
# --------------------------------------------------------------------------


def check_widget(board: Board, i: int, w: dict, offline: bool, cache: dict, placements: dict) -> None:
    wpath = ["widgets", i]
    package = w.get("package_name")
    config, cpath = w.get("chartconfig"), wpath + ["chartconfig"]
    version = w.get("version")
    if config is None and isinstance(w.get("widget_config"), dict):
        board.warn(wpath + ["widget_config"], "legacy widget_config wrapper; write chartconfig, custom_style and version directly on the widget")
        config, cpath = w["widget_config"].get("chartconfig"), wpath + ["widget_config", "chartconfig"]
        version = version or w["widget_config"].get("version")
    if not isinstance(package, str):
        return

    schema = None
    if not offline:
        key = (package, version if isinstance(version, str) else None)
        if key not in cache:
            try:
                resolved = key[1] or widget_lib.latest_version(package)
                cache[key] = (resolved, widget_lib.widget_file(package, resolved, "definition-schema.json"), None)
            except widget_lib.FetchError as exc:
                cache[key] = (None, None, str(exc))
        resolved, schema, problem = cache[key]
        if problem:
            if key[1] and "404" in problem:
                board.error(wpath + ["version"], f"{package}@{key[1]} is not published; the board cannot load it")
            elif "404" in problem:
                board.error(wpath + ["package_name"], f"there is no widget package '{package}'")
            else:
                board.warn(wpath, f"could not fetch the schema of {package}: {problem}")

    label = f"{package}@{cache.get((package, version if isinstance(version, str) else None), (None,))[0] or '?'}"
    walker = Walker(board, package, label, schema, w.get("layout_id"), placements)
    if isinstance(config, dict):
        if schema is not None:
            walker.check_object(config, schema, cpath, [config])
        else:
            walker.check_refs_only(config, cpath)
    if package == "widget-form" and isinstance(config, dict):
        check_form_targets(board, config, cpath)


class Walker:
    def __init__(self, board: Board, package: str, label: str, schema: dict | None, layout_id: Any, placements: dict):
        self.board = board
        self.package = package
        self.label = label
        self.schema = schema
        self.layout_id = layout_id
        self.placements = placements

    # -- chartconfig against the widget's definition schema ------------------

    def check_object(self, obj: dict, node: dict, path: list, stack: list) -> None:
        props = node.get("properties") if isinstance(node.get("properties"), dict) else None
        if props is None:
            return  # free-form object (e.g. a font): nothing to check against
        for key, value in obj.items():
            ppath = path + [key]
            if key not in props:
                hint = difflib.get_close_matches(str(key), list(props), n=1)
                more = f" (did you mean '{hint[0]}'?)" if hint else ""
                self.board.error(ppath, f"{self.label} has no property '{key}' here{more}; the widget ignores it")
                continue
            child = props[key] if isinstance(props[key], dict) else {}
            self.check_condition(key, value, child, ppath, stack)
            self.check_value(value, child, ppath, stack + [value] if isinstance(value, (dict, list)) else stack)

    def check_value(self, value: Any, node: dict, path: list, stack: list) -> None:
        t = schema_type(node)
        if is_ref(value):
            self.check_binding(value, node, t, path)
            return
        if value is None:
            return
        if isinstance(value, dict):
            if t in SCALAR_TYPES or t == "array":
                self.board.error(path, f"expected {t}, found an object")
            elif t == "object" or node.get("properties"):
                self.check_object(value, node, path, stack)
            return
        if isinstance(value, list):
            if t != "array":
                if t is not None:
                    self.board.error(path, f"expected {t}, found a list")
                return
            items = node.get("items") if isinstance(node.get("items"), dict) else {}
            for k, item in enumerate(value):
                self.check_value(item, items, path + [k], stack + [item] if isinstance(item, (dict, list)) else stack)
            return
        self.check_literal(value, node, t, path)

    def check_literal(self, value: Any, node: dict, t: str | None, path: list) -> None:
        if t in ("array", "object"):
            self.board.error(path, f"expected {t}, found {value!r}")
            return
        kind, decoded = decode(value) if isinstance(value, str) else (None, value)
        if isinstance(value, str) and kind is not None:
            prefix = f"${kind}:"
            if not value.startswith(prefix):
                self.board.warn(path, f"'{prefix}' is decoded wherever it appears, so the text before it is dropped")
        if t in ("number", "integer"):
            if kind == "num" and math.isnan(decoded):
                self.board.error(path, f"{value!r} is not a number")
            elif kind in ("str", "bool") or (kind is None and isinstance(value, str)):
                self.board.warn(path, f"number property holds {value!r}; write $num:<value>")
            elif value == 0 and not isinstance(value, bool) and kind is None:
                self.board.warn(path, "write $num:0; the board editor replaces a raw 0 with the default when it saves")
        elif t == "boolean":
            if kind == "bool" and isinstance(value, str) and value.split("$bool:")[1].lower() not in ("true", "false"):
                self.board.warn(path, f"{value!r} decodes to false; only $bool:true is true")
            elif kind in ("str", "num") or (kind is None and isinstance(value, str)):
                self.board.warn(path, f"boolean property holds {value!r}; write $bool:true or $bool:false")
            elif value is False:
                self.board.warn(path, "write $bool:false; the board editor replaces a raw false with the default when it saves")
        elif t in TEXT_TYPES:
            if kind in ("num", "bool") or isinstance(value, bool) or (isinstance(value, (int, float)) and t != "image"):
                self.board.warn(path, f"text property holds {value!r}; write $str:<text>")
        elif t in ("actionApp", "actionDevice"):
            ok = (kind == "num" and not math.isnan(decoded) and float(decoded).is_integer()) or (
                isinstance(value, int) and not isinstance(value, bool)
            )
            if not ok:
                self.board.error(path, f"{t} must be $num:<key> (a numeric key picked in the board editor)")
        enum = node.get("enum")
        # '' is how the editor stores a cleared field, which means "use the default"
        if isinstance(enum, list) and enum and decoded != "" and not any(same(decoded, e) for e in enum):
            self.board.error(path, f"{decoded!r} is not one of {enum}")

    def check_condition(self, key: str, value: Any, node: dict, path: list, stack: list) -> None:
        cond = node.get("condition")
        if not isinstance(cond, dict) or not cond.get("relativePath") or value is None:
            return
        allowed = cond.get("showIfValueIn")
        if not isinstance(allowed, list):
            return
        # stack[-1] is the object holding the property; each `..` steps out one
        # level starting from the property itself, so k dots land on stack[-k].
        parts = [p for p in str(cond["relativePath"]).split("/") if p not in ("", ".")]
        dots = 0
        while parts and parts[0] == "..":
            parts.pop(0)
            dots += 1
        if dots == 0 or dots > len(stack):
            return
        cursor: Any = stack[len(stack) - dots]
        for part in parts:
            if not isinstance(cursor, dict):
                return
            cursor = cursor.get(part)
        if is_ref(cursor):
            return
        source = decode(cursor)[1] if isinstance(cursor, str) else (False if cursor is None else cursor)
        if not any(same(source, a) for a in allowed):
            self.board.warn(
                path,
                f"'{key}' is only used when {cond['relativePath']} is one of {allowed}; it is {source!r} here, "
                "so the board editor drops this setting",
            )

    # -- data bindings --------------------------------------------------------

    def check_binding(self, value: dict, node: dict, t: str | None, path: list) -> None:
        if "valueRef" in value and "tableRef" in value:
            self.board.error(path, "a binding holds either valueRef or tableRef, not both")
            return
        if node.get("dataDrivenDisabled"):
            self.board.error(path, "this property cannot be bound to data (dataDrivenDisabled); write a literal value")
        if "tableRef" in value:
            ref = value["tableRef"] if isinstance(value["tableRef"], dict) else {}
            rpath = path + ["tableRef"]
            if t is not None and t != "array":
                where = "an object: bind its fields instead" if t == "object" else f"a single {t}: use valueRef"
                self.board.error(path, f"tableRef replaces a list, but this property is {where}")
            columns = self.check_ref_common(ref, rpath, kind="tableRef")
            items = node.get("items") if t == "array" and isinstance(node.get("items"), dict) else None
            fmt = ref.get("format")
            if fmt is not None:
                self.check_format(fmt, items, rpath + ["format"], columns, ref.get("tablename"), top=True)
            self.check_series(ref, rpath, columns, kind="tableRef", roles=format_roles(fmt))
        else:
            ref = value["valueRef"] if isinstance(value["valueRef"], dict) else {}
            rpath = path + ["valueRef"]
            if t == "array":
                self.board.error(path, "valueRef gives one value, but this property is a list: use tableRef")
            elif t == "object":
                self.board.error(path, "valueRef gives one value, but this property is an object: bind its fields instead")
            columns = self.check_ref_common(ref, rpath, kind="valueRef")
            column = ref.get("column")
            if isinstance(column, str) and columns is not None and "{" not in column:
                self.check_column(column, columns, ref.get("tablename"), rpath + ["column"])
            self.check_series(ref, rpath, columns, kind="valueRef", roles={"x": None, "pivot": [], "metrics": [column]})

    def check_refs_only(self, obj: Any, path: list) -> None:
        """Without the widget schema, still check every binding's data side."""
        if is_ref(obj):
            self.check_binding(obj, {}, None, path)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                self.check_refs_only(v, path + [k])
        elif isinstance(obj, list):
            for k, v in enumerate(obj):
                self.check_refs_only(v, path + [k])

    def check_ref_common(self, ref: dict, path: list, kind: str) -> dict[str, str] | None:
        board = self.board
        table = ref.get("tablename")
        provider = ref.get("provider_app")
        if "swarm_app_databackend_key" in ref:
            board.warn(path + ["swarm_app_databackend_key"], "remove it: the platform sets the backend key itself")
        qp = ref.get("queryParams") if isinstance(ref.get("queryParams"), dict) else {}
        if "timeRange" in qp:
            board.warn(path + ["queryParams", "timeRange"], "timeRange is derived from the window at runtime; remove it")
        if provider:
            consumes = board.data.consumes
            if board.data.present and provider.lower() not in consumes and "*" not in consumes:
                board.error(path + ["provider_app"], f"reads app '{provider}', which data-template.yml does not list under consumes")
        columns = None
        if isinstance(table, str) and not provider:
            columns = board.columns_of(table, None)
            if columns is None and board.data.present:
                known = sorted(set(board.data.tables) | set(SYSTEM_TABLES))
                hint = difflib.get_close_matches(table, known, n=1)
                more = f" (did you mean '{hint[0]}'?)" if hint else ""
                board.error(path + ["tablename"], f"there is no table or transform '{table}' in data-template.yml{more}")
        is_transform = isinstance(table, str) and table in board.data.transforms and not provider
        self.check_query(qp, path + ["queryParams"], columns, is_transform, kind, table)
        return columns

    def check_column(self, column: str, columns: dict[str, str], table: Any, path: list) -> None:
        base = re.split(r"[.\[]", column, maxsplit=1)[0]
        if base and base not in columns:
            hint = difflib.get_close_matches(base, list(columns), n=1)
            more = f" (did you mean '{hint[0]}'?)" if hint else ""
            self.board.error(path, f"table '{table}' has no column '{base}'{more}; the query fails and the widget stays empty")

    def check_query(self, qp: dict, path: list, columns: dict | None, is_transform: bool, kind: str, table: Any) -> None:
        board = self.board
        window = [qp.get(k) for k in WINDOW_KEYS]
        complete = qp.get("windowPeriod") and qp.get("windowLength") and qp.get("nowMinusPeriods") is not None
        if any(v is not None for v in window) and not complete:
            board.warn(path, "the time window applies only when windowPeriod, windowLength and nowMinusPeriods are all set")
        for key in ("timeStartParam", "timeEndParam"):
            if qp.get(key) and not complete:
                board.warn(path + [key], f"{key} only takes effect together with a complete time window")
            if isinstance(qp.get(key), str) and qp.get(key):
                board.time_params_used.setdefault(qp[key], []).append(path + [key])
        if is_transform:
            dead = [k for k in (*WINDOW_KEYS, "timeStartParam", "timeEndParam", "series") if qp.get(k) is not None]
            if dead:
                board.error(path + [dead[0]], f"{', '.join(dead)}: no effect on a transform; only limit, offset and filterAnd apply")
        filters = qp.get("filterAnd") if isinstance(qp.get("filterAnd"), list) else []
        for k, f in enumerate(filters):
            self.check_filter(f, path + ["filterAnd", k], columns, is_transform, bool(qp.get("series")), table)

    def check_filter(self, f: Any, path: list, columns: dict | None, is_transform: bool, aggregated: bool, table: Any = None) -> None:
        board = self.board
        if not isinstance(f, dict):
            return
        if isinstance(f.get("filters"), list):
            for k, sub in enumerate(f["filters"]):
                self.check_filter(sub, path + ["filters", k], columns, is_transform, aggregated, table)
            return
        if f.get("latest"):
            if is_transform:
                board.error(path, "the latest filter does not work on a transform")
            elif aggregated:
                board.warn(path, "the latest filter is ignored when the binding aggregates (series)")
            return
        column, op, value = f.get("column"), f.get("operator"), f.get("value")
        if column == "latest_flag":
            board.warn(path, "legacy latest_flag filter (still migrated by the platform); write {latest: true} instead")
            return
        if isinstance(column, str) and columns is not None and "{" not in column:
            self.check_column(column, columns, table, path + ["column"])
            base = re.split(r"[.\[]", column, maxsplit=1)[0]
            actual, declared = columns.get(base), f.get("dataType")
            if actual and declared and actual != declared and actual != "json" and {actual, declared} != {"numeric", "bigint"}:
                board.warn(path + ["dataType"], f"column '{base}' is {actual}, but the filter says {declared}")
            if op in PATTERN_OPERATORS and actual and actual != "string":
                board.warn(path + ["operator"], f"{op} only works on text columns; it is dropped for {actual}")
        if f.get("useFilter") and f.get("useRoute"):
            board.error(path, "use either useFilter or useRoute, not both")
        if f.get("useRoute"):
            if not (isinstance(value, int) or (isinstance(value, str) and value.isdigit())):
                board.error(path + ["value"], "with useRoute, value is the index of a route segment, e.g. '2'")
            else:
                pages = self.placements.get(self.layout_id) or []
                idx = int(value)
                if pages and all(idx > len(r) for r in pages):
                    board.warn(path + ["value"], f"segment {idx} does not exist on the pages this widget is on")
        elif f.get("useFilter"):
            if isinstance(value, str) and value:
                board.filter_params_used.setdefault(value, []).append(path + ["value"])
            else:
                board.error(path + ["value"], "with useFilter, value is the name of the URL parameter to read")
        elif op in ("IS NULL", "IS NOT NULL"):
            if value not in (None, ""):
                board.warn(path + ["value"], f"{op} takes no value")
        elif op in ("IN", "NOT IN"):
            if not isinstance(value, (list, str)):
                board.error(path + ["value"], f"{op} needs a list of values")

    def check_format(self, fmt: Any, node: dict | None, path: list, columns: dict | None, table: Any, top: bool) -> None:
        board = self.board
        t = schema_type(node) if node else None
        if node is not None and top:
            if t == "array" and not isinstance(fmt, list):
                board.error(path, "the bound items are lists, so format must be a list (one entry per element)")
                return
            if t in ("object", None) and node.get("properties") and not isinstance(fmt, dict):
                board.error(path, "the bound items are objects, so format must be an object")
                return
        if isinstance(fmt, dict):
            props = node.get("properties") if node and isinstance(node.get("properties"), dict) else None
            for key, leaf in fmt.items():
                lpath = path + [key]
                child = None
                if props is not None:
                    if key not in props:
                        hint = difflib.get_close_matches(str(key), list(props), n=1)
                        more = f" (did you mean '{hint[0]}'?)" if hint else ""
                        board.error(lpath, f"{self.label}: the items here have no property '{key}'{more}")
                        continue
                    child = props[key] if isinstance(props[key], dict) else {}
                    if top and child.get("dataDrivenDisabled") and isinstance(leaf, str) and decode(leaf)[0] is None:
                        board.warn(lpath, f"'{key}' is a fixed setting (dataDrivenDisabled); give it a $-prefixed constant")
                self.check_format_leaf(leaf, child, lpath, columns, table)
        elif isinstance(fmt, list):
            items = node.get("items") if node and isinstance(node.get("items"), dict) else None
            for k, leaf in enumerate(fmt):
                self.check_format_leaf(leaf, items, path + [k], columns, table)

    def check_format_leaf(self, leaf: Any, node: dict | None, path: list, columns: dict | None, table: Any) -> None:
        if isinstance(leaf, (dict, list)):
            self.check_format(leaf, node, path, columns, table, top=False)
            return
        if not isinstance(leaf, str):
            return  # raw numbers/booleans are rejected by the board schema
        kind, decoded = decode(leaf)
        if kind is None:
            if leaf == "":
                self.board.warn(path, "an empty string reads no column; use '$str:' for an empty constant")
            elif columns is not None and "{" not in leaf:
                self.check_column(leaf, columns, table, path)
            return
        if node:
            self.check_literal(leaf, node, schema_type(node), path)

    def check_series(self, ref: dict, path: list, columns: dict | None, kind: str, roles: dict) -> None:
        board = self.board
        qp = ref.get("queryParams") if isinstance(ref.get("queryParams"), dict) else {}
        series = qp.get("series")
        if not isinstance(series, dict):
            return
        spath = path + ["queryParams", "series"]
        method = series.get("method")
        complete = qp.get("windowPeriod") and qp.get("windowLength") and qp.get("nowMinusPeriods") is not None
        if not complete:
            board.error(spath, "aggregation needs a complete time window (windowPeriod, windowLength, nowMinusPeriods); without one the widget gets raw rows")
        bucket = series.get("bucket")
        if kind == "valueRef":
            if bucket:
                board.error(spath + ["bucket"], "a valueRef aggregates over the whole window; remove bucket")
            if qp.get("limit") != 1:
                board.error(path + ["queryParams", "limit"], "an aggregated valueRef needs limit: 1")
        else:
            x = roles.get("x")
            if bucket and x is not None and x != "tsp":
                board.error(spath + ["bucket"], f"x is the category column '{x}', which has no time buckets; remove bucket or map x to tsp")
            if bucket and x is None:
                board.error(spath + ["bucket"], "fixed buckets need x mapped to tsp")
            if x is None and not roles.get("pivot"):
                board.warn(spath, "nothing to group by (no x and no pivot), so the platform returns raw rows instead")
        metrics = [m for m in roles.get("metrics") or [] if isinstance(m, str)]
        if kind == "tableRef" and not metrics:
            board.error(spath, "no value column to aggregate (map y or another value field to a column)")
        for metric in metrics:
            base = re.split(r"[.\[]", metric, maxsplit=1)[0]
            dtype = (columns or {}).get(base)
            if base == "tsp" and method != "COUNT":
                board.error(spath + ["method"], "tsp can only be counted (method COUNT)")
            elif dtype and method in ("AVG", "SUM") and dtype not in NUMERIC and dtype != "json":
                board.error(spath + ["method"], f"{method} needs a numeric column; '{base}' is {dtype}")
            elif dtype and method in ("MIN", "MAX") and dtype not in ORDERABLE and dtype != "json":
                board.error(spath + ["method"], f"{method} does not work on {dtype} column '{base}'")
            if isinstance(method, str) and len(f"{method}:{metric}".encode()) > 63:
                board.error(spath, f"'{method}:{metric}' is longer than 63 bytes; shorten the column path")


# --------------------------------------------------------------------------
# Forms, routes, filters
# --------------------------------------------------------------------------


def check_form_targets(board: Board, config: dict, path: list) -> None:
    fields = config.get("formFields") if isinstance(config.get("formFields"), list) else []
    targets = [(path + ["formFields", k, "targetColumn"], f.get("targetColumn")) for k, f in enumerate(fields) if isinstance(f, dict)]
    targets.append((path + ["deleteFlagColumn"], config.get("deleteFlagColumn")))
    for tpath, target in targets:
        if not isinstance(target, dict) or not target.get("tablename"):
            continue
        table, column = target.get("tablename"), target.get("column")
        columns = board.columns_of(table, None)
        if columns is None:
            if board.data.present:
                board.error(tpath + ["tablename"], f"the form writes to '{table}', which is not a table in data-template.yml")
            continue
        if table in board.data.transforms:
            board.error(tpath + ["tablename"], f"'{table}' is a transform; forms can only write to tables")
        if column not in columns:
            board.error(tpath + ["column"], f"table '{table}' has no column '{column}'")
        elif target.get("dataType") and columns[column] != target.get("dataType"):
            board.warn(tpath + ["dataType"], f"column '{column}' is {columns[column]}, not {target.get('dataType')}")


def resolve(page: dict, segments: list[str]) -> bool:
    """Walk subRoutes the way the board navigator does: exact segment, else '*'.
    A '*' in the route itself is replaced by the current URL's segment, so it
    may stand for any child page."""
    if not segments:
        return True
    subs = page.get("subRoutes") if isinstance(page.get("subRoutes"), dict) else {}
    seg, rest = segments[0], segments[1:]
    if seg == "*":
        return any(isinstance(child, dict) and resolve(child, rest) for child in subs.values())
    nxt = subs.get(seg) if isinstance(subs.get(seg), dict) else subs.get("*")
    return isinstance(nxt, dict) and resolve(nxt, rest)


def collect_routes(config: Any, path: list, out: list) -> None:
    if isinstance(config, dict) and not is_ref(config):
        for key, value in config.items():
            if key in ("route", "deleteNavigationRoute") and isinstance(value, str):
                out.append((path + [key], value))
            elif key == "navItems" and isinstance(value, list):
                for k, item in enumerate(value):
                    collect_routes(item, path + [key, k], out)


def check_routes(board: Board, layout: dict | None, placements: dict) -> None:
    if not isinstance(layout, dict):
        return
    for i, w in enumerate(board.doc.data.get("widgets") or []):
        if not isinstance(w, dict) or w.get("package_name") not in (*NAV_WIDGETS, "widget-form"):
            continue
        routes: list = []
        collect_routes(w.get("chartconfig"), ["widgets", i, "chartconfig"], routes)
        for rpath, raw in routes:
            kind, route = decode(raw)
            if kind not in (None, "str") or "{{" in route or route in ("", "/"):
                continue
            route = re.split(r"[?#]", route, maxsplit=1)[0]
            target = [s for s in route.strip("/").split("/") if s]
            if route.startswith("/"):
                candidates = [target]
            else:
                pages = placements.get(w.get("layout_id")) or [[]]
                candidates = [p[:-1] + target if p else target for p in pages]
            if not any(resolve(layout, c) for c in candidates):
                board.warn(rpath, f"route '{route}' matches no page; clicking it lands on the board's home page")


def check_filter_wiring(board: Board) -> None:
    for i, w in enumerate(board.doc.data.get("widgets") or []):
        if not isinstance(w, dict) or not isinstance(w.get("chartconfig"), dict):
            continue
        config = w["chartconfig"]
        keys = []
        if w.get("package_name") == "widget-filter-dropdown":
            keys = [("parameterKey", "filter")]
        elif w.get("package_name") == "widget-filter-calendar":
            keys = [("startKey", "time"), ("endKey", "time")]
        for key, kind in keys:
            raw = config.get(key)
            if not isinstance(raw, str):
                continue
            name = decode(raw)[1]
            if not name:
                continue
            used = board.filter_params_used if kind == "filter" else board.time_params_used
            if name not in used:
                how = (
                    "a filterAnd entry with useFilter: true and value: '{0}'"
                    if kind == "filter"
                    else "queryParams.timeStartParam/timeEndParam: '{0}'"
                ).format(name)
                board.warn(["widgets", i, "chartconfig", key], f"no widget reads '{name}'; give the widgets to filter {how}")
            board.filter_params_offered.setdefault(name, []).append(["widgets", i])
    for name, paths in board.filter_params_used.items():
        if name not in board.filter_params_offered:
            board.warn(paths[0], f"no filter widget on this board sets the URL parameter '{name}'")
