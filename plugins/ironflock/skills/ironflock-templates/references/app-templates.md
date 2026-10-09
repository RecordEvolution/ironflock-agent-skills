# data, env, port and ai templates

Each of these files has a JSON Schema in `schemas/<name>/v1.yml` (in this skill's
folder) with a description on every field. Read the schema before writing the file;
this page only adds what a schema cannot say: how the file connects to the app's
code and to the rest of the platform.

Start every file with its schema line. It gives the developer validation and
autocomplete in their editor:

```yaml
# yaml-language-server: $schema=https://ironflock.com/schemas/data-template/v1.yml
```

## Contents

- [data-template.yml](#data-templateyml): tables in the project database
- [env-template.yml](#env-templateyml): app parameters
- [port-template.yml](#port-templateyml): remote access
- [ai-template.yml](#ai-templateyml): AI agents
- [Python SDK quick reference](#python-sdk-quick-reference)

## data-template.yml

Docs: <https://ironflock.com/docs/app-development/data-backend>

When the app is installed in a project, the platform creates these tables in that
project's own database. Boards, the SQL workbench, AI agents and other apps then
read them.

- **The app fills the tables; the template only declares them.** Rows arrive through
  the SDK: `await flock.publish_to_table("sensordata", {"temperature": 21.5, ...})`.
  `tablename` must match the first argument, and each column's `path` (default
  `args[0].<id>`) picks the value out of the published object. Read the app's code
  and keep the two in agreement. A key the code publishes with no matching column is
  dropped, and a column nobody publishes stays NULL. Neither raises an error.
- **Every table has a `tsp` column of `dataType: timestamp`.** A published row
  without a value at the `tsp` path is rejected and logged as an error for the app.
  Boards order by `tsp`, and time windows and aggregation key on it. Publish an ISO
  8601 timestamp with a time zone.
- **Every column needs `id`, `name`, `description` and `dataType`.** The schema
  requires all four. Some older documentation examples omit `name` or `description`;
  follow the schema. Descriptions are read by people in the board editor and by AI
  agents, so say what the value means and its unit.
- **`device_key` and `authid` exist on every table automatically**, so don't declare
  them (the schema reserves those ids). `device_key` identifies the device that
  published the row, which makes it the natural pivot for "one series per device" and
  the usual entity key: `maintainLatestFlagFor: [device_key]`.
- `maintainLatestFlagFor` makes a table an entity table: read the newest row per
  entity, and send only the changed columns in a new row (the rest are inherited).
  Use it for state such as machine status, settings or orders.
- Transforms are SQL views over the app's own tables. Boards can bind to them, but
  board-side aggregation only works on plain tables. Do the aggregation in the
  transform's SQL instead.
- Add `downsample` to tables that will back charts spanning weeks or months.
- To read another app's tables, list it under `consumes:`. Board widgets that bind
  to those tables then need `provider_app` on their data reference (see
  board-template.md).

## env-template.yml

Docs: <https://ironflock.com/docs/app-development/app-parameters>

Parameters the user can set per device or per device group without a custom UI.

- Top-level keys are environment variable names (`^[A-Z0-9_]+$`). Each holds
  `label` and `type` (`numeric`, `text`, `textarea` or `boolean`) plus optional
  `defaultValue`, `description`, `unit`, `min`, `max`, `secret` and `valueList`.
- **A dropdown is `valueList: [a, b, c]`.** An older documentation example shows
  `type: select`, `options` and `optional`. The schema rejects `type: select`, and
  the parameters form ignores `options` and `optional`, so don't use any of them.
- Values reach the container as environment variables, so they are always strings.
  Parse them in code, and fall back to the same default as `defaultValue`. Every
  value is also written to `/data/env/<NAME>.txt`, and a value over 20 KB exists
  only there.
- `defaultValue` is what the app gets while running in development.
- `DEVICE_NAME`, `DEVICE_SERIAL_NUMBER` and `SWARM_KEY` are always set by the
  platform. Don't declare them.

## port-template.yml

Docs: <https://ironflock.com/docs/remote-access/port-template>

Ports the app serves (a web UI, a video stream, VNC, an OPC UA server) that
privileged users may open as a remote-access tunnel. Declaring a port never opens
it by itself.

- `ports: [{name, port, main?, protocol?, remote_port_environment?}]`. `port` is the
  port the app listens on. `protocol` defaults to `http`, which is served to the
  user as HTTPS with websockets working. Use `tcp` or `udp` for raw sockets.
- Mark at most one port `main: true`. It is opened when the user clicks the app's
  icon on a device.
- Several apps can run on one device, so the platform maps each declared port to a
  free host port. The app learns the real values from injected variables:
  `DEVICE_LAN_IP`, `DEVICE_PORT_FOR_<port>`, and for an active tcp/udp tunnel
  `REMOTE_PORT_FOR_<port>`. Never hard-code the declared port into a URL you show
  to the user.
- A board can show the app's web UI inside an embed widget (widget-embed).

## ai-template.yml

Docs: <https://ironflock.com/docs/app-development/ai-template>

AI agents the app contributes to the IronFlock assistant.

- Top-level keys are agent names. Exactly one agent should be `main: true`. That is
  the one the IronFlock assistant can call, and its `tool_description` tells the
  assistant when to call it. The others are sub-agents, reached through a
  `delegate:` tool.
- Required on every agent: `system_prompt`, `tools` (at least one),
  `max_context_tokens`, `messages_after_summary` and `max_iterations`.
- Each tool has a `description` and exactly one of `topic` or `delegate`.
- A tool with `topic: <name>` calls a function the app registers on each device:
  `await flock.register_device_function("<name>", handler)`. The names must match
  exactly. The platform adds a required `device_key` parameter so the assistant can
  pick the device, and strips it before the call, so don't declare it and don't
  expect it in the handler. The handler's return value and error messages go
  straight to the model, so make them readable text or small JSON.
- `register_function()` still works but is deprecated. Write
  `register_device_function()` in new code.
- `data_access: true` gives the agent read-only SQL over the app's own tables.
  The agent learns the columns from data-template.yml descriptions, so write those
  well instead of repeating them in `system_prompt`.

## Python SDK quick reference

`pip install ironflock` (JavaScript: `npm install ironflock`, same names in
camelCase).

```python
from datetime import datetime, timezone
from ironflock import IronFlock

async def toggle_lamp(on: bool):
    ...                                   # called by widget actions and AI tools
    return {"lamp": "on" if on else "off"}

async def main():
    await flock.register_device_function("toggle_lamp", toggle_lamp)
    await flock.publish_to_table("sensordata", {
        "tsp": datetime.now(timezone.utc).isoformat(),
        "temperature": 21.5,
    })

flock = IronFlock(mainFunc=main)
flock.run()
```

JavaScript: `registerDeviceFunction`, `publishToTable`, `publishRowsToTable`.
