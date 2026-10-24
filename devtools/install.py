"""`devtools fusion install` — link the add-in from this repo into Fusion."""
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
NAME = "DevToolsBridge"


def default_addins_dir() -> Path:
    return Path(os.environ["APPDATA"]) / "Autodesk" / "Autodesk Fusion 360" / "API" / "AddIns"


def install(repo: Path = REPO, addins_dir: Path | None = None) -> int:
    target = (repo / "fusion" / "addin" / NAME).resolve()
    link = (addins_dir or default_addins_dir()) / NAME
    if link.exists() or link.is_junction():
        if os.path.realpath(link) == str(target):
            print(f"already installed: {link} → {target}")
            return 0
        print(f"{link} exists and is not a link to {target}; move it away first")
        return 1
    link.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, encoding="oem", errors="replace")  # cmd speaks the console code page
    if r.returncode != 0:
        print(f"mklink failed: {r.stdout}{r.stderr}")
        return 1
    print(f"linked {link} → {target}")
    print("In Fusion: Utilities → Add-Ins → DevToolsBridge → Run, and tick Run on Startup.")
    return 0
