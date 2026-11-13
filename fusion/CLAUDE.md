# Fusion through dev-tools — command card

Claude drives Fusion with `devtools fusion …` (shell; `C:\dev-tools\bin` on PATH).
Scripts run inside Fusion as **jobs** on its main thread, one at a time.

## The loop

1. `devtools fusion health` — bridge up? which document is active?
2. `devtools fusion info` — the design in numbers: bodies (volume mm³, bbox mm and
   where it starts), user parameters, timeline errors, clashes between bodies.
   Read it **before** changing anything.
3. Write the change as a **script file** and `devtools fusion run file.py`.
   One-liners: `devtools fusion exec "dt.bodies()"` (a trailing expression is printed).
4. **Measure** the result: `info` again, `devtools fusion inspect BODY` for holes,
   bosses, mass and faces, `dt.gap(a, b)` for clearances. Compare with what was
   intended — a bounding box does not show a hole in the wrong place.
5. **Look**: `devtools fusion shot iso-top-right front top` prints PNG paths; open
   them with Read. Hidden insides (bores, pockets, walls): `--section y=10` and a
   view from the cut side (`back` for y).
6. Fix and repeat. Numbers decide; pictures confirm.

A failed job ends with a `note:` saying what it left behind (new timeline items,
open documents) — clean up with `dt.trim(N)` before running the fixed script.

## Commands

| Command | Does |
|---------|------|
| `run FILE [--detach] [--wait-cap S] [--label L]` | run a script file |
| `exec CODE` | run a line or two |
| `info` | `print(dt.info())` |
| `inspect BODY` | `print(dt.inspect(BODY))`: size, place, mass, faces, holes/bosses |
| `shot VIEW… [--out DIR] [--size WxH] [--section AXIS=MM [--flip]]` | `front back left right top bottom iso-top-right iso-top-left iso-bottom-right iso-bottom-left current` |
| `wait ID` · `log ID` · `cancel ID` | follow again · output so far · drop a queued job |
| `reset` | forget the names kept between jobs |

Exit codes: **0** done · **1** script failed (traceback printed) · **2** bridge
unreachable or job unknown · **3** token refused · **4** still running after the
wait cap (9 min; Bash stops at 10) — `devtools fusion wait <id>` · **5** usage.
Output is capped at 200 lines; the rest is in the job's log, whose path is printed.
Shots default to 1024x640 (each image costs context); ask for `--size 1600x1000`
only to read fine detail, and only the views that answer the question.

## Inside a job

- Already there: `adsk`, `app`, `ui`, `design` (active design or `None`), `root`, `dt`.
  `design`/`root` are refreshed before every job; your own names persist.
- `run(context)` is called if the script defines it. A **generator** `run` hands
  control back to Fusion at each `yield` — use it in long loops so Fusion repaints.
- **Fusion's internal unit is cm.** `dt.mm(x)` / `dt.cm(x)`; in `ValueInput`s pass
  strings with units: `"12 mm"`, `"45 deg"`, or a parameter name.
- `dt.bodies()`, `dt.params()`, `dt.set_params({"w": "30 mm"})`, `dt.health()`,
  `dt.info()`, `dt.shot(views, w, h, dir, section=None, flip=False)`,
  `dt.trim(n)`; `dt` reloads when its files change.
- Measuring (all mm, assembly coordinates): `dt.body(name)` finds a body by
  `name`, `component/body` or `occurrence/body`; `dt.holes(b)` lists cylinders
  (hole/boss/arc, ⌀, axis, position, span); `dt.gap(a, b)`; `dt.clashes()`;
  `dt.mass(b)`; `dt.inspect(b)`. Name bodies (`body.name = "lid"`) so checks read well.

## Rules

- Non-trivial scripts are files, kept in the project (e.g. its `snippets/`),
  not pasted into `exec`.
- Check `sk.profiles.count` before `profiles.item(0)`; a revolve profile must
  stay on one side of its axis.
- Never save or close the user's documents unless asked; experiment in a new
  document and close it with `doc.close(False)`.

Fusion API pitfalls and patterns: [`docs/api-notes.md`](docs/api-notes.md).
