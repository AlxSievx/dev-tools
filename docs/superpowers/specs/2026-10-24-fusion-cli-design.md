# dev-tools — Fusion side: design

Date 2026-10-24. Status: draft for approval.

## Intent

`C:\dev-tools` becomes one tooling repository that lets Claude Code design
**mechanical parts in Fusion** and, later, **electronics in KiCad 10**. This
document covers the repository frame and the Fusion side; KiCad gets its own
brainstorm and design once the Fusion side works.

The why: Claude should design as well as it can. That means a tight loop —
write a script, run it, **measure** the result in numbers, **look** at it, fix —
with no timeouts in the way, little context spent per step, and every
non-trivial script kept as a file that can be diffed and re-run.

Today Claude reaches Fusion through `C:\fusion360-mcp-bridge` (a clone of
`ndoo/fusion360-mcp-bridge`, MIT): an add-in with an HTTP server on
`127.0.0.1:7654`, and an MCP server offering `fusion_execute` and
`fusion_screenshot`. Its limits, found in use on the Crawler project:

- the add-in answers 504 after 30 s while the script keeps running; Crawler
  works around it with `snippets/run_script.py` (stdout to a temp file, poll);
- every call starts with an empty namespace, so helpers are re-sent every time;
- `fusion_screenshot` returns base64 inside a JSON string — Claude cannot see it
  as an image and it costs many tokens; it is also broken on Fusion
  2705.1.15 (`saveAsImageFileWithOptions` changed signature; the documented fix
  `vp.saveAsImageFile(path, w, h)` was never applied to the add-in), and even
  with it **the camera change does not take effect before the capture**, so
  every image shows whatever was on screen (Crawler `open_points.md` B8);
- the long API notes (`CLAUDE.md`, 288 lines) load into every session.

From `C:\code-mode` we take ideas, not code: a short command card instead of a
manual in context, one script for a whole chain of steps, results kept out of
the conversation unless they are needed. We drop its sandbox (`vm2`), its MCP
proxying and its silent mock fallback.

## Goals

1. A Fusion add-in **`DevToolsBridge`** that runs scripts as **jobs**: submit,
   poll, stream output, no time limit on the script.
2. A **persistent session namespace** across jobs, with a helper library
   **`dt`** preloaded and hot-reloaded when its files change.
3. **Screenshots as PNG files** on disk, one or several views per call, which
   Claude opens with its Read tool.
4. A command-line tool **`devtools`**, run through Bash, as the only way Claude
   talks to Fusion.
5. A **compact state readout** (`devtools fusion info`) so Claude checks the
   design in numbers before and after a change.
6. A **short command card** that a project imports into its `CLAUDE.md`, with
   the longer API notes read only when needed.
7. Tests without Fusion for the logic, and a live test run with Fusion open.

## Non-goals

- No MCP server (the user chose the command-line approach, 2026-10-24). Claude
  Desktop and other MCP-only clients cannot use this tooling.
- No sandbox: scripts run inside Fusion with the full API, as today. The trust
  boundary stays localhost plus a token.
- No parallel execution: the Fusion API runs on its main thread, one job at a
  time, queued in arrival order.
- No hard cancel of a running script (Python on Fusion's main thread cannot be
  interrupted safely); a job can only be cancelled while still queued.
- No KiCad in this round; `kicad/` holds a README placeholder.
- No project-specific building blocks: Crawler's honeycomb, fasteners, parts
  stay in Crawler. `dev-tools` holds general tooling only.
- macOS: code stays portable where it costs nothing, but only Windows is tested.

## Anti-goals (a failure even if it works)

- A result that is not real. No mock or fallback output, ever: if Fusion is not
  reachable, the command fails loudly with exit code ≠ 0 and says why.
- More context per step than today. Output is capped; the full log goes to a file.
- Changes to the user's open documents by the tests. Live tests work in a new
  document they create and close without saving.

## Constraints

- Windows 11; Claude Code runs Git Bash and PowerShell.
- Host Python 3.15.0 runs the CLI: **standard library only**, nothing to install.
- Fusion's embedded Python is 3.14.0 (webdeploy build `736d2b4c…`); the add-in
  and `dt` use the standard library and `adsk` only — nothing is pip-installed
  into Fusion.
- Fusion 2705.1.15: screenshots via `vp.saveAsImageFile(path, w, h)`.
- All Fusion API calls happen on the main thread, reached from the HTTP thread
  through a registered custom event (the pattern the bridge already uses).
