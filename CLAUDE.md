@README.md

## Layout

| Path | Holds |
|------|-------|
| `devtools/` | the CLI (`python -m devtools`, shims in `bin/`); standard library only |
| `fusion/addin/DevToolsBridge/` | the Fusion add-in; `jobs.py`, `server.py`, `executor.py` do not import `adsk` |
| `fusion/dt/` | helpers available as `dt` inside every Fusion job |
| `fusion/CLAUDE.md` | the command card a project imports to work in Fusion |
| `kicad/` | KiCad tooling, not started |
| `tools/` | development helpers: `dev_load.py` / `dev_stop.py` (re)load the add-in inside a running Fusion through the old bridge |
| `tests/` | `python -m unittest discover -s tests -v`; live tests need `DEVTOOLS_LIVE=1` and Fusion open |
| `docs/superpowers/` | design specs and implementation plans — planning lives here |

## Rules

- **Standard library only**, in the CLI and inside Fusion. Nothing gets pip-installed.
- **No fake results.** When Fusion or the bridge is unreachable, fail loudly
  with a non-zero exit code; never fall back to a mock.
- **General tooling only.** Building blocks that encode one project's style
  stay in that project.
- **Live tests never touch the user's documents**: they create their own and
  close them with `close(False)`.

## Commits

- **Commit when the user asks.**
- **Commit messages are short**: one subject line, a body only when the subject
  can't carry the point, and then two lines at most. No `Co-Authored-By` trailer.
  The subject starts with the area it touches: `fusion:` (`fusion/`), `kicad:`
  (`kicad/`), `cli:` (`devtools/`, `bin/`), `doc:` (READMEs, CLAUDE.md, specs,
  plans), or `treewide:` when it spans several areas,
  e.g. `fusion: screenshots confirm the camera before saving`.
