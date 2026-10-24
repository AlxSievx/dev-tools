# dev-tools Fusion CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Fusion add-in `DevToolsBridge` with a job queue, a persistent
session and file screenshots, driven by a stdlib-only `devtools` CLI.

**Architecture:** The add-in keeps the bridge's pattern (HTTP thread on
localhost → custom event → Fusion main thread) but splits it into adsk-free
modules (`jobs.py`, `server.py`, `executor.py`) that unit tests drive with a fake
main-thread pump, plus a thin adsk shell. A job may be a generator: each `yield`
hands control back to Fusion and the job continues on the next event — this is
how screenshots let the camera settle. The CLI submits jobs over HTTP and polls.

**Tech Stack:** Python 3.15 (host, CLI, tests: `unittest`), Python 3.14 inside
Fusion (add-in, `dt`), standard library only, Fusion API `adsk.*`.

**Spec:** `docs/superpowers/specs/2026-10-24-fusion-cli-design.md`

## Global Constraints

- Standard library only, on both sides; nothing pip-installed, into Fusion or the host.
- Add-in binds `127.0.0.1`, default port **7655**; override order for the CLI:
  env `DEVTOOLS_FUSION_PORT` > `fusion/addin/DevToolsBridge/config.json` `{"port": N}` > 7655.
  The add-in reads only `config.json` > 7655.
- Token: `~/.fusion-mcp-secret` (stripped); every endpoint, `/health` included,
  answers 401 without `Authorization: Bearer <token>`.
