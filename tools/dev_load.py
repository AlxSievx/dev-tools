"""(Re)load DevToolsBridge from this repo inside a running Fusion — development only.

Runs inside Fusion through the old bridge:
    python -I C:\Crawler\Hardware\pv_robot_v1_0\snippets\run_script.py tools\dev_load.py
Stops a dev-loaded instance, drops its modules and `dt`, loads the add-in
fresh and starts it. `tools/dev_stop.py` only stops it.
"""
import importlib.util
import sys

ADDIN = r"C:\dev-tools\fusion\addin\DevToolsBridge\DevToolsBridge.py"
NAME = "DevToolsBridge_dev"

old = sys.modules.get(NAME)
if old:
    old.stop(None)
    print("stopped the dev instance")
for m in [m for m in sys.modules if m in (NAME, "dt") or m.startswith(("devtools_bridge", "dt."))]:
    del sys.modules[m]

if not globals().get("STOP_ONLY"):
    spec = importlib.util.spec_from_file_location(NAME, ADDIN)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[NAME] = mod
    spec.loader.exec_module(mod)
    mod.run(None)
    print(f"DevToolsBridge running on {mod._port()}" if mod._server else "start failed, see bridge.log")
