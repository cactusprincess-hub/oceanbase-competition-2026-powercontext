"""Run the actual published Dify workflow against actual seekdb, including restart."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from client import ROOT, env, request, run, wait

results = {"environment": {"dify": "1.9.1", "seekdb": "1.4.0", "engine": "Dify published workflow (not a mock)"}, "scenarios": []}
runtime = json.loads((ROOT / ".runtime.json").read_text())


def action(command, session):
    response = run(command, session)
    record = {"session_id": session, "command": command, "workflow_run_id": response["workflow_run_id"],
              "status": response["data"]["status"], "outputs": response["data"]["outputs"]}
    results["scenarios"].append(record)
    print(command + " => " + record["outputs"]["status"], flush=True)
    return record["outputs"]


def db(source):
    # Operates only on the isolated compose deployment created for this app.
    script = "import {Store} from './server/store.mjs'; const store = new Store(); try {" + source + "} finally {await store.close();}"
    result = subprocess.run(["docker", "compose", "exec", "-T", "game", "node", "--input-type=module", "-e", script], cwd=ROOT, check=True, capture_output=True, text=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


session = secrets.token_hex(16)
walkthrough = ["开始", "去站台", "查看", "去仓库", "查看", "去广播室", "查看", "去大厅"]
for command in walkthrough:
    action(command, session)
wrong = action("打开 000000", session)
assert wrong["status"] == "playing" and "密码错误" in wrong["reply"]
won = action("打开 231704", session)
assert won["status"] == "won" and "成功回家" in won["reply"]
assert json.loads(won["state"])["turns"] == 9

# Already gathered evidence must survive both application and database process restarts.
subprocess.run(["docker", "compose", "restart", "seekdb", "game"], cwd=ROOT, check=True)
wait("http://127.0.0.1:3100/health")
restored = action("状态", session)
assert restored["state"] == won["state"]
results["restart_recovered"] = True

other = secrets.token_hex(16)
action("开始", other)
missing = action("打开 231704", other)
assert missing["status"] == "playing" and "证据不足" in missing["reply"]
for _ in range(11):
    lost = action("查看", other)
assert lost["status"] == "lost"
reset = action("重来", other)
assert json.loads(reset["state"])["turns"] == 0
assert action("什么", other)["state"] == reset["state"]
assert action("提示", other)["status"] == "playing"

# Show that changing the seekdb fixture affects actual Dify puzzle decisions.
try:
    db("await store.pool.execute(\"UPDATE midnight_game.scenes SET fragment=?, clue_text=? WHERE clue_id='ticket'\", ['09', '旧票根：站台编号09（验收数据变更）']);")
    changed = secrets.token_hex(16)
    for command in walkthrough:
        action(command, changed)
    old_code = action("打开 231704", changed)
    assert "密码错误" in old_code["reply"]
    new_code = action("打开 231709", changed)
    assert new_code["status"] == "won"
    results["database_clue_changes_outcome"] = True
finally:
    db("await store.pool.execute(\"UPDATE midnight_game.scenes SET fragment=?, clue_text=? WHERE clue_id='ticket'\", ['04', '旧票根上保留着『站台编号：04』；票号 9852 已被划掉。']);")

results["persisted_players"] = db("const [rows] = await store.pool.query('SELECT session_id,version,state_json FROM midnight_game.players ORDER BY session_id'); console.log(JSON.stringify(rows));")
results["history_counts"] = db("const [rows] = await store.pool.query('SELECT session_id, COUNT(*) AS actions FROM midnight_game.history GROUP BY session_id'); console.log(JSON.stringify(rows));")
# Keep node status evidence, not HTTP authorization headers or secret inputs.
last = results["scenarios"][-1]["workflow_run_id"]
node_data = request(runtime["api_url"] + f'/console/api/apps/{runtime["app_id"]}/workflow-runs/{last}/node-executions', token=runtime["console_token"])
results["dify_nodes"] = [{key: node.get(key) for key in ("node_id", "node_type", "title", "status", "elapsed_time", "error")} for node in node_data["data"]]
assert len(results["dify_nodes"]) == 7
assert all(node["status"] == "succeeded" for node in results["dify_nodes"])
exported = request(runtime["api_url"] + f'/console/api/apps/{runtime["app_id"]}/export', token=runtime["console_token"])["data"]
for key, value in env().items():
    if key in ("GAME_TOKEN", "DIFY_ADMIN_PASSWORD", "DB_PASSWORD", "DIFY_SECRET", "PLUGIN_KEY", "PLUGIN_INNER_KEY", "REDIS_PASSWORD", "SANDBOX_KEY", "SEEKDB_PASSWORD"):
        exported = exported.replace(value, "")
(ROOT / "results" / "dify-export.yml").write_text(exported)
results["passed"] = True
results["github_run"] = os.environ.get("GITHUB_RUN_ID", "local")
(ROOT / "results" / "acceptance.json").write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n")
print("PASS: real Dify, full story, failure/reset, seekdb restart, database clue mutation, seven core nodes", flush=True)
