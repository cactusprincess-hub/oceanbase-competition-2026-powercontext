import json


def main(body: str, command: str, status_code: int) -> dict:
    if status_code != 200:
        raise ValueError("线索读取失败，未执行行动；请检查数据服务")
    context = json.loads(body)
    state = context["state"]
    scenes = {scene["room"]: scene for scene in context["scenes"]}
    command = command.strip()
    message = ""
    if command in ("重来", "开始"):
        state = {"room": "大厅", "inventory": [], "turns": 0, "hints": 0, "status": "playing"}
        message = scenes["大厅"]["description"] + " 先试试『去站台』。"
    elif command in ("帮助", "状态", "背包"):
        message = "探索四个地点，查看并收集证据，回到大厅打开失物箱。帮助、状态、背包不消耗电量。"
    elif state["status"] != "playing":
        message = "这一局已结束。输入『重来』可开始新一局。"
    elif command == "提示":
        state["turns"] += 1
        state["hints"] += 1
        needed = [room for room in ("站台", "仓库", "广播室") if scenes[room]["clue_id"] not in state["inventory"]]
        message = "试着去" + needed[0] + "，然后『查看』。" if needed else "证据已齐，回大厅，按照值班记录组合密码。"
    elif command.startswith("去") and command[1:].strip() in scenes:
        state["room"] = command[1:].strip()
        state["turns"] += 1
        message = scenes[state["room"]]["description"]
    elif command == "查看":
        state["turns"] += 1
        scene = scenes[state["room"]]
        if scene["clue_id"] not in state["inventory"]:
            state["inventory"].append(scene["clue_id"])
        message = "【" + scene["clue_id"] + "】" + scene["clue_text"]
    elif command.startswith("打开"):
        if state["room"] != "大厅":
            message = "失物箱在大厅。输入『去大厅』返回。"
        else:
            state["turns"] += 1
            code = command[2:].strip()
            required = {scenes[room]["clue_id"] for room in ("站台", "仓库", "广播室")}
            if not required.issubset(set(state["inventory"])):
                message = "锁屏提示：证据不足。请先查看机械钟、旧票根与值班记录。"
            elif code != scenes["站台"]["fragment"] + scenes["仓库"]["fragment"]:
                message = "密码错误，失物箱轻轻闪了一下红灯。别把划掉的票号当成站台号。"
            else:
                state["status"] = "won"
                message = "失物箱打开了！里面是你的车票，还有一行字：『最后一班车，会等找回自己的人。』你成功回家。"
    else:
        message = "没听懂这条指令。请使用下方选项；无效指令不消耗电量。"
    if state["turns"] >= 12 and state["status"] == "playing":
        state["status"] = "lost"
        message += " 电量耗尽，天亮前无法开箱。输入『重来』再试一次。"
    score = max(0, 100 - state["turns"] * 3 - state["hints"] * 8) if state["status"] == "won" else 0
    evidence = [scene["clue_text"] for scene in context["scenes"] if scene["clue_id"] in state["inventory"]]
    reply = (message + "\n\n地点：" + state["room"] + " | 剩余行动：" + str(12 - state["turns"]) +
             " | 状态：" + state["status"] + " | 得分：" + str(score) +
             "\n证据背包：" + ("；".join(evidence) or "空") +
             "\n可用指令：去大厅 / 去站台 / 去仓库 / 去广播室 / 查看 / 打开 <密码> / 提示 / 背包 / 状态 / 重来")
    payload = {"session_id": context["session_id"], "version": context["version"], "state": state, "command": command, "reply": reply}
    return {"body": json.dumps(payload, ensure_ascii=False)}