- Exit codes: 0 done, 1 script failed, 2 bridge unreachable / job unknown,
  3 auth, 4 still running (wait cap reached, job id printed), 5 usage error
  (argparse's own 2 is overridden).
- Wait cap default **540 s** (9 min), `--wait-cap` overrides; CLI prints at
  most **200** output lines, then one line pointing at the job's log file.
- Job logs: `%TEMP%\devtools\fusion\<job id>.log`, UTF-8.
- Screenshots: `vp.saveAsImageFile(path, w, h)`; never write an image whose
  view cannot be confirmed.
- No mock or fallback results anywhere.
- Commit rules from `CLAUDE.md`: subject `<area>: …`, areas `fusion:`, `kicad:`,
  `cli:`, `doc:`, `treewide:`, no `Co-Authored-By`, commit only when the user asks.

## Review Focus

1. A script without `run()` after one that defined it → the stale `run` must
   not be called again (Task 4 test `test_stale_run_not_called`).
2. A script file in UTF-8 with umlauts that prints umlauts → read and echoed
   unchanged (Task 4 `test_utf8_script_and_output`).
3. A script calling `sys.exit()` or raising `KeyboardInterrupt` → job failed,
   session survives, next job runs (Task 4 `test_base_exception_fails_job`).
4. The bridge restarted while the CLI waits → job id unknown (404) → message
   "job <id> is not known to the bridge (restarted?)", exit 2 (Task 7
   `test_follow_job_vanishes`).
5. 10 000 lines of output → 200 printed, then the log pointer; exit code still
   0; the log holds all lines (Task 7 `test_output_capped`).

## File Structure

```
CLAUDE.md, README.md, .gitignore          frame (Task 1)
bin/devtools, bin/devtools.cmd            shims → python -m devtools (Task 1)
devtools/__init__.py, __main__.py         CLI entry, argparse (Task 1, 7)
devtools/client.py                        HTTP client, port/token, errors (Task 7)
devtools/fusion.py                        fusion subcommands (Task 7)
devtools/install.py                       junction install (Task 9)
devtools/selftest.py                      live checks (Task 9)
fusion/addin/DevToolsBridge/devtools_bridge/jobs.py       Job, JobQueue (Task 2)
fusion/addin/DevToolsBridge/devtools_bridge/server.py     HTTP endpoints (Task 3)
fusion/addin/DevToolsBridge/devtools_bridge/executor.py   Session: namespace, exec, generators (Task 4)
fusion/addin/DevToolsBridge/DevToolsBridge.py + .manifest + config.json   adsk shell (Task 5)
fusion/dt/__init__.py, core.py, view.py   helpers inside Fusion (Task 6)
fusion/CLAUDE.md, fusion/docs/api-notes.md   command card, API notes (Task 8)
kicad/README.md                           placeholder (Task 1)
tests/test_cli_smoke.py, test_jobs.py, test_server.py, test_executor.py, test_cli.py, test_live.py
tools/dev_load.py                         (re)loads the add-in through the old bridge (Task 5)
```

Tests run from the repo root: `python -m unittest discover -s tests -v`.
Tests that import add-in modules put `fusion/addin/DevToolsBridge` on `sys.path` (`tests/_paths.py`)
and import the package `devtools_bridge` — a package, not bare `jobs`/`server` modules, because
Fusion shares one `sys.modules` among all add-ins.

**Status 2026-10-24:** Tasks 0–9 done except Task 9 Steps 1 and 3 (install from Fusion's
Add-Ins dialog, Crawler regression), which need the user. Added on the way: `exec` prints a
trailing expression; tracebacks start at the script, not the executor.

---

### Task 0: Screenshot spike (throwaway, Fusion open, old bridge)

Not committed. Answers: what makes a camera change land before
`saveAsImageFile`? Run through `C:\Crawler\Hardware\pv_robot_v1_0\snippets\run_script.py`
(old bridge, port 7654) in a new scratch document with one box.

- [ ] **Step 1:** variant A — `cam.isSmoothTransition = False`, set eye/target/up,
  `vp.camera = cam`, `vp.fit()`, `vp.refresh()`, save `front` and `top`.
- [ ] **Step 2:** variant B — A plus `adsk.doEvents()` before saving.
- [ ] **Step 3:** variant C — set camera in one call, save in a second call
  (control back to Fusion in between).
- [ ] **Step 4:** open each PNG with Read; also compare SHA-256 of `front` vs `top`.
  Record which variant shows the right views in the spec's screenshot paragraph.
  Expected: at least one variant gives two different, correct images; if none
  does, screenshots ship as "current view only" (spec fallback).

### Task 1: Repository frame and CLI entry

**Files:** Create `CLAUDE.md`, `README.md`, `.gitignore`, `kicad/README.md`,
`bin/devtools`, `bin/devtools.cmd`, `devtools/__init__.py`, `devtools/__main__.py`,
`tests/test_cli_smoke.py`

**Interfaces:**
- Produces: `devtools.__version__ = "0.1.0"`; `devtools.__main__.main(argv: list[str] | None = None) -> int`
  with an argparse parser whose `error()` exits 5; subparser `fusion` filled by Task 7.

- [ ] **Step 1: failing test** `test_version` — `subprocess.run([sys.executable, "-m", "devtools", "--version"])`
  prints `devtools 0.1.0`, exit 0; `test_usage_error_exit_5` — `-m devtools --bogus` exits 5.
- [ ] **Step 2:** run `python -m unittest tests.test_cli_smoke -v` → FAIL (no module).
- [ ] **Step 3:** implement `main`; shims: `bin/devtools.cmd` =
  `@set "PYTHONPATH=%~dp0..;%PYTHONPATH%" & python -m devtools %*`;
  `bin/devtools` (sh) sets `PYTHONPATH` to `cygpath -w` of the repo root
  (Windows Python wants `;` and Windows paths) and `exec python -m devtools "$@"`.
  `CLAUDE.md`: layout, test command, commit rules (Global Constraints), "planning
  lives in docs/superpowers". `README.md`: what it is, install (Task 9), the two tools.
- [ ] **Step 4:** tests PASS; `bin/devtools --version` works from Git Bash and PowerShell.
- [ ] **Step 5:** commit `treewide: repository frame and devtools entry point` (when the user OKs commits).

### Task 2: Job queue

**Files:** Create `fusion/addin/DevToolsBridge/devtools_bridge/jobs.py`, `tests/test_jobs.py`

**Interfaces:**
- Produces:
  - `class Job`: `id: str` (8 hex), `kind: str` (`"script" | "shot" | "reset"`),
    `payload: dict`, `label: str`, `state: str` (`queued|running|done|failed|cancelled`),
    `result: object`, `error: str | None`, `log_path: str | None`;
    `write(text: str) -> None` (thread-safe; appends to buffer and log file);
    `read(since: int = 0) -> tuple[str, int]` (text from char offset, new offset);
    `to_dict(since: int = 0) -> dict` with keys `id kind label state output offset result error log`.
  - `class JobQueue(log_dir: Path | None = None, keep: int = 50)`:
    `submit(kind, payload, label="") -> Job`, `next() -> Job | None` (FIFO, marks running),
    `get(job_id) -> Job | None`, `cancel(job_id) -> bool` (queued only),
    `finish(job, result=None, error=None) -> None` (`failed` iff error),
    `snapshot() -> dict` `{"queued": int, "running": str | None}`.
    Keeps the last `keep` finished jobs; older ones are forgotten.

- [ ] **Step 1: failing tests:** `test_fifo_order`, `test_read_since_offset`
  (write "a\n","b\n" → `read(0)==("a\nb\n",4)`, `read(2)==("b\n",4)`),
  `test_cancel_only_queued` (running → False, queued → True, state `cancelled`, `next()` skips it),
  `test_finish_states`, `test_log_file_written` (UTF-8, `ä` round-trips),
  `test_concurrent_writes` (4 threads × 1000 writes → 4000 lines), `test_keep_limit`.
- [ ] **Step 2:** run → FAIL. **Step 3:** implement with one `threading.Lock`.
- [ ] **Step 4:** PASS. **Step 5:** commit `fusion: job queue for the bridge`.

### Task 3: HTTP server

**Files:** Create `fusion/addin/DevToolsBridge/devtools_bridge/server.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: `JobQueue`, `Job.to_dict` (Task 2).
- Produces: `make_server(queue: JobQueue, token: str, notify: Callable[[], None],
  status: Callable[[], dict], host: str = "127.0.0.1", port: int = 7655) -> ThreadingHTTPServer`.
  `notify()` is called after every submit (the shell fires the custom event).
  Endpoints exactly as the spec, with codes: `POST /jobs` `{script|path, label}` → 202 `{"id"}`
  (400 if neither or both); `GET /jobs/<id>?since=N` → 200 `to_dict(N)` / 404;
  `DELETE /jobs/<id>` → 200 `{"cancelled": true}` / 409 / 404;
  `POST /screenshot` `{views, width=1600, height=1000, dir}` → 202 `{"id"}` (kind `shot`);
  `POST /reset` → 202 `{"id"}` (kind `reset`);
  `GET /health` → 200 `{"ok": true, **status(), **queue.snapshot()}`;
  any endpoint without the token → 401; unknown path → 404; bad JSON → 400.

- [ ] **Step 1: failing tests** (real server on port 0, `urllib` client, a fake
  pump thread that calls `queue.next()`/`finish()`): `test_auth_required_everywhere`,
  `test_submit_and_poll`, `test_submit_validation`, `test_cancel_codes`,
  `test_health_fields`, `test_notify_called_on_submit`, `test_screenshot_and_reset_kinds`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `fusion: bridge http endpoints`.

### Task 4: Executor session

**Files:** Create `fusion/addin/DevToolsBridge/devtools_bridge/executor.py`, `tests/test_executor.py`

**Interfaces:**
- Consumes: `JobQueue`, `Job` (Task 2).
- Produces: `class Session(base: Callable[[], dict], handlers: dict[str, Callable[[dict, dict], object]] | None = None, reload_dt: Callable[[], object | None] = lambda: None)`
  - `base()` returns the per-job fresh names (`adsk app ui design root`); they
    overwrite the namespace before each job. `reload_dt()` returns the `dt` module
    (or None) and is called before each job; a non-None result is put in as `dt`.
  - `step(queue: JobQueue) -> bool` — runs one unit of work on the calling (main)
    thread and returns True when more work is pending (caller re-fires the event).
    A pending generator is continued first; otherwise `queue.next()`.
  - Kind `script`: payload `script` (filename `<exec>`) or `path` (read UTF-8,
    compile with that filename, `__file__` set). Pop `run` from the namespace
    before exec; if the script defined `run`, call `run(None)`. If the exec'd
    code or `run` returns a generator, iterate it one `next()` per `step()`.
  - Kind `shot`: `handlers["shot"](namespace, payload)` returns a generator; its
    `StopIteration.value` is the job result.
  - Kind `reset`: namespace cleared, result `"reset"`.
  - During each step `sys.stdout` and `sys.stderr` write into the job; restored after.
  - Any `BaseException` → `finish(job, error=traceback text)`; the traceback is
    also written to the output.

- [ ] **Step 1: failing tests:** `test_print_captured`, `test_namespace_persists`,
  `test_reset_clears`, `test_run_called`, `test_stale_run_not_called`,
  `test_path_job_sets_file`, `test_utf8_script_and_output` (file with `# Größe` printing `Maß`),
  `test_exception_fails_job`, `test_base_exception_fails_job` (`sys.exit(3)` and
  `raise KeyboardInterrupt`, then a next job still runs), `test_generator_spans_steps`
  (a `run` that yields twice needs 3 steps; `step()` returns True until done),
  `test_base_refreshed_each_job`, `test_stdout_restored`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `fusion: executor session with generator jobs`.

### Task 5: Add-in shell (live)

**Files:** Create `fusion/addin/DevToolsBridge/DevToolsBridge.py`,
`DevToolsBridge.manifest` (id `DevToolsBridge`, `runOnStartup: true`, `supportedOS: "windows|mac"`),
`config.json` `{"port": 7655}`, `tools/dev_load.py`

**Interfaces:**
- Consumes: Tasks 2–4, `dt` (Task 6, imported lazily so the shell starts without it).
- Produces: Fusion `run(context)` / `stop(context)`. Custom event id
  `DevToolsBridgeEvent`; its handler calls `session.step(queue)` and re-fires
  while it returns True. `status()` returns `{"fusion": app.version, "document": <cached name or None>}`,
  the cache updated by `app.documentActivated` and at start. `reload_dt` imports
  `dt` from `fusion/` (found via `Path(__file__).resolve().parents[2]`) and
  re-imports when the newest mtime under `fusion/dt/` changed. Missing secret →
  generate `secrets.token_hex(32)` into `~/.fusion-mcp-secret`. No message box;
  log to `%TEMP%\devtools\fusion\bridge.log`.
  `tools/dev_load.py` runs inside Fusion through the old bridge: stops a loaded
  `DevToolsBridge` module if present, reloads its modules, calls `run(None)`.

- [ ] **Step 1:** `python -I C:\Crawler\Hardware\pv_robot_v1_0\snippets\run_script.py tools\dev_load.py` → prints `DevToolsBridge running on 7655`.
- [ ] **Step 2:** `curl -H "Authorization: Bearer $(cat ~/.fusion-mcp-secret)" http://127.0.0.1:7655/health` → `{"ok": true, "fusion": "2705…", …}`.
- [ ] **Step 3:** POST a job `print(design.rootComponent.name)` → poll → `done` with the root name.
- [ ] **Step 4:** commit `fusion: DevToolsBridge add-in shell`.

### Task 6: `dt` helpers (live)

**Files:** Create `fusion/dt/__init__.py`, `fusion/dt/core.py`, `fusion/dt/view.py`

**Interfaces:**
- Produces (all `design` args default to the active design):
  `mm(v_cm: float) -> float`, `cm(v_mm: float) -> float`;
  `bodies(design=None) -> list[dict]` keys `component body volume_mm3 bbox_mm` (bbox `(dx, dy, dz)`);
  `params(design=None) -> dict[str, str]` (user parameter → expression);
  `set_params(values: dict[str, str], design=None) -> None` (KeyError on an unknown name);
  `health(design=None) -> list[str]` (timeline items with errors/warnings, `"<name>: <message>"`);
  `info(design=None) -> str` (document, design type, units, bodies, params, health — compact);
  `VIEWS: dict[str, tuple[tuple, tuple]]` (the bridge's ten presets);
  `shot(views: list[str], width: int, height: int, out_dir: str)` — generator
  per Task 0's winning variant; per view: set camera, fit, yield, read the camera
  back (eye–target direction · requested ≥ 0.999), save `<out_dir>/<view>.png`;
  unknown view → ValueError; restores the original camera; returns `list[str]`;
  if two written images have the same SHA-256 → raises RuntimeError naming them.
  The shell registers `handlers["shot"] = lambda ns, p: dt.shot(p["views"], p["width"], p["height"], p["dir"])`.

- [ ] **Step 1:** in a scratch document with a 20×10×5 mm box: a job printing
  `dt.info()` shows that body with `volume_mm3` 1000.0 and bbox (20, 10, 5).
- [ ] **Step 2:** `set_params({"w": "30 mm"})` on a parameter-driven box → `bodies()` volume follows.
- [ ] **Step 3:** shot `iso-top-right front top` → three PNGs, each shows its view (Read them).
- [ ] **Step 4:** edit `core.py` (add a print), next job shows it without reloading the add-in.
- [ ] **Step 5:** commit `fusion: dt helpers and confirmed screenshots`.

### Task 7: CLI client and fusion commands

**Files:** Create `devtools/client.py`, `devtools/fusion.py`, `tests/test_cli.py`; modify `devtools/__main__.py`

**Interfaces:**
- Consumes: the HTTP API (Task 3); tests reuse `make_server` with the fake pump.
- Produces:
  - `client.py`: `class BridgeError(Exception)` with `exit_code` (2);
    `Unreachable(BridgeError)` 2, `AuthError(BridgeError)` 3;
    `load_port() -> int`, `load_token() -> str`;
    `class Client(port: int | None = None, token: str | None = None, host: str = "127.0.0.1", timeout: float = 10)`
    with `request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]`.
  - `fusion.py`: `add_parser(subparsers) -> None`;
    `follow(client: Client, job_id: str, wait_cap: float = 540, max_lines: int = 200, out=sys.stdout, poll: float = 0.5) -> int`.
    Commands: `health`, `info` (= exec `print(dt.info())`), `run FILE [--detach] [--wait-cap S] [--label L]`
    (path made absolute), `exec CODE`, `wait ID`, `log ID`, `cancel ID`,
    `shot VIEW... [--out DIR] [--size WxH]` (default dir `%TEMP%\devtools\fusion\shots\<job id>`,
    prints one path per line), `reset`, `selftest` (Task 9), `install` (Task 9).
    Messages go to stderr; script output to stdout. On exit 4 print
    `still running: devtools fusion wait <id>`.

- [ ] **Step 1: failing tests:** `test_unreachable_exit_2` (closed port, message names
  the port and "Add-Ins → DevToolsBridge"), `test_auth_exit_3`, `test_run_done_exit_0`,
  `test_run_failed_exit_1` (traceback on stdout), `test_wait_cap_exit_4`,
  `test_wait_reattaches`, `test_detach_prints_id`, `test_cancel`, `test_follow_job_vanishes`,
  `test_output_capped`, `test_port_precedence` (env > config.json > 7655), `test_run_path_absolute`.
- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** commit `cli: fusion commands`.

### Task 8: Knowledge

**Files:** Create `fusion/CLAUDE.md`, `fusion/docs/api-notes.md`

- [ ] **Step 1:** `api-notes.md` ← `C:\fusion360-mcp-bridge\CLAUDE.md` sections
  Units … Common construction planes (keep content, MIT notice at top), replace
  its "Viewport screenshots" and "Architecture" sections with this tooling.
- [ ] **Step 2:** `fusion/CLAUDE.md` ≤ 60 lines: the loop (health → info → run →
  info → shot → Read), commands and exit codes, units cm internally / `dt.mm`,
  `dt` list, write scripts as files, generator `yield` for long jobs that need
  Fusion to repaint, pointer to `docs/api-notes.md`.
- [ ] **Step 3:** commit `doc: fusion command card and api notes`.

### Task 9: Install and selftest (live)

**Files:** Create `devtools/install.py`, `devtools/selftest.py`, `tests/test_live.py`

**Interfaces:**
- Produces: `install(repo: Path, addins_dir: Path | None = None) -> int` —
  junction `%APPDATA%\Autodesk\Autodesk Fusion 360\API\AddIns\DevToolsBridge`
  → `repo/fusion/addin/DevToolsBridge` via `cmd /c mklink /J`; existing junction
  to the same target → "already installed", 0; anything else there → error, 1.
  `selftest(client: Client) -> int` — the spec's live checks 1–10, one
  `PASS|FAIL <name>` line each, exit 0 only if all pass; works in its own new
  documents and closes them with `close(False)`. `tests/test_live.py` runs it
  when `DEVTOOLS_LIVE=1`, else skips.

- [ ] **Step 1:** `devtools fusion install`; user starts `DevToolsBridge` in Add-Ins (or it starts with Fusion).
- [ ] **Step 2:** `devtools fusion selftest` → 10× PASS.
- [ ] **Step 3:** Crawler regression: with the user's assembly open, the output of
  `run_script.py verify fit()` (7654) equals `devtools fusion run` (7655).
- [ ] **Step 4:** commit `fusion: install and live selftest`.

### Task 10: Migration (each step confirmed with the user)

- [ ] User stops and unticks `FusionMCPBridge`.
- [ ] Crawler: `Hardware/pv_robot_v1_0/CLAUDE.md` import → `@C:/dev-tools/fusion/CLAUDE.md`,
  drop the toolchain note, retire `snippets/run_script.py`, update B8 — committed in Crawler.
- [ ] Allow rule `Bash(devtools fusion:*)` in user settings; `bin/` on the user PATH.
- [ ] Remove the `fusion` MCP server registration.