- The old add-in `FusionMCPBridge` is installed as a copy in
  `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\` with `runOnStartup`
  true, on port 7654. The new add-in therefore uses **port 7655**, so both run
  side by side during the build and the Crawler regression can compare them live.
- Claude Code's Bash tool stops a command after 10 minutes at most; Crawler's
  sweeps and `fit()` take minutes, so a job can outlast one CLI call.
- Commit rules are taken over from Crawler (see "Repository frame").

## Approaches considered

1. **Command line only, no MCP** — add-in with jobs, persistent namespace and
   file screenshots; a `devtools` CLI through Bash. No timeout, scripts as
   files, nothing in context until used; Desktop clients can't use it, Bash
   needs allow-rules. **Chosen by the user.**
2. Same, plus a thin MCP layer — keeps Desktop clients, but two front ends to
   test and keep in step.
3. A code-mode-style hub holding Fusion and KiCad as typed objects — adds a
   layer whose parallelism Fusion cannot use, and code-mode is not Windows-ready.

## Repository frame (decided)

- Location `C:\dev-tools` (user, 2026-10-24); the repo exists, branch `main`,
  no commits yet.
- `CLAUDE.md` carries the commit rules from Crawler: short subject, a body only
  when needed (two lines at most), **no `Co-Authored-By` trailer**, commit only
  when the user asks. Area prefixes: `fusion:`, `kicad:`, `cli:`, `doc:`,
  `treewide:`.

Layout (my proposal):

```
dev-tools/
  README.md            what it is, install, the two tools
  CLAUDE.md            layout, commit rules, how to test
  devtools/            the CLI package: __main__.py, fusion.py, client.py
  devtools.cmd         shim so `devtools …` works from Bash and PowerShell
  fusion/
    addin/DevToolsBridge/   manifest + add-in (HTTP thread, job queue, executor)
    dt/                     helper library imported inside Fusion
    CLAUDE.md               the command card a project imports
    docs/api-notes.md       Fusion API knowledge, migrated from the bridge
  kicad/README.md      placeholder
  tests/               unit tests; live tests marked `live`
  docs/superpowers/    specs and plans
