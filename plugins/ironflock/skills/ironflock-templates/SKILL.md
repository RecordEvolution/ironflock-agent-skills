---
name: ironflock-templates
description: Write, edit and validate the IronFlock integration files in an app's .ironflock/ folder. These are board-template.yml (the app's dashboard, with widgets, data bindings, pages and layout), data-template.yml (database tables and transforms), env-template.yml (app parameters), port-template.yml (remote-access ports) and ai-template.yml (AI agents). Use this whenever you build or change an IronFlock app, add or change a board, dashboard or widget, bind a widget to live data, add a table or column, expose a parameter or port, or define an app's AI agent. Also use it when the user mentions IronFlock, Reswarm or a .ironflock folder, even if they don't say "template".
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/*), Bash(uv run ${CLAUDE_SKILL_DIR}/scripts/*)
---

# IronFlock integration templates

An IronFlock app is a Docker app plus an optional `.ironflock/` folder that plugs it
into the platform. When a user installs the app in a project, the platform reads
these files. Each file is optional and independent:

| File | What the platform does with it |
|---|---|
| `data-template.yml` | Creates the app's tables in the project's database. The app fills them through the SDK. |
| `board-template.yml` | Installs the app's board (dashboard): widgets bound to those tables, laid out on pages. |
| `env-template.yml` | Renders a parameters form per device or device group. Values reach the app as environment variables. |
| `port-template.yml` | Lists ports that privileged users may open as remote-access tunnels. |
| `ai-template.yml` | Registers the app's AI agents with the IronFlock assistant. |

Each file has a JSON Schema in `${CLAUDE_SKILL_DIR}/schemas/<name>/v1.yml`, the same
file that is published at `https://ironflock.com/schemas/<name>/v1.yml`. Every field
in it is described.

## Workflow

1. **Read before writing.** Look at the existing `.ironflock/` files and at the app's
   code. The code shows what the app actually publishes (`publish_to_table` calls)
   and which functions it registers (`register_device_function` calls). The templates
   have to agree with it. If templates already exist, run the validator (step 4)
   once before changing anything, so you can tell findings that were already there
   from ones you introduce.
2. **Data comes first.** A board can only show what a table in `data-template.yml`
   holds. Design or extend the tables before the board. If the code doesn't publish
   the data yet, say so and change the code too.
3. **Write the file.** For the board, read
   [references/board-template.md](references/board-template.md) and the complete
   example [references/example-board.yml](references/example-board.yml) first,
   choose widgets with [references/widget-catalog.md](references/widget-catalog.md),
   and look up each chosen widget's exact properties (see below). For the other four
   files, read [references/app-templates.md](references/app-templates.md) and the
   file's schema.
4. **Validate, fix and repeat until there are no errors:**

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/validate.py <app-dir>
   ```

   It checks every template against its schema, the rules the platform otherwise
   reports only at install time, and each board widget against the widget's
   published schema. Each finding names the file, line and path. If it reports a
   missing module, run it as `uv run ${CLAUDE_SKILL_DIR}/scripts/validate.py <app-dir>`,
   or install `pyyaml` and `jsonschema`. Warnings are judgment calls. Read each one
   and fix it unless it is deliberate. The validator has its own test suite, so a
   clean run is enough; there is no need to seed mistakes to test it.
5. **Report** what you created, which tables and widgets connect, and anything the
   app's code still has to do. Tell the user they can open the board in IronFlock's
   board editor to adjust it visually. The editor reads and writes the same
   `board-template.yml`.

## Widget schemas

Every widget is an npm package whose `definition-schema.json` defines exactly what its
`chartconfig` may contain. Read it before configuring the widget, because property
names differ between widgets and change between releases:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/widget.py widget-linechart            # outline of all properties
python3 ${CLAUDE_SKILL_DIR}/scripts/widget.py widget-linechart --path dataseries
python3 ${CLAUDE_SKILL_DIR}/scripts/widget.py widget-linechart --example  # a working config
python3 ${CLAUDE_SKILL_DIR}/scripts/widget.py widget-scada --symbols tank      # find SCADA symbols
python3 ${CLAUDE_SKILL_DIR}/scripts/widget.py widget-scada --when symbol=cylindrical-tank
```

The outline marks `dataDrivenDisabled` (cannot be bound to data), enums, defaults and
conditional fields. `--example` prints plain values. In a board template the same
config needs the literal prefixes and data references described in board-template.md.

## Principles

- **Bind, don't paste.** If a value lives in a table, bind the widget to it with a
  data reference. Pasted numbers go stale the moment the board is installed.
- **Use only names that exist.** Every table, column and widget property you write
  must exist in data-template.yml or the widget's schema. The platform ignores unknown
  keys silently, so an invented key renders as nothing rather than as an error.
- **Keep the schema line.** Start every template with
  `# yaml-language-server: $schema=https://ironflock.com/schemas/<name>/v1.yml`, so
  the developer's editor validates the file too. Older platform versions dropped
  comments from board-template.yml when the board was saved in the editor, so
  re-add the line there if it has gone missing.
- **Leave platform-only values to the board editor.** The numeric app and device keys
  that switch and SCADA actions target only exist in a running project. Leave them
  out and tell the user to pick them in the editor.
- **Don't touch what you weren't asked to change.** Existing boards can carry configs
  from older widget versions. Fix validator errors in widgets you add or edit. For
  errors in widgets you didn't touch, report them instead of rewriting them silently.
