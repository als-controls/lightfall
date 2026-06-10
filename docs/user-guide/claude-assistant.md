# Claude Assistant

Lightfall embeds a Claude-based agent that understands the application and
the beamline it is running on. It answers questions, but it can also *act*:
open panels, read and move devices, run plans, inspect data — and build new
panels and plans on request (covered separately in
[Customizing Lightfall with the Agent](agent-customization.md)). Every action
goes through an approval prompt unless you have allowed it.

## Setup

The assistant needs Claude credentials. In **File → Settings → Claude
Assistant**:

- **API key** — paste an Anthropic API key, or set the `ANTHROPIC_API_KEY`
  environment variable before launching.
- **OAuth** — if you have authenticated the Claude Code CLI on this machine
  (`claude auth login`), the settings page shows your OAuth status and the
  assistant can use that instead of an API key.

The same page selects the **model**, the **maximum turns** per request, and
the **permission mode** (see below).

## Using the assistant

Open the **Claude Assistant** panel from the sidebar (lower section). Type
in the input field and press Enter to send (Shift+Enter inserts a newline).
The broom button clears the conversation and starts fresh.

> 🖼️ **Image placeholder** — *Screenshot: Claude Assistant panel with a short conversation and the input field at the bottom*

Things you can ask, with the simulated devices as examples:

**Questions and help**

- "How do I run a 2D grid scan?"
- "What's the difference between an absolute and a relative scan?"
- "Why did my last scan fail?"

**Application control**

- "Open the Devices panel"
- "What panels are available?"

**Device operations**

- "What motors are available?"
- "Read the temperature sensor"
- "Move motor to 2.5"

**Acquisition and data**

- "Run a 1D scan of motor from -5 to 5 with det, 21 points"
- "What's the RunEngine doing right now?"
- "Show me the last run in the visualization panel"

The assistant sees the same application state you do — the panel registry,
the device catalog, the plan registry, run history — so its answers are about
*your* session, not generic Bluesky advice.

## Approval prompts

The assistant's tools follow a whitelist design: **read-only tools run
without asking** (listing panels and devices, reading values, checking run
status), and **everything that changes state requires your approval** —
moving a motor, running a plan, opening or closing panels, writing files.

When the assistant wants to use a tool that is not auto-approved, a prompt
appears in the chat showing the tool name and its exact input, with three
choices:

- **✓ Allow** — permit this one call
- **✗ Deny** — refuse it (the assistant is told, and can try something else)
- **∞ Always** — permit this tool for the rest of the session without asking
  again

> 🖼️ **Image placeholder** — *Screenshot: an approval prompt in the chat for `lightfall_move_motor` showing the motor name and target position, with Allow / Deny / Always buttons*

```{note}
Plans the assistant submits go through the same path as plans you run
yourself — including the Sample Metadata dialog and the RunEngine queue. You
keep the toolbar pause/stop/abort controls regardless of who started the run.
```

### Permission modes

**File → Settings → Claude Assistant → Permission Mode** adjusts the gate:

| Mode | Behavior |
|------|----------|
| `default` | Confirmation required for all non-read-only actions |
| `acceptEdits` | File-edit tools are also auto-approved |
| `bypassPermissions` | No confirmations (for unattended automation — use deliberately) |

## Skills and tools

The assistant's capabilities come from **agent plugins** — bundles of domain
prompts and tools. Built-in ones cover device operations, plan management,
engine control, panel building, beamline alignment, scan planning, and
adaptive (gpCAM/Tsuchinoko) experiment design; beamline deployments and user
plugins can add more.

**File → Settings → Assistant Tools** lists every agent plugin with a
checkbox to enable or disable it. Disabling a plugin removes its prompts and
tools from the assistant entirely.

## Tips

- **Be specific.** "Run a 1D scan of motor1 from -2 to 2 with det1, 41
  points" beats "run a scan".
- **Use it to learn.** "What plans are available?" or "explain what the
  adaptive scan parameters mean" are fast ways into the system.
- **Watch the prompts.** The approval dialog shows exactly what the assistant
  is about to do, with real parameter values — read them before allowing,
  especially for motor moves on real hardware.
- **Reset when switching topics.** Long conversations slow responses; the
  broom button starts a clean one.

## Troubleshooting

### The assistant doesn't respond

- Check credentials in **File → Settings → Claude Assistant** (the page has a
  connection test).
- Check network access to the Anthropic API (or your configured custom
  endpoint), and the **Logging** panel for errors.

### The assistant can't perform an action

- Device- and engine-control tools check *your* permissions — in a guest
  session the assistant cannot move motors or control the RunEngine.
- The tool's plugin may be disabled under **Assistant Tools**.

### Responses are slow

- Reset the conversation to clear accumulated history.
- Large tool results (long device lists, big scans) take time to process;
  narrower questions help.
