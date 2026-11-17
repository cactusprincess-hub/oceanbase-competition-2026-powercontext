import json


def main(body: str, status_code: int) -> dict:
    result = json.loads(body)
    if status_code != 200 or not result.get("saved"):
        raise ValueError(result.get("error", "进度保存失败；请稍后重试"))
    return {"reply": result["reply"], "status": result["state"]["status"],
            "version": result["version"], "state": json.dumps(result["state"], ensure_ascii=False)}
