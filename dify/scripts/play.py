"""Interactive client for the published Dify workflow."""
import json
import secrets
import sys
from client import run

session = sys.argv[1] if len(sys.argv) > 1 else secrets.token_hex(16)
print("存档ID：" + session + "（下次可用 python3 scripts/play.py " + session + " 继续）")
print(run("状态", session)["data"]["outputs"]["reply"])
while True:
    try:
        command = input("\n行动（退出：exit）：").strip()
        if command == "exit":
            break
        print(run(command, session)["data"]["outputs"]["reply"])
    except (EOFError, KeyboardInterrupt):
        break
    except Exception as error:
        print(str(error))
