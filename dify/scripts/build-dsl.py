"""Build the importable Dify DSL; JSON is also valid YAML."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
nodes, edges = [], []


def node(id, kind, title, **data):
    pos = {"x": 50 + len(nodes) * 310, "y": 180}
    nodes.append({"id": id, "type": "custom", "data": {"type": kind, "title": title, "desc": "", "selected": False, **data},
                  "position": pos, "positionAbsolute": pos, "height": 100, "width": 244,
                  "sourcePosition": "right", "targetPosition": "left", "selected": False})
    if len(nodes) > 1:
        previous = nodes[-2]
        edges.append({"id": previous["id"] + "-" + id, "source": previous["id"], "target": id,
                      "sourceHandle": "source", "targetHandle": "target", "type": "custom", "zIndex": 0,
                      "data": {"sourceType": previous["data"]["type"], "targetType": kind, "isInIteration": False, "isInLoop": False}})


def code(id, title, filename, inputs, outputs):
    node(id, "code", title, code_language="python3", code=(root / "workflow" / filename).read_text(),
         variables=[{"variable": name, "value_selector": selector} for name, selector in inputs.items()],
         outputs={name: {"type": kind, "children": None} for name, kind in outputs.items()})


def http(id, title, route, source):
    node(id, "http-request", title, method="POST", url="{{#env.game_url#}}/" + route,
         authorization={"type": "no-auth"}, headers="Content-Type: application/json\nAuthorization: Bearer {{#env.game_token#}}",
         params="", body={"type": "raw-text", "data": [{"type": "text", "text": "{{#" + source + ".body#}}", "key": "", "id": "body"}]},
         timeout={"connect": 10, "read": 30, "write": 30},
         retry_config={"retry_enabled": False, "max_retries": 1, "retry_interval": 1000})


node("start", "start", "玩家输入", variables=[
    {"variable": key, "label": label, "type": "text-input", "required": True, "max_length": length, "options": []}
    for key, label, length in [("session_id", "存档ID（32位小写十六进制）", 32), ("command", "命令，例如：开始、去站台、查看", 100)]])
code("prepare", "校验输入与构造请求", "prepare.py", {"session_id": ["start", "session_id"], "command": ["start", "command"]}, {"body": "string", "command": "string"})
http("context", "seekdb读取存档与场景证据", "context", "prepare")
code("game", "行动判断、线索组合与胜负", "game.py", {"body": ["context", "body"], "status_code": ["context", "status_code"], "command": ["prepare", "command"]}, {"body": "string"})
http("save", "seekdb事务保存进度与行动记录", "commit", "game")
code("confirm", "确认持久化结果", "confirm.py", {"body": ["save", "body"], "status_code": ["save", "status_code"]}, {"reply": "string", "status": "string", "version": "number", "state": "string"})
node("end", "end", "返回故事与下一步选项", outputs=[{"variable": key, "value_selector": ["confirm", key], "value_type": kind} for key, kind in [("reply", "string"), ("status", "string"), ("version", "number"), ("state", "string")]])
app = {"version": "0.3.0", "kind": "app", "dependencies": [],
       "app": {"name": "午夜失物招领", "description": "12次行动内解开午夜车站的失物箱。Dify负责游戏规则，seekdb保存证据与跨次进度，无需模型Key。", "icon": "🌙", "icon_type": "emoji", "icon_background": "#E8E5FF", "mode": "workflow", "use_icon_as_answer_icon": False},
       "workflow": {"conversation_variables": [], "environment_variables": [
           {"id": "c4e83210-67ba-493f-91f0-000000000001", "name": "game_url", "value": "http://game:3100", "value_type": "string", "description": "seekdb数据服务地址", "selector": ["env", "game_url"]},
           {"id": "c4e83210-67ba-493f-91f0-000000000002", "name": "game_token", "value": "", "value_type": "secret", "description": "填写本地GAME_TOKEN", "selector": ["env", "game_token"]}],
           "features": {"file_upload": {"enabled": False}, "opening_statement": "", "retriever_resource": {"enabled": False}, "sensitive_word_avoidance": {"enabled": False}, "speech_to_text": {"enabled": False}, "suggested_questions": [], "suggested_questions_after_answer": {"enabled": False}, "text_to_speech": {"enabled": False}},
           "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.65}}}}
(root / "midnight.yml").write_text(json.dumps(app, ensure_ascii=False, indent=2) + "\n")
print("Generated dify/midnight.yml (YAML 1.2 / JSON)")