```

## How Claude uses it

1. `devtools fusion health` — is Fusion up, which document is active.
2. `devtools fusion info` — document, design type, units, components and
   bodies (volume, bounding box in mm), user parameters, timeline errors and
   warnings. Compact text, a few dozen lines.
3. Claude writes `build_x.py` (in the project, or the scratchpad for a one-off)
   and runs `devtools fusion run build_x.py`. Output streams while the job runs;
   the command ends with exit 0 (done), 1 (script raised, traceback shown),
   2 (bridge unreachable), 3 (auth), 4 (**still running** — the wait cap,
   default 9 min, was reached; the job id is printed and the job goes on).
   `devtools fusion wait <id>` reattaches and streams the rest,
   `log <id>` prints the output so far, `cancel <id>` drops a queued job,
   `run --detach` submits and returns the id at once.
   `devtools fusion exec "print(dt.bodies())"` runs a one-liner.
4. `devtools fusion info` again, or a `dt` check, to measure the change.
5. `devtools fusion shot iso front top --out <dir>` writes PNGs and prints
   their paths; Claude reads them as images.
6. `devtools fusion reset` clears the session namespace.

## Fusion side (decided parts)

**Add-in `DevToolsBridge`** — installed as a **directory junction** from the
AddIns folder to `fusion/addin/DevToolsBridge`, so repo edits are live after
the add-in restarts (`mklink /J`, no admin). Port 7655 (a `port` entry in a
config file beside the add-in overrides it — an environment variable would have
to be set for Fusion's own process), binds 127.0.0.1, bearer token from the
existing `~/.fusion-mcp-secret` (kept so nothing else must change at once).
Startup shows no message box (the old one blocks Fusion's start); it logs to a
file.

Endpoints:
- `GET /health` → Fusion version, active document, job queue length.
- `POST /jobs` `{script | path, label}` → `{id}`; queued.
- `GET /jobs/{id}?since=<offset>` → state (queued / running / done / failed),
  output from that offset, traceback.
- `DELETE /jobs/{id}` → cancel while queued.
- `POST /screenshot` `{views[], width, height, dir}` → a job; its result is the
  paths of the PNGs written.
- `POST /reset` → fresh session namespace.

**Screenshots must show the view they are named for.** Because the camera
change does not take effect within one handler (B8), a screenshot is a job of
several main-thread steps: set the camera, hand control back to Fusion (a
follow-up custom event, or `adsk.doEvents()`), read the camera back, and only
then capture. If the camera read back does not match the requested view, the
view fails loudly (exit 1, no file) — never an image under a wrong name.
This is proven by a **spike first**, before the rest of the add-in is built on
it. If no way to drive the camera works, screenshots ship as "current view
only" and the numbers from `info` / `dt` remain the check.

*Spike result (2026-10-24, Fusion 2705.1.30):* B8 no longer reproduces. With
`cam.isSmoothTransition = False`, `vp.camera = cam`, `vp.fit()`, `vp.refresh()`
and `vp.saveAsImageFile(path, w, h)` in one call, front / top / right / iso all
showed the requested view (checked by eye; all hashes differ; camera read-back
matched). Even the old bridge's sequence without those calls produced correct
views, only unfitted. `ShadedWithVisibleEdgesOnlyVisualStyle` makes edges
readable and is set for the capture, then restored. The camera read-back and
the duplicate-hash check stay as guards; the `yield` between views stays so
Fusion repaints and stays responsive.

The job queue and HTTP handling live in a module that does **not** import
`adsk`, so it can be tested with a fake main-thread pump.

**Session namespace** — one dict kept across jobs, holding `adsk`, `app`, `ui`,
`dt`; `design` (and `root`) are re-resolved from the active document before
every job, since the user may switch documents. Scripts that define
`run(context)` keep working (it is called, as today), so existing Crawler
snippets run unchanged.

**`dt` helpers** (general, first set): `info()`, `bodies()`, `params()` /
`set_params({...})`, `health()` (timeline errors and warnings), `mm()` / `cm()`
conversions, `shot()`. Reloaded when any file under `fusion/dt/` changed since
the last job.

**Output** — stdout of each job goes to a log file under
`%TEMP%\devtools\fusion\<job id>.log`; the CLI prints at most ~200 lines and
points to the log for the rest.

**Knowledge** — `fusion/CLAUDE.md` is the short card (commands, exit codes,
units in cm, `dt` list, main pitfalls, a pointer to `docs/api-notes.md`).
Crawler's `Hardware/pv_robot_v1_0/CLAUDE.md` changes its first line from
`@C:/fusion360-mcp-bridge/CLAUDE.md` to `@C:/dev-tools/fusion/CLAUDE.md`.

**Permissions** — an allow rule for `devtools fusion *` in the user's Claude
Code settings, so the loop runs without prompts.

## Testing

Without Fusion (`python -m unittest`, host Python):
- CLI argument handling, exit codes, output capping, the message when the
  bridge is unreachable or the token is wrong.
- The job queue and HTTP module against a fake executor: ordering, two jobs
  submitted at once, output streaming with offsets, cancel while queued, a job
  that raises.

Spike first (throwaway, through the old bridge): can a camera change be made
to take effect before a capture on 2705.1.15? Its answer sets the screenshot
design above.

Live, Fusion open (`devtools fusion selftest`, also runnable as `-m live` tests),
in a new document it creates and closes without saving:
1. health; a short run; a one-liner exec.
2. a job of ~45 s — passes the old 30 s limit, output streams during the run.
3. a script that raises → traceback, exit 1, Fusion still responsive after.
4. namespace persists between two runs; `reset` clears it.
5. `dt` hot-reload after editing a helper file.
6. switching the active document → `design` follows.
7. screenshots of four views written and readable as images, each showing
   the view it is named for (checked by eye once, and by camera read-back).
8. a small parametric part end to end: user parameters, sketch, extrude,
   fillet; change a parameter; volume checked in numbers; screenshot.
9. the add-in stopped → CLI exit 2 with a clear message; restarted → works.
10. a job longer than a lowered wait cap → exit 4 with its id; `wait <id>`
    reattaches and ends with exit 0; `cancel` drops a queued job.

Regression on Crawler (read-only, user's document open): one existing
verification snippet (`verify_deck_top_mount.py` `fit()`) run through
`devtools fusion run` (port 7655) gives the same output as through
`run_script.py` (old bridge, port 7654), both add-ins running.

## Migration (last step, each change confirmed with the user)

1. `DevToolsBridge` is installed early and runs beside the old add-in during the
   build. At the end the user stops and unticks `FusionMCPBridge` in Fusion's
   Add-Ins dialog.
2. Crawler: switch the `CLAUDE.md` import, drop the toolchain note about the
   broken screenshot, retire `snippets/run_script.py` — committed in Crawler
   under its own rules.
3. Remove the `fusion` MCP server registration from the Claude Code config.
4. `C:\fusion360-mcp-bridge` stays on disk untouched until the user deletes it.

## Calls made by Claude, not yet confirmed by the user

- `dev-tools` holds general tooling only; Crawler's house-style snippets stay.
- The commit-area prefixes listed above.
- Port 7655, side by side with the old bridge until migration.
- The secret stays in `~/.fusion-mcp-secret`.
- Wait cap 9 min with exit 4 and `wait` / `log` / `cancel` / `--detach`.
- Screenshots: spike first; a view that cannot be confirmed fails rather
  than saving a wrong image.
- The add-in installs as a directory junction into the repo.

## Left to the builder

Module split inside the add-in and the CLI, the exact output cap, log
rotation, the poll interval, the exact `info()` layout, test file names.
