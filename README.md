# IronFlock agent skills

A Claude Code plugin that teaches coding agents to write an IronFlock app's
integration templates, the files in its `.ironflock/` folder, correctly:

| File | Purpose |
|---|---|
| `board-template.yml` | the app's board: widgets, data bindings, pages, layout |
| `data-template.yml` | the app's tables and transforms in the project database |
| `env-template.yml` | app parameters, delivered as environment variables |
| `port-template.yml` | ports users may open for remote access |
| `ai-template.yml` | the app's AI agents for the IronFlock assistant |

The skill gives the agent each template's schema and rules, a catalog of the 24
board widgets, a tool that reads any widget's published configuration schema, and a
validator. The validator catches mistakes the platform would otherwise report only
at install time, or not at all, such as a widget bound to a column that doesn't
exist.

## Install

In Claude Code:

```text
/plugin marketplace add RecordEvolution/ironflock-agent-skills
/plugin install ironflock@ironflock
```

Or from a shell:

```bash
claude plugin marketplace add RecordEvolution/ironflock-agent-skills
claude plugin install ironflock@ironflock
```

Claude Code does not auto-update third-party plugins by default. Turn it on under
`/plugin` → Marketplaces → `ironflock` → Enable auto-update, or pull changes with
`claude plugin marketplace update ironflock`.

To make the plugin available to everyone working on an app, commit this to the
app's `.claude/settings.json`. It loads once a person trusts the folder:

```json
{
  "extraKnownMarketplaces": {
    "ironflock": {
      "source": { "source": "github", "repo": "RecordEvolution/ironflock-agent-skills" },
      "autoUpdate": true
    }
  },
  "enabledPlugins": { "ironflock@ironflock": true }
}
```

The skill triggers on its own when you work on an IronFlock app. You can also
invoke it directly as `/ironflock:ironflock-templates`.

## Requirements

- Python 3.9 or later.
- [`uv`](https://docs.astral.sh/uv/) is recommended. The validator needs PyYAML and
  jsonschema and installs them through `uv run` by itself. Without uv, run
  `python3 -m pip install pyyaml jsonschema`.
- Network access to `registry.npmjs.org` and `cdn.jsdelivr.net` for widget schemas.
  `validate.py --offline` skips the widget checks.

## Layout

```text
.claude-plugin/marketplace.json          marketplace "ironflock"
plugins/ironflock/
├── .claude-plugin/plugin.json           plugin "ironflock"
└── skills/ironflock-templates/
    ├── SKILL.md                         workflow and principles
    ├── references/
    │   ├── board-template.md            board format, data bindings, pages, layout
    │   ├── example-board.yml            a complete, valid three-page board
    │   ├── widget-catalog.md            which widget for which data (generated)
    │   └── app-templates.md             data, env, port and ai templates
    ├── schemas/<name>/v1.yml            JSON Schemas (vendored, see below)
    └── scripts/
        ├── validate.py                  validates a whole .ironflock folder
        ├── board_checks.py              the board-specific part of it
        └── widget.py                    prints a widget's published schema
tools/build_widget_catalog.py            regenerates widget-catalog.md
tests/                                   validator regression tests
evals/                                   test app, eval prompts and grader for the skill
```

## Maintenance

**Schemas are vendored, never edited here.** The canonical files live in
`ironflock-landingpage/public/schemas/` and are published at
`https://ironflock.com/schemas/<name>/v1.yml`. After changing one there, run
`bun run schemas:vendor` in `ironflock-landingpage` (with this repo checked out next
to it), then commit the copies here. `bun run schemas:check` reports drift.

**Widget catalog.** Run `python3 tools/build_widget_catalog.py` when a widget is
added or removed, or when its `aiSelection` text changes. The widget list in that
script mirrors the widgets the platform registers. Widget property names are never copied into this repo:
`widget.py` and the validator read them live from npm, so widget releases need no
change here.

**Updates reach users by commit.** `plugin.json` sets no `version` on purpose. Users
then track the commit SHA, and every push becomes an update. If you add a `version`,
you have to bump it on every change, or users keep the old copy.

**Test the validator** after changing `validate.py` or `board_checks.py`:
`python3 tests/run.py`. It requires the example board to stay clean and every
mistake in `tests/boards/broken.yml` to be reported.

**Evaluate the skill** after changing its instructions: run each prompt in
`evals/evals.json` against a fresh copy of `evals/fixtures/machine-monitor`, once
with the skill and once without, then score the runs with
`uv run evals/grade.py <iteration-dir>` (layout: `<eval-name>/<config>/app`).

**Check the manifests** before pushing:

```bash
claude plugin validate .
claude plugin validate plugins/ironflock
```

## License

MIT. See [LICENSE](LICENSE).
