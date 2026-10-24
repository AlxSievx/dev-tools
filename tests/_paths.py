"""Put the add-in folder on sys.path so its adsk-free modules import in tests."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADDIN = ROOT / "fusion" / "addin" / "DevToolsBridge"
if str(ADDIN) not in sys.path:
    sys.path.insert(0, str(ADDIN))
