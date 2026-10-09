# board-template.yml

The board is the app's dashboard. The platform installs it into every project the
app is installed in, and binds its widgets to that project's copy of the app's
tables. Developers usually build it in the board editor of the IronFlock studio,
which reads and writes this file. Writing it by hand is fine, and the editor picks
up your version.

Structure is defined by `schemas/board-template/v1.yml`. Each widget's own settings
are defined by its published definition schema (`scripts/widget.py <package>`).

## Contents

1. [Anatomy](#anatomy)
2. [Literal values](#literal-values)
3. [Binding data](#binding-data): valueRef, tableRef, queryParams, filters, aggregation
4. [What a binding can and cannot read](#what-a-binding-can-and-cannot-read)
5. [Pages and navigation](#pages-and-navigation)
6. [Layout](#layout)
7. [Style](#style)
8. [SCADA pages](#scada-pages)
9. [Left to the board editor](#left-to-the-board-editor)
10. [Checklist](#checklist)

[example-board.yml](example-board.yml) is a complete, valid three-page board. It
has navigation, a data-driven machine menu, route filtering, aggregation and a
latest-per-machine table. Read it before writing your first board.

## Anatomy

```yaml
# yaml-language-server: $schema=https://ironflock.com/schemas/board-template/v1.yml
name: machine-monitor                # the app name
widgets:
    - layout_id: temperature         # this widget's id on the board
      package_name: widget-value     # widget type
      version: 1.1.33                # pin the version whose schema you used
      chartconfig:                   # the widget's settings
          title: $str:Temperature
          dataseries:
              - label: $str:Press 1
                unit: $str:°C
                value:
                    valueRef:
                        tablename: measurements
                        column: temperature
                        queryParams:
                            limit: 1
      custom_style:                  # optional: this tile's colours
          tile_background_color: '#e8f5e9'
layout:                              # the home page (route /)
    gridLayout:
        - {id: temperature, x: 0, y: 0, w: 96, h: 64}
style:
    columns: 96
    rows: 64
```

- **`name`**: write the app name. The editor overwrites it with the app name on
  every save anyway. It may only contain letters, digits, spaces, `.`, `_` and `-`,
  and can be at most 40 characters.
- **`layout_id`**: a string that is unique on the board. It appears in URLs, so no
  `/`, `?` or `#`. Use short readable ids such as `temperature-trend`. Quote ids
  that look like numbers (`'1'`), because an unquoted number is not a string and
  the editor would not show the widget.
- **`version`**: set it to the version `widget.py` printed. The board renders
  exactly the pinned version, so it should be the version whose schema you checked
  against. Without a pin, the platform uses the newest version at install time.
- **`chartconfig`**: only properties from the widget's definition schema. Unknown
  keys are silently ignored, so an invented key does nothing.
- **`custom_style`**: `text_color`, `tile_background_color`, `tile_border_color`
  and `tile_border_radius` (e.g. `8px`).

Start the file with the schema line shown above, like the other templates. The
editor rewrites the file when someone saves the board: it sorts widgets by
`layout_id` and pins versions, but keeps comments, including the schema line and
any comment above a widget. Older platform versions dropped comments on save, so
re-add the schema line if it has gone missing.

## Literal values

Write every constant in `chartconfig` with its type prefix, the way the editor
does:

| Value | Write |
|---|---|
| text | `$str:Line 3` |
| empty text | `'$str:'` |
| number | `$num:12.5` |
| boolean | `$bool:true` / `$bool:false` |
| colour | `$str:#4caf50` |

The prefixes matter because:

- **Inside a tableRef `format`, an unprefixed string is a column name.** A constant
  there must carry a prefix. A raw number or boolean in a format makes the whole
  item null.
- **Outside a format, unprefixed values work, but don't survive editing.** When
  someone opens the widget in the editor and saves, a raw `false`, `0` or `''`
  becomes the widget's default.
- **The prefix is found anywhere in a string**, not only at the start. Never put
  `$str:`, `$num:` or `$bool:` inside a text such as a markdown body, because
  everything before the prefix would be cut off.

Long multi-line text such as `markdown: |` can stay unprefixed. To leave a setting
unset, omit the key rather than writing `null`.

## Binding data

A property can hold a binding instead of a literal. The binding object has exactly
one key and replaces the whole value:

```yaml
value:                   # a single value  →  valueRef
    valueRef: {...}
data:                    # a list          →  tableRef
    tableRef: {...}
```

Use `widget.py` to see each property's type. **valueRef** goes where the schema
says string, number, boolean, color and so on. **tableRef** goes where it says
`array`, never as an element inside a list (`[ {tableRef} ]` is wrong). An object
property never takes a binding; bind its fields instead. Properties marked
`dataDrivenDisabled` take no binding at all.

### valueRef: one value

```yaml
valueRef:
    tablename: measurements    # a table or transform from data-template.yml
    column: temperature        # column id, or a json path: payload.sensor.value
    queryParams:
        limit: 1
```

This gives the newest value of the column after the filters are applied.
`queryParams` is optional (it defaults to `limit: 1`), but when present it needs a
`limit`.

### tableRef: a list

```yaml
data:
    tableRef:
        tablename: measurements
        format:                # one array item per row
            x: tsp
            y: temperature
            pivot: machine_id
        queryParams:
            limit: 500         # 1 to 3000; the newest rows are kept
```

- **`format` is the template for one array item.** It is shaped exactly like the
  `items` of that array in the widget's schema
  (`widget.py <pkg> --path dataseries.data`). For an array of objects it is an
  object. For an array of arrays, such as doughnut `sections`, it is a list.
- **Leaves are column ids or json paths**, or prefixed constants such as
  `iconName: $str:settings`. Only properties of the item are allowed.
- **Rows arrive oldest first.**
- **`pivot` splits the rows** into one series, gauge or ring per distinct value.
  For example, `pivot: machine_id` draws one line per machine. Gauges and value
  tiles split only with `multiChart: $bool:true`, and then read `data` instead of
  `value`.
- **`limit` is required.** Charts usually want 500 to 3000; a list of entities as
  many as you expect.

### queryParams

| Key | Meaning |
|---|---|
| `limit` | Maximum rows, 1 to 3000. Always set it. |
| `windowPeriod`, `windowLength`, `nowMinusPeriods` | Rolling time window, e.g. `hour`, `24`, `0` for the last 24 hours up to now. Periods: `second minute hour day week month year`. The window applies only when **all three** are set. |
| `timeStartParam`, `timeEndParam` | Names of URL parameters (a filter-calendar's `startKey`/`endKey`) whose values replace the window's start and end. They need a complete window. |
| `filterAnd` | List of filters, all of which must match. |
| `series` | Aggregation, see below. |

The order of application is: time window, then filters, then aggregation, then
limit. Never write `timeRange`, `columns` or `swarm_app_databackend_key`; the
platform derives or sets them.

### Filters

```yaml
filterAnd:
    - column: machine_id
      operator: '='              # quote it: a bare = breaks some YAML parsers
      value: press-1
      dataType: string           # the column's dataType from data-template.yml
```

- **Operators:** `= != <> > >= < <= LIKE ILIKE NOT LIKE NOT ILIKE IN NOT IN IS NULL
  IS NOT NULL`, in uppercase. `IN` takes a list as its value, and `IS NULL` takes
  no value.
- **`dataType` is required in practice.** Without it, the database does not apply
  the filter.
- **Latest row per entity:** on a table with `maintainLatestFlagFor`, this filter
  keeps only the newest row of each entity:

  ```yaml
  - {latest: true, column: '', operator: '=', value: '', dataType: boolean}
  ```

- **From the URL path:** `useRoute: true`, where `value` is the index of the route
  segment. On `/machines/press-1`, segment `'1'` is `machines` and `'2'` is
  `press-1`. See [Pages and navigation](#pages-and-navigation).
- **From a filter widget:** `useFilter: true`, where `value` is the URL parameter
  name, i.e. a filter-dropdown's `parameterKey`. When the parameter is absent, the
  filter is dropped, so the widget shows everything.
- **OR logic:** `{combinator: OR, filters: [ ... ]}`.

### Aggregation

`series` makes the database compute one value per group instead of returning raw
rows:

```yaml
queryParams:
    limit: 3000
    windowPeriod: day
    windowLength: 7
    nowMinusPeriods: 0
    series:
        method: AVG              # AVG SUM COUNT MIN MAX FIRST LAST
        bucket:                  # optional: a fixed interval; omit for automatic
            length: 1
            period: hour         # second minute hour day week
```

The roles come from the format:

| Format | Result |
|---|---|
| `x: tsp` | A time series. Buckets are automatic (sized to the widget's width) or fixed by `bucket`. |
| `x: <other column>` | One value per category over the whole window. No `bucket`. |
| `pivot: <column>` | Each group aggregated separately, e.g. one line per machine. |
| every other column leaf | Aggregated with the method. |

Rules, which the validator enforces:

- Aggregation needs a complete time window. Without one, raw rows come back
  silently.
- `AVG` and `SUM` need numeric columns. `tsp` can only be counted: `y: tsp` with
  `COUNT` is the number of records.
- On a valueRef, aggregation means one value over the whole window. It needs
  `limit: 1` and no bucket. Example: readings today is
  `column: tsp, series: {method: COUNT}` with a 1-day window.
- There is one method per series. To show average and maximum, add two series.
- Transforms cannot be aggregated (see below).

## What a binding can and cannot read

You can read:

- **The app's own tables:** every column, plus `device_key` (the publishing device)
  and `authid`, which exist on every table.
- **Transforms (SQL views):** only `limit`, `offset` and `filterAnd` work. There is
  no time window, no aggregation and no latest filter, and rows come in the view's
  own order.
- **`error-logs`:** the app's error log, with columns `tsp msg source level
  device_key`.
- **Another app's table:** add `provider_app: <app name in lowercase>` to the ref,
  and list that app under `consumes` in data-template.yml.

There's no arithmetic, ratio, join or derived column in a binding, and no widget
computes those either. When a widget needs one, add a transform to
data-template.yml that computes it in SQL and bind to the transform. The same goes
for "latest value per machine, summed", or anything else that combines rows in a
way the aggregation table above cannot express.

## Pages and navigation

`layout` is a tree of pages:

```yaml
layout:                         # /
    gridLayout: [...]
    subRoutes:
        machines:               # /machines
            gridLayout: [...]
            subRoutes:
                '*':            # /machines/<anything>: one page for every machine
                    gridLayout: [...]
```

- **Page names** use letters, digits, `.`, `_`, `-` and `*`. A `*` page matches any
  segment, and the widgets on it read the segment with `useRoute` filters.
- **Every page needs a `gridLayout`.**
- **A widget is on a page** when that page's `gridLayout` has an entry whose `id`
  is the widget's `layout_id`.
- **Shared widgets:** to show the same widget on several pages (a menu, say), add
  the same id to each page. Give it the same position on every page, because moving
  it in the editor moves it everywhere.

Navigation widgets are widget-sidenav (vertical menu), widget-navbar (horizontal
bar) and widget-navbutton (one button). Their `route` values work like this:

- **`/machines` is absolute**, from the board's home.
- **`press-2`, with no leading slash, replaces the last segment** of the current
  URL. From `/machines/press-1` it goes to `/machines/press-2`. From `/machines`
  (no trailing slash) it would go to `/press-2`. So link a page whose children are
  reached relatively with a trailing slash: `/machines/`.
- **A segment that is exactly `*`** is replaced by the current URL's segment at
  that position, and `{{name}}` by the item's `variables` entry with `label: name`.

A data-driven menu lists one item per row:

```yaml
navItems:
    tableRef:
        tablename: machine_status
        format:
            label: machine_id
            iconName: $str:precision_manufacturing    # Material icon name
            route: machine_id                         # relative: /machines/<id>
        queryParams:
            limit: 50
            filterAnd:
                - {latest: true, column: '', operator: '=', value: '', dataType: boolean}
```

Put this menu on `/machines` and `/machines/*`, and link to it with `/machines/`.
On the `*` page, filter every widget with
`{column: machine_id, operator: '=', value: '2', dataType: string, useRoute: true}`.

On a multi-page board, put a navigation widget on every page. Otherwise users get
stuck.

Filter widgets set URL parameters for other widgets to read:

- **widget-filter-dropdown:** set `parameterKey: $str:machine`. Each widget it
  should filter needs a filter with `useFilter: true, value: machine`.
- **widget-filter-calendar:** set `startKey` and `endKey`. Each widget it should
  filter needs `timeStartParam` and `timeEndParam` with those names, plus a
  complete time window, which serves as the default range.

## Layout

The grid is `style.columns` × `style.rows`, by default 96 × 64. Positions are given
in cells: `x`, `y` from 0, and `w`, `h`. Without `overflow`, the whole grid is
scaled onto the screen, so the grid is the screen.

- **Fill every page completely.** Any cell no widget covers shows as empty screen,
  and users read that as unfinished. Plan bands whose heights add up to `rows` and
  split each band into widths that add up to `columns`. On a 96 × 64 grid:

  ```text
  nav    x=0  y=0  w=16 h=64 │ gauges x=16 y=0  w=52 h=24 │ power  x=68 y=0  w=28 h=24
                             │ trend  x=16 y=24 w=52 h=40 │ states x=68 y=24 w=28 h=40
  ```

- **Size for readability.** A chart below roughly 16 × 10 cells is too small to
  read. With few widgets, make them bigger rather than leaving space.
- **Keep everything inside the grid:** `x + w <= columns` and `y + h <= rows`.
- **Layering is allowed.** A small widget (a date picker, a button) may sit on top
  of a big one. `z` orders them, higher in front.

## Style

`style` applies to the whole board. Every key is optional:

```yaml
style:
    theme: {theme_name: light}   # --- light dark chalk essos halloween infographic macarons
                                 # purple-passion roma shine vintage walden westeros wonderland
    background_color: '#fefefe'
    text_color: '#334d5c'
    tile_background_color: '#ffffff80'
    tile_border_color: '#fefefe'
    tile_border_radius: 8px
    grid_gap: 8                  # pixels between tiles
    columns: 96
    rows: 64
```

`overflow: true` makes the board scroll (cells are then `cell_size` pixels) instead
of scaling to the screen.

## SCADA pages

widget-scada draws one industrial symbol (a tank, pump, valve, pipe and so on) per
widget, stretched to its tile:

- **Find symbols** with `widget.py widget-scada --symbols <word>`, which prints each
  symbol's id, natural size, aspect ratio (height/width) and bindings.
- **Read one symbol's settings** with `widget.py widget-scada --when symbol=<id>`.
- **Give each SCADA position** `aspectRatio: <the symbol's aspectRatio>`,
  `fixedWidth: true` and `fixedHeight: true`, as the editor does. Size it in the
  proportion of its `widgetSize`.
- **Put symbols on a page of their own.** Arrange them like the process, not like a
  tiled dashboard: a SCADA page does not need to fill the screen. Ask the user to
  check the proportions in the board editor, because grid cells are not square.

## Left to the board editor

Some values only exist inside a running platform, and a template cannot know them:

- **`actionApp` / `actionDevice`** in switch and SCADA actions are numeric keys of
  an app and a device. Leave them out, and tell the user to pick them in the editor.
  On a data-bound list of devices, `actionDevice` can come from the `device_key`
  column.
- **Uploaded images** (`useUpload`). Use an image URL instead, or let the user
  upload.

## Checklist

1. Every `tablename` and column exists in data-template.yml (or under
   `provider_app` in a consumed app), and the app's code actually publishes it.
2. Every chartconfig key comes from the pinned widget version's schema, and every
   constant is prefixed.
3. Bindings sit at the right kind of property (valueRef on single values, tableRef
   on lists), each tableRef has a `limit` and a `format` shaped like its items, and
   aggregations have a complete window.
4. Every page fills the grid and has a way to navigate, and every route leads to a
   page.
5. `scripts/validate.py <app-dir>` (run it as SKILL.md shows) reports no errors.
