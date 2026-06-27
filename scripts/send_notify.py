#!/usr/bin/env python3
"""Server酱通知发送器 - Housing项目"""
import sys, json, urllib.request, os
from datetime import datetime, timezone, timedelta

SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "")
if not SENDKEY:
    sys.exit(0)

title_prefix = sys.argv[1] if len(sys.argv) > 1 else "房价周报"
summary_path = sys.argv[2] if len(sys.argv) > 2 else "data/summary.json"

beijing = datetime.now(timezone(timedelta(hours=8)))

html = "<p>no data</p>"
try:
    with open(summary_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    html = d.get("notify_html", html)
except Exception:
    pass

title = f"{title_prefix} ({beijing.strftime('%Y-%m-%d')})"
payload = json.dumps({"title": title, "desp": html}).encode("utf-8")

req = urllib.request.Request(
    f"https://sctapi.ftqq.com/{SENDKEY}.send",
    data=payload, headers={"Content-Type": "application/json"}, method="POST")
resp = json.loads(urllib.request.urlopen(req).read().decode())
print(f"Notify: code={resp.get('code')} pushid={resp.get('data',{}).get('pushid','?')}")
