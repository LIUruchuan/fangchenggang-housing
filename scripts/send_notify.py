#!/usr/bin/env python3
"""Server酱通知发送器 - Housing项目（Markdown格式，微信可渲染）"""
import sys, json, urllib.request, os
from datetime import datetime, timezone, timedelta

SENDKEY = os.environ.get("SERVERCHAN_SENDKEY", "")
if not SENDKEY:
    print("No SERVERCHAN_SENDKEY, skip notification.")
    sys.exit(0)

title_prefix = sys.argv[1] if len(sys.argv) > 1 else "房价周报"
summary_path = sys.argv[2] if len(sys.argv) > 2 else "data/summary.json"

beijing = datetime.now(timezone(timedelta(hours=8)))

# 读取通知内容（优先 Markdown notify_text，微信可渲染链接和格式）
text = "no data"
try:
    with open(summary_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    # 优先 Markdown，回退 HTML
    text = d.get("notify_text") or d.get("notify_html") or text
    if not text.strip():
        print("[WARN] Empty notify content, skip notification.")
        sys.exit(0)
except Exception as e:
    print(f"[WARN] Failed to read {summary_path}: {e}")
    sys.exit(1)

title = f"{title_prefix} ({beijing.strftime('%Y-%m-%d')})"
payload = json.dumps({"title": title, "desp": text}).encode("utf-8")

try:
    req = urllib.request.Request(
        f"https://sctapi.ftqq.com/{SENDKEY}.send",
        data=payload, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    resp = json.loads(urllib.request.urlopen(req).read().decode())
    if resp.get("code") == 0:
        print(f"Server酱 notified: pushid={resp.get('data',{}).get('pushid','?')}")
    else:
        print(f"Server酱 failed: code={resp.get('code')} msg={resp.get('message','?')}")
        sys.exit(1)
except Exception as e:
    print(f"[ERROR] HTTP request failed: {e}")
    sys.exit(1)