"""Initialize only this project's local Dify, import DSL, publish, and keep keys local."""
import json
from pathlib import Path
from client import ROOT, env, request, wait

values = env()
base = "http://127.0.0.1:5001"
wait(base + "/health")
wait("http://127.0.0.1:3100/health")
setup = request(base + "/console/api/setup")
if setup["step"] == "not_started":
    request(base + "/console/api/setup", {"email": "player@example.com", "name": "Midnight Player", "password": values["DIFY_ADMIN_PASSWORD"]})
login = request(base + "/console/api/login", {"email": "player@example.com", "password": values["DIFY_ADMIN_PASSWORD"]})
token = login["data"]["access_token"]
dsl = json.loads((ROOT / "midnight.yml").read_text())
for variable in dsl["workflow"]["environment_variables"]:
    if variable["name"] == "game_token":
        variable["value"] = values["GAME_TOKEN"]
result = request(base + "/console/api/apps/imports", {"mode": "yaml-content", "yaml_content": json.dumps(dsl, ensure_ascii=False)}, token)
if result["status"] == "pending":
    result = request(base + "/console/api/apps/imports/" + result["id"] + "/confirm", {}, token)
if result["status"] != "completed":
    raise RuntimeError(json.dumps(result, ensure_ascii=False))
app = result["app_id"]
request(base + f"/console/api/apps/{app}/workflows/publish", {"marked_name": "midnight-game", "marked_comment": "seekdb persistent puzzle"}, token)
key = request(base + f"/console/api/apps/{app}/api-keys", {}, token)["token"]
with (ROOT / ".runtime.json").open("w") as out:
    json.dump({"api_url": base, "app_id": app, "app_key": key, "console_token": token}, out)
(ROOT / ".runtime.json").chmod(0o600)
print("Imported and published 午夜失物招领; API credentials saved only in .runtime.json")
print("Dify UI: http://localhost:8080 ; login: player@example.com (password in local .env)")
