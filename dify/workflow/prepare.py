import json
import re


def main(session_id: str, command: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise ValueError("session_id 必须为32位小写十六进制字符串；请先生成独立的游戏存档编号")
    if not command.strip() or len(command) > 100:
        raise ValueError("命令不能为空且不能超过100个字符")
    return {"body": json.dumps({"session_id": session_id}), "command": command.strip()}
