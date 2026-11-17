"""Local Dify 1.9.1 deployment client (stdlib only)."""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def env():
    values = dict(os.environ)
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line and not line.startswith("#"):
                key, value = line.split("=", 1)
                values.setdefault(key, value)
    return values


def request(url, payload=None, token=None, method=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=150) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}: {error.read().decode()[:2000]}") from None


def wait(url):
    for _ in range(150):
        try:
            return request(url)
        except (OSError, RuntimeError):
            time.sleep(2)
    raise RuntimeError("Service did not become ready: " + url)


def run(command, session_id, runtime=None):
    runtime = runtime or json.loads((ROOT / ".runtime.json").read_text())
    result = request(runtime["api_url"] + "/v1/workflows/run", {
        "inputs": {"session_id": session_id, "command": command}, "response_mode": "blocking", "user": session_id
    }, runtime["app_key"])
    if result["data"]["status"] != "succeeded":
        raise RuntimeError(json.dumps(result, ensure_ascii=False))
    return result
