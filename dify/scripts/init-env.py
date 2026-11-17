"""Initialize a new, isolated local deployment. Never overwrite existing secrets."""
from pathlib import Path
import secrets

root = Path(__file__).resolve().parents[1]
path = root / ".env"
if path.exists():
    print(".env already exists; retained existing local configuration")
else:
    keys = ["DIFY_SECRET", "DB_PASSWORD", "REDIS_PASSWORD", "SANDBOX_KEY", "PLUGIN_KEY", "PLUGIN_INNER_KEY", "SEEKDB_PASSWORD", "GAME_TOKEN", "DIFY_ADMIN_PASSWORD"]
    with path.open("x") as out:
        out.write("".join(key + "=Game1" + secrets.token_hex(24) + "\n" for key in keys))
    path.chmod(0o600)
    print("Generated local .env; credentials remain on this machine")
