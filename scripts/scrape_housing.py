#!/usr/bin/env python3
"""
防城港锦泰现代城房价爬虫
数据源: 安居客（主） + 链家/贝壳（辅助）
"""

import os
import sys
import re
import json
import csv
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ---------- 配置 ----------
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
DATA_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

HISTORY_CSV = DATA_DIR / "history.csv"
TREND_CSV = DATA_DIR / "trend_history.csv"          # 🆕 趋势分析累积表
SCRAPE_LOG_CSV = DATA_DIR / "scrape_log.csv"         # 🆕 数据质量追踪表

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# 安居客小区页面
ANJUKE_URL = "https://fangchenggang.anjuke.com/community/view/1125952"
# 链家/贝壳文章页
LIANJIA_URL = "https://news.lianjia.com/fcg/xiaoqu/8909132900874515.html"
# 链家成交记录 API（贝壳 RTC API）
LIANJIA_TRANSACTION_API = "https://fcg.ke.com/api/xiaoqu/ershoufang/xiaoquchengjiao/query"
LIANJIA_XIAOQU_ID = "8909132900874515"

# 小区信息
COMMUNITY_NAME = "锦泰现代城"
CITY = "防城港"


def fetch_anjuke(session: requests.Session) -> dict:
    """抓取安居客数据"""
    result = {"source": "anjuke", "avg_price": None, "price_change": None, "listing_count": None}
    try:
        resp = session.get(ANJUKE_URL, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            # 提取均价（常见在 .price-txt 或 text 中包含 元/平米）
            text = soup.get_text()
            # 均价: NNNN元/㎡ 或 NNNN元/平米
            m = re.search(r"(\d{3,5})\s*元/[㎡平米]", text)
            if m:
                result["avg_price"] = int(m.group(1))
            # 比上月变化
            m2 = re.search(r"(?:比上月|环比)[^\d]*([+-]?\d+\.?\d*)%", text)
            if m2:
                result["price_change"] = float(m2.group(1))
            # 在租/在售房源数
            m3 = re.search(r"在(?:租|售)房源[:\s]*(\d+)", text)
            if m3:
                result["listing_count"] = int(m3.group(1))
    except Exception as e:
        print(f"[WARN] 安居客抓取失败: {e}")
    return result


def fetch_lianjia(session: requests.Session) -> dict:
    """抓取链家/贝壳数据（交叉验证）"""
    result = {"source": "lianjia", "avg_price": None, "last_month_deals": None, "listing_count": 0}
    try:
        resp = session.get(LIANJIA_URL, headers=HEADERS, timeout=15)
        if resp.status_code == 200:
            text = resp.text
            # 均价
            m = re.search(r"参考成交均价\s*(\d{3,5})\s*元", text)
            if m:
                result["avg_price"] = int(m.group(1))
            # 上月成交
            m2 = re.search(r"上月成交\s*(\d+)\s*套", text)
            if m2:
                result["last_month_deals"] = int(m2.group(1))
            # 在售房源
            m3 = re.search(r"目前在售房源\s*(\d+)\s*套", text)
            if m3:
                result["listing_count"] = int(m3.group(1))
    except Exception as e:
        print(f"[WARN] 链家抓取失败: {e}")
    return result


def fetch_transactions(session: requests.Session) -> dict:
    """抓取链家/贝壳成交记录"""
    result = {"avg_price": None, "count": 0, "prices": []}
    try:
        resp = session.get(
            LIANJIA_TRANSACTION_API,
            params={
                "community_id": LIANJIA_XIAOQU_ID,
                "limit": 10,
                "offset": 0,
            },
            headers={**HEADERS, "Referer": "https://fcg.ke.com/xiaoqu/"},
            timeout=15,
        )
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("data", {}).get("list", [])
            if items:
                prices = []
                for item in items:
                    # unit_price 是成交单价
                    up = item.get("unit_price")
                    if up:
                        prices.append(float(up))
                if prices:
                    result["prices"] = prices
                    result["avg_price"] = round(sum(prices) / len(prices))
                    result["count"] = len(prices)

                # 也可能在 data.total 直接有汇总价
                summary = data.get("data", {}).get("summary", {})
                if summary.get("avg_price"):
                    result["avg_price"] = round(float(summary["avg_price"]))
                if summary.get("total"):
                    result["count"] = int(summary["total"])
    except Exception as e:
        print(f"[WARN] 成交记录抓取失败: {e}")
    return result


def update_history_csv(anjuke: dict, lianjia: dict, transaction: dict = None):
    """追加一行到 history.csv"""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")
    week_str = now.strftime("%Y-W%W")

    # 读取已有数据检查是否重复
    if HISTORY_CSV.exists():
        with open(HISTORY_CSV, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) > 1 and lines[-1].startswith(date_str):
                print(f"[INFO] {date_str} 已有记录，跳过追加。")
                return

    row = [
        date_str,
        week_str,
        anjuke.get("avg_price", "") or "",
        lianjia.get("avg_price", ""),
        anjuke.get("price_change", ""),
        lianjia.get("last_month_deals", ""),
        anjuke.get("listing_count", ""),
        lianjia.get("listing_count", ""),
        transaction.get("avg_price", "") if transaction else "",
        transaction.get("count", "") if transaction else "",
    ]

    is_new = not HISTORY_CSV.exists()
    with open(HISTORY_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow([
                "date", "week", "anjuke_avg_price", "lianjia_avg_price",
                "anjuke_price_change_pct", "lianjia_last_month_deals",
                "anjuke_listing_count", "lianjia_listing_count",
                "transaction_avg_price", "transaction_count"
            ])
        writer.writerow(row)

    print(f"[SAVED] history.csv 追加: {date_str}")


def generate_report(anjuke: dict, lianjia: dict) -> str:
    """生成周报 Markdown"""
    beijing = datetime.now(timezone(timedelta(hours=8)))
    date_str = beijing.strftime("%Y-%m-%d")

    # 读取历史趋势
    history = []
    if HISTORY_CSV.exists():
        with open(HISTORY_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            history = list(reader)

    lines = [f"# 🏠 {CITY}·{COMMUNITY_NAME} 房价周报"]
    lines.append(f"")
    lines.append(f"**日期**: {date_str}")
    lines.append(f"**数据源**: 安居客 + 链家")
    lines.append(f"")
    lines.append("---")
    lines.append(f"")

    # 关键指标
    lines.append("## 📊 关键指标")
    lines.append("")
    lines.append("| 指标 | 安居客 | 链家 |")
    lines.append("|------|--------|------|")

    a_price = f"{anjuke.get('avg_price')}元/m²" if anjuke.get('avg_price') else "N/A"
    l_price = f"{lianjia.get('avg_price')}元/m²" if lianjia.get('avg_price') else "N/A"
    lines.append(f"| 均价 | {a_price} | {l_price} |")

    a_change = f"{anjuke.get('price_change', '+')}%" if anjuke.get('price_change') else "N/A"
    lines.append(f"| 环比变化 | {a_change} | - |")

    a_list = str(anjuke.get('listing_count', '')) if anjuke.get('listing_count') else "N/A"
    l_list = str(lianjia.get('listing_count', '')) if lianjia.get('listing_count') else "N/A"
    lines.append(f"| 在售/在租 | {a_list} | {l_list} |")

    l_deals = str(lianjia.get('last_month_deals', '')) if lianjia.get('last_month_deals') else "N/A"
    lines.append(f"| 上月成交 | - | {l_deals} 套 |")

    lines.append("")

    # 趋势分析
    if len(history) >= 2:
        lines.append("## 📈 趋势分析")
        lines.append("")
        lines.append("| 日期 | 安居客均价 | 链家均价 | 变化% |")
        lines.append("|------|-----------|---------|-------|")
        for row in history[-5:]:
            a = row.get("anjuke_avg_price", "-")
            l = row.get("lianjia_avg_price", "-")
            c = row.get("anjuke_price_change_pct", "-")
            lines.append(f"| {row['date']} | {a} | {l} | {c} |")
        lines.append("")

    # 小区基础信息
    lines.append("## 📋 小区档案")
    lines.append("")
    lines.append(f"- **地址**: 广西防城港市防城区金花茶大道906号")
    lines.append(f"- **建成时间**: 1994年")
    lines.append(f"- **总户数**: 488户 / 2栋")
    lines.append(f"- **物业类型**: 住宅 / 商品房")
    lines.append(f"- **产权年限**: 70年")
    lines.append("")

    lines.append("---")
    lines.append(f"*报告自动生成于 {beijing.strftime('%Y-%m-%d %H:%M')}*")
    lines.append("")

    return "\n".join(lines)


def update_index_html():
    """更新 HTML 首页：趋势图 + 关键指标 + 历史表格"""
    history_rows = []
    if HISTORY_CSV.exists():
        with open(HISTORY_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                history_rows.append({
                    "date": row["date"],
                    "anjuke": row.get("anjuke_avg_price", ""),
                    "lianjia": row.get("lianjia_avg_price", ""),
                    "listing": row.get("anjuke_listing_count", ""),
                    "deals": row.get("lianjia_last_month_deals", ""),
                })
    history_json = json.dumps(history_rows, ensure_ascii=False)

    # 趋势数据
    trend_rows = []
    if TREND_CSV.exists():
        with open(TREND_CSV, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                trend_rows.append(row)
    trend_json = json.dumps(trend_rows, ensure_ascii=False)

    # 最新指标
    latest = history_rows[-1] if history_rows else {}
    latest_trend = trend_rows[-1] if trend_rows else {}
    latest_date = latest.get("date", "N/A")
    a_price = latest.get("anjuke", "N/A")
    l_price = latest.get("lianjia", "N/A")
    t4w = latest_trend.get("trend_4w", "数据累积中")
    anomaly = latest_trend.get("has_anomaly", "") == "True"
    anomaly_msg = latest_trend.get("anomaly_msg", "")

    html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>锦泰现代城 房价追踪</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f0f2f5;color:#333;min-height:100vh}}
.header{{background:linear-gradient(135deg,#e67e22,#d35400);color:white;padding:32px 20px;text-align:center}}
.header h1{{font-size:24px;margin-bottom:4px}}
.header p{{font-size:14px;opacity:0.85}}
.container{{max-width:960px;margin:0 auto;padding:16px}}
.card{{background:white;border-radius:12px;padding:20px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,0.06)}}
.card h3{{font-size:16px;margin-bottom:12px;color:#555}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}}
.metric{{text-align:center;padding:12px;background:#f8f9fa;border-radius:8px}}
.metric .val{{font-size:22px;font-weight:bold}}
.metric .lbl{{font-size:12px;color:#999;margin-top:4px}}
.up{{color:#e74c3c}}.down{{color:#27ae60}}.neutral{{color:#555}}
.anomaly{{background:#fff3e0!important;border:1px solid #e67e22}}
.chart-box{{height:380px;position:relative}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th,td{{padding:8px 12px;text-align:center;border-bottom:1px solid #eee}}
th{{background:#f5f5f5;font-weight:600;color:#666}}
tr:hover{{background:#f8f9fa}}
.footer{{text-align:center;color:#aaa;font-size:12px;padding:20px}}
</style>
</head>
<body>
<div class="header">
  <h1>防城港 · 锦泰现代城</h1>
  <p>房价追踪 | 最后更新: {latest_date}</p>
</div>

<div class="container">

<div class="card">
  <h3>关键指标</h3>
  <div class="metrics">
    <div class="metric">
      <div class="val up">{a_price}</div>
      <div class="lbl">安居客均价(元/m²)</div>
    </div>
    <div class="metric">
      <div class="val up">{l_price}</div>
      <div class="lbl">链家均价(元/m²)</div>
    </div>
    <div class="metric">
      <div class="val neutral">{t4w[:8]}</div>
      <div class="lbl">近4周走势</div>
    </div>
    <div class="metric{" anomaly" if anomaly else ""}">
      <div class="val">{"⚠" if anomaly else "-"}</div>
      <div class="lbl">{anomaly_msg[:20] if anomaly else "无异常"}</div>
    </div>
  </div>
</div>

<div class="card">
  <h3>价格趋势</h3>
  <div class="chart-box"><canvas id="priceChart"></canvas></div>
</div>

<div class="card">
  <h3>历史记录</h3>
  <table><thead><tr><th>日期</th><th>安居客(元/m²)</th><th>链家(元/m²)</th><th>挂牌量</th><th>成交</th></tr></thead>
  <tbody id="dataTable"><tr><td colspan="5">加载中...</td></tr></tbody></table>
</div>

</div>
<div class="footer">自动更新 · 每周六 12:00 CST | 数据源: 安居客 + 链家</div>

<script>
const historyData = {history_json};
const trendData = {trend_json};

function renderChart() {{
  const dates = historyData.map(d => d.date);
  const a = historyData.map(d => d.anjuke ? parseFloat(d.anjuke) : null);
  const l = historyData.map(d => d.lianjia ? parseFloat(d.lianjia) : null);

  new Chart(document.getElementById('priceChart'), {{
    type: 'line',
    data: {{
      labels: dates,
      datasets: [
        {{ label: '安居客', data: a, borderColor: '#e67e22', backgroundColor: '#e67e2220', tension: 0.3, fill: true, pointRadius: 5 }},
        {{ label: '链家', data: l, borderColor: '#2ecc71', backgroundColor: '#2ecc7120', tension: 0.3, fill: true, pointRadius: 5 }},
      ]
    }},
    options: {{
      responsive: true, maintainAspectRatio: false,
      plugins: {{ legend: {{ position: 'top', labels: {{ usePointStyle: true }} }} }},
      scales: {{ y: {{ beginAtZero: false, title: {{ display: true, text: '均价 (元/m²)' }} }} }}
    }}
  }});

  let tbody = '';
  [...historyData].reverse().forEach(d => {{
    const tr = trendData.find(t => t.date === d.date);
    const listing = d.listing || '-';
    const deals = d.deals || '-';
    tbody += `<tr><td>${{d.date}}</td><td>${{d.anjuke || '-'}}</td><td>${{d.lianjia || '-'}}</td><td>${{listing}}</td><td>${{deals}}</td></tr>`;
  }});
  document.getElementById('dataTable').innerHTML = tbody;
}}

renderChart();
</script>
</body>
</html>'''

    with open(REPORTS_DIR / "index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[SAVED] reports/index.html")


def analyze_trend(anjuke: dict, lianjia: dict) -> dict:
    """对比历史数据，计算周环比、近4周走势、挂牌量变化"""
    result = {
        "anjuke_wow_change": None,
        "lianjia_wow_change": None,
        "listing_wow_change": None,
        "trend_4w": "数据不足",
        "has_anomaly": False,
        "anomaly_msg": "",
    }

    if not HISTORY_CSV.exists():
        return result

    with open(HISTORY_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    if len(reader) < 1:
        return result

    # 上周最后一条记录
    last_rows = reader[-2:] if len(reader) >= 2 else [reader[-1]]
    prev = last_rows[0]  # 上一周
    curr_price_a = int(anjuke.get("avg_price") or 0)
    curr_price_l = int(lianjia.get("avg_price") or 0)
    curr_listing = int(anjuke.get("listing_count") or 0)

    prev_price_a = float(prev.get("anjuke_avg_price") or 0)
    prev_price_l = float(prev.get("lianjia_avg_price") or 0)
    prev_listing = float(prev.get("anjuke_listing_count") or 0)
    prev_l_listing = float(prev.get("lianjia_listing_count") or 0)

    # 周环比涨跌幅
    if prev_price_a > 0 and curr_price_a > 0:
        result["anjuke_wow_change"] = round((curr_price_a - prev_price_a) / prev_price_a * 100, 2)
    if prev_price_l > 0 and curr_price_l > 0:
        result["lianjia_wow_change"] = round((curr_price_l - prev_price_l) / prev_price_l * 100, 2)

    # 挂牌量变化（新增挂牌数 = 本周 - 上周）
    # 优先用链家数据，回退到安居客
    if (prev_l_listing > 0 or prev_listing > 0) and (anjuke.get("listing_count") or lianjia.get("listing_count")):
        new_listing = (lianjia.get("listing_count") or anjuke.get("listing_count") or 0) - max(prev_l_listing, prev_listing)
        result["listing_wow_change"] = int(new_listing)

    # 近4周走势总结
    if len(reader) >= 4:
        recent = reader[-4:]
        prices = []
        for row in recent:
            p = float(row.get("anjuke_avg_price") or row.get("lianjia_avg_price") or 0)
            if p > 0:
                prices.append(p)

        if len(prices) >= 3:
            # 简单趋势判断：涨幅超过1%算涨，超过-1%算跌
            first = prices[0]
            last = prices[-1]
            total_change = (last - first) / first * 100 if first > 0 else 0

            ups = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i-1])
            downs = sum(1 for i in range(1, len(prices)) if prices[i] < prices[i-1])

            if abs(total_change) < 1:
                result["trend_4w"] = f"近4周保持平稳，波动不超过 ±1%"
            elif ups > downs:
                result["trend_4w"] = f"近4周持续上涨 {total_change:+.1f}%"
            elif downs > ups:
                result["trend_4w"] = f"近4周持续下跌 {total_change:+.1f}%"
            else:
                result["trend_4w"] = f"近4周窄幅震荡，累计 {total_change:+.1f}%"
        elif len(prices) == 2:
            chg = (prices[-1] - prices[0]) / prices[0] * 100 if prices[0] > 0 else 0
            result["trend_4w"] = f"近2周变化 {chg:+.1f}%，数据积累中"

    # 异常检测
    a_wow = result.get("anjuke_wow_change") or 0
    l_wow = result.get("lianjia_wow_change") or 0
    max_change = a_wow if abs(a_wow) > abs(l_wow) else l_wow
    if abs(max_change) > 5:
        result["has_anomaly"] = True
        direction = "上涨" if max_change > 0 else "下跌"
        result["anomaly_msg"] = f"注意：本周环比{direction}{abs(max_change):.1f}%，幅度超过5%"

    return result


def save_trend_history(trend: dict):
    """趋势分析数据沉淀：WoW变化、走势判断、异常标记"""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")

    row = [
        date_str,
        trend.get("anjuke_wow_change"),
        trend.get("lianjia_wow_change"),
        trend.get("listing_wow_change"),
        trend.get("trend_4w", ""),
        trend.get("has_anomaly", False),
        trend.get("anomaly_msg", ""),
    ]

    is_new = not TREND_CSV.exists()
    with open(TREND_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow([
                "date", "anjuke_wow_pct", "lianjia_wow_pct",
                "listing_wow_change", "trend_4w", "has_anomaly", "anomaly_msg",
            ])
        writer.writerow(row)
    print(f"[SAVED] trend_history.csv 追加: {date_str}")


def save_scrape_log(anjuke_ok: bool, lianjia_ok: bool, transaction_ok: bool):
    """数据质量追踪：每次抓取后记录哪些数据源成功"""
    now = datetime.now(timezone(timedelta(hours=8)))
    date_str = now.strftime("%Y-%m-%d")

    row = [date_str, anjuke_ok, lianjia_ok, transaction_ok]

    is_new = not SCRAPE_LOG_CSV.exists()
    with open(SCRAPE_LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["date", "anjuke_ok", "lianjia_ok", "transaction_ok"])
        writer.writerow(row)
    print(f"[SAVED] scrape_log.csv 追加: {date_str}")


def write_summary_json(anjuke: dict, lianjia: dict, transaction: dict, trend: dict):
    """写入 data/summary.json 供 Workflow 通知使用，同时生成通知 Markdown"""
    a_price = anjuke.get("avg_price")
    l_price = lianjia.get("avg_price")
    t_avg = transaction.get("avg_price") if transaction else None
    t_count = transaction.get("count", 0) if transaction else 0

    a_wow = trend.get("anjuke_wow_change")
    listing_wow = trend.get("listing_wow_change")
    trend_4w = trend.get("trend_4w", "")
    has_anomaly = trend.get("has_anomaly", False)
    anomaly_msg = trend.get("anomaly_msg", "")

    PAGE_URL = "https://liuruchuan.github.io/fangchenggang-housing/"

    # 生成通知 Markdown（微信可渲染链接）
    lines = []
    lines.append("**安居客均价**: {}元/m²".format(a_price if a_price else "N/A"))
    lines.append("**链家均价**: {}元/m²".format(l_price if l_price else "N/A"))

    if a_wow is not None:
        lines.append("**均价周环比**: {0:+.1f}%".format(a_wow))
    if listing_wow:
        lines.append("**本周新增挂牌**: {}".format(f"+{listing_wow}" if listing_wow > 0 else str(listing_wow)))
    if t_count and t_count > 0:
        lines.append("**成交均价**: {}元/m² ({}条)".format(t_avg, t_count))

    if trend_4w:
        lines.append("")
        lines.append(trend_4w)
    if has_anomaly and anomaly_msg:
        lines.append("")
        lines.append(f"⚠️ {anomaly_msg}")

    lines.append("")
    lines.append(f"[查看完整趋势图]({PAGE_URL})")

    notify_text = "\n".join(lines)

    summary = {
        "anjuke": {"avg_price": a_price, "listing_count": anjuke.get("listing_count")},
        "lianjia": {"avg_price": l_price, "last_month_deals": lianjia.get("last_month_deals")},
        "transaction_avg": t_avg, "transaction_count": t_count,
        "trend": trend,
        "notify_text": notify_text,
    }
    with open(DATA_DIR / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    print("[SAVED] data/summary.json")


def main():
    print("=" * 50)
    print(f"防城港房价调研: {datetime.now(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    session = requests.Session()
    session.headers.update(HEADERS)

    # 抓取数据
    anjuke_data = fetch_anjuke(session)
    print(f"安居客: 均价={anjuke_data.get('avg_price')}, 变化={anjuke_data.get('price_change')}%")

    lianjia_data = fetch_lianjia(session)
    print(f"链家: 均价={lianjia_data.get('avg_price')}, 上月成交={lianjia_data.get('last_month_deals')}套")

    # 尝试获取成交记录
    transaction_data = fetch_transactions(session)
    if transaction_data.get("count"):
        print(f"成交记录: 均价={transaction_data.get('avg_price')}, 条数={transaction_data.get('count')}")
    else:
        print("成交记录: 暂无")

    # 保存原始快照
    now_str = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    snapshot = {"date": now_str, "anjuke": anjuke_data, "lianjia": lianjia_data, "transaction": transaction_data}
    with open(DATA_DIR / f"{now_str}_raw.json", "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # 追加 CSV
    update_history_csv(anjuke_data, lianjia_data, transaction_data)

    # 趋势分析
    trend = analyze_trend(anjuke_data, lianjia_data)
    print(f"趋势: 安居客周环比={trend.get('anjuke_wow_change', 'N/A')}%, {trend.get('trend_4w', '')}")

    # 趋势数据沉淀
    save_trend_history(trend)

    # 数据质量追踪
    save_scrape_log(
        anjuke_ok=bool(anjuke_data.get("avg_price")),
        lianjia_ok=bool(lianjia_data.get("avg_price")),
        transaction_ok=bool(transaction_data and transaction_data.get("count")),
    )

    # 写入 summary JSON（供 Workflow 通知读取）
    write_summary_json(anjuke_data, lianjia_data, transaction_data, trend)

    # 生成周报
    report = generate_report(anjuke_data, lianjia_data)
    report_path = REPORTS_DIR / f"{now_str}-房价周报.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"[SAVED] {report_path}")

    # 更新 HTML
    update_index_html()

    print("\n== 完成 ==")


if __name__ == "__main__":
    main()
