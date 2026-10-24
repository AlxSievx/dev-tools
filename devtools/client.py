"""HTTP client for the DevToolsBridge add-in. Standard library only."""
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG = REPO / "fusion" / "addin" / "DevToolsBridge" / "config.json"
DEFAULT_PORT = 7655
SECRET_FILE = Path.home() / ".fusion-mcp-secret"


class BridgeError(Exception):
    exit_code = 2


class Unreachable(BridgeError):
    exit_code = 2


class AuthError(BridgeError):
    exit_code = 3


def load_port() -> int:
    if os.environ.get("DEVTOOLS_FUSION_PORT"):
        return int(os.environ["DEVTOOLS_FUSION_PORT"])
    try:
        return int(json.loads(CONFIG.read_text(encoding="utf-8"))["port"])
    except (OSError, ValueError, KeyError):
        return DEFAULT_PORT


def load_token() -> str:
    path = Path(os.environ.get("DEVTOOLS_FUSION_SECRET_FILE") or SECRET_FILE)
    try:
        return path.read_text().strip()
    except OSError:
        raise AuthError(f"no token file {path}; start DevToolsBridge in Fusion once, it creates it")


class Client:
    def __init__(self, port: int | None = None, token: str | None = None,
                 host: str = "127.0.0.1", timeout: float = 10):
        self.port = port or load_port()
        self.token = token if token is not None else load_token()
        self.host = host
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://{self.host}:{self.port}{path}", data=data,
                                     method=method)
        req.add_header("Authorization", f"Bearer {self.token}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            with e:
                payload = json.loads(e.read() or b"{}")
            if e.code == 401:
                raise AuthError(f"the bridge refused the token from {SECRET_FILE} "
                                "(or DEVTOOLS_FUSION_SECRET_FILE)")
            return e.code, payload
        except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
            raise Unreachable(
                f"cannot reach DevToolsBridge on {self.host}:{self.port} ({getattr(e, 'reason', e)}). "
                "Is Fusion open and the add-in running? Utilities → Add-Ins → DevToolsBridge → Run")
