"""Run with sudo on Civo. Append only the new host; validate before graceful reload."""

import datetime
import os
import shutil
import subprocess
from pathlib import Path

config = Path("/etc/caddy/Caddyfile")
original = config.read_text()
route = (Path(__file__).parent / "caddy.snippet").read_text()
if "tools.opskoo.com" in original:
    raise SystemExit("tools.opskoo.com already has a route; inspect it instead of overwriting.")
candidate = config.with_name("Caddyfile.ekyc-lab.candidate")
candidate.write_text(original.rstrip() + "\n\n" + route)
candidate.chmod(0o644)
subprocess.run(
    ["/usr/bin/caddy", "validate", "--config", str(candidate), "--adapter", "caddyfile"], check=True
)
if config.read_text() != original:
    raise SystemExit("Caddyfile changed during validation; no active configuration modified.")
stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
backup = Path(f"/etc/caddy/backups/Caddyfile.before-ekyc-lab-{stamp}")
backup.parent.mkdir(exist_ok=True)
shutil.copy2(config, backup)
os.replace(candidate, config)
try:
    subprocess.run(["systemctl", "reload", "caddy"], check=True)
except subprocess.CalledProcessError:
    shutil.copy2(backup, config)
    subprocess.run(["systemctl", "reload", "caddy"], check=True)
    raise
print(f"Added tools.opskoo.com only. Backup: {backup}")
