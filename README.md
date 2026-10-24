# dev-tools

Tooling that lets Claude Code design hardware: mechanical parts in
**Autodesk Fusion** now, electronics in **KiCad 10** later.

Claude works through one command, `devtools`, run from a shell. It writes a
script file, runs it inside Fusion, measures the result in numbers, looks at
screenshots, and fixes. No MCP server sits in between.

| Tool | State | Start with |
|------|-------|------------|
| Fusion | in development | [`fusion/CLAUDE.md`](fusion/CLAUDE.md) |
| KiCad | not started | [`kicad/README.md`](kicad/README.md) |

## How it works with Fusion

```
devtools (CLI, host Python)  ──HTTP 127.0.0.1:7655, token──▶  DevToolsBridge add-in
                                                               └─ custom event ─▶ Fusion main thread
                                                                                   runs the job, keeps a session
```

Scripts run as **jobs**: queued, run one at a time on Fusion's main thread,
output streamed back, no time limit. Names defined by one job stay for the
next until `devtools fusion reset`. Screenshots are written as PNG files.

## Install

Needs Python ≥ 3.12 on the host and Autodesk Fusion on Windows.

1. `C:\dev-tools\bin` on the user `PATH`.
2. `devtools fusion install` links the add-in into Fusion's AddIns folder.
3. In Fusion: Utilities → Add-Ins → `DevToolsBridge` → Run (and tick
   *Run on Startup*).
4. `devtools fusion health`.

The token is `~/.fusion-mcp-secret`; the add-in creates it if it is missing.

## Origins

The add-in grew out of [ndoo/fusion360-mcp-bridge](https://github.com/ndoo/fusion360-mcp-bridge)
(MIT); the idea of letting the model write one script for a chain of steps
comes from Code Mode.
