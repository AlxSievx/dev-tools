# Fusion through dev-tools — command card

Claude drives Fusion with `devtools fusion …` (shell; `C:\dev-tools\bin` on PATH).
Scripts run inside Fusion as **jobs** on its main thread, one at a time.

## The loop

1. `devtools fusion health` — bridge up? which document is active?
2. `devtools fusion info` — the design in numbers: bodies (volume mm³, bbox mm),
   user parameters, timeline errors. Read it **before** changing anything.
3. Write the change as a **script file** and `devtools fusion run file.py`.
   One-liners: `devtools fusion exec "dt.bodies()"` (a trailing expression is printed).
4. `devtools fusion info` again, or a `dt` check — **measure** the result.
5. `devtools fusion shot iso-top-right front top` — prints PNG paths; open them
   with Read to **look**.
6. Fix and repeat. Numbers decide; pictures confirm.

## Commands

| Command | Does |
|---------|------|
| `run FILE [--detach] [--wait-cap S] [--label L]` | run a script file |
| `exec CODE` | run a line or two |
| `info` | `print(dt.info())` |
| `shot VIEW… [--out DIR] [--size WxH]` | `front back left right top bottom iso-top-right iso-top-left iso-bottom-right iso-bottom-left current` |
| `wait ID` · `log ID` · `cancel ID` | follow again · output so far · drop a queued job |
| `reset` | forget the names kept between jobs |

Exit codes: **0** done · **1** script failed (traceback printed) · **2** bridge
unreachable or job unknown · **3** token refused · **4** still running after the
wait cap (9 min; Bash stops at 10) — `devtools fusion wait <id>` · **5** usage.
Output is capped at 200 lines; the rest is in the job's log, whose path is printed.

## Inside a job

- Already there: `adsk`, `app`, `ui`, `design` (active design or `None`), `root`, `dt`.
  `design`/`root` are refreshed before every job; your own names persist.
- `run(context)` is called if the script defines it. A **generator** `run` hands
  control back to Fusion at each `yield` — use it in long loops so Fusion repaints.
- **Fusion's internal unit is cm.** `dt.mm(x)` / `dt.cm(x)`; in `ValueInput`s pass
  strings with units: `"12 mm"`, `"45 deg"`, or a parameter name.
- `dt.bodies()`, `dt.params()`, `dt.set_params({"w": "30 mm"})`, `dt.health()`,
  `dt.info()`, `dt.shot(views, w, h, dir)`; `dt` reloads when its files change.

## Rules

- Non-trivial scripts are files, kept in the project (e.g. its `snippets/`),
  not pasted into `exec`.
- Check `sk.profiles.count` before `profiles.item(0)`; a revolve profile must
  stay on one side of its axis.
- Never save or close the user's documents unless asked; experiment in a new
  document and close it with `doc.close(False)`.

Fusion API pitfalls and patterns: [`docs/api-notes.md`](docs/api-notes.md).
