import json
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta
import html
import plotly.graph_objects as go
import plotly.io as pio

pio.templates.default = "plotly_dark"

CHAT_FILE = "main.jsonl"
MY_AUTHOR_ID = "1"

# --- Load contacts and chat metadata from main.jsonl ---
contacts = {}
chat_meta = {}

with open(CHAT_FILE) as f:
    for line in f:
        obj = json.loads(line)
        if "recipient" in obj:
            r = obj["recipient"]
            c = r.get("contact", {})
            rid = r.get("id")
            if rid and c:
                given = c.get("systemGivenName") or c.get("profileGivenName") or ""
                family = c.get("systemFamilyName") or c.get("profileFamilyName") or ""
                name = f"{given} {family}".strip()
                if name:
                    contacts[rid] = name
        elif "chat" in obj:
            ch = obj["chat"]
            cid = ch.get("id")
            if cid:
                chat_meta[cid] = ch

contacts[MY_AUTHOR_ID] = "You"

# --- Fun fact tracking ---
longest_msg = {"length": 0, "text": "", "author": "", "chat": "", "date": ""}
busiest_day = {"date": "", "count": 0}
hourly_counts = Counter()
weekday_counts = Counter()
msg_dates = defaultdict(set)  # chat_id -> set of date strings
msg_timestamps = []  # (timestamp_ms, author, chat_id)
msg_lengths = []  # (length, author, chat_id, date, text)
day_counts = Counter()


def name_for(aid):
    return contacts.get(aid, f"User {aid}")


def chat_label(cid, authors):
    if len(authors) == 2 and MY_AUTHOR_ID in authors:
        partner = (authors - {MY_AUTHOR_ID}).pop()
        return name_for(partner)
    members = [name_for(a) for a in sorted(authors, key=lambda x: int(x)) if a != MY_AUTHOR_ID]
    return ", ".join(members[:3]) + (f" +{len(members)-3}" if len(members) > 3 else "")


def parse_timestamp(ts_ms):
    return datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone.utc)


# --- Parse messages ---
chats = {}

with open(CHAT_FILE) as f:
    for line in f:
        obj = json.loads(line)
        item = obj.get("chatItem", {})
        if not item:
            continue

        cid = item["chatId"]
        aid = item.get("authorId", "?")

        if cid not in chats:
            chats[cid] = {
                "authors": set(),
                "author_counter": Counter(),
                "total": 0,
                "incoming": 0,
                "outgoing": 0,
                "directionless": 0,
                "by_type": Counter(),
                "text_msg_count": 0,
                "text_len_total": 0,
                "text_lens": [],
                "len_buckets": Counter(),
                "monthly": Counter(),
                "monthly_chars": Counter(),
                "top_msgs": [],
            }

        chat = chats[cid]
        chat["authors"].add(aid)
        chat["author_counter"][aid] += 1
        chat["total"] += 1

        direction = "incoming" if "incoming" in item else ("outgoing" if "outgoing" in item else "directionless")
        if direction == "incoming":
            chat["incoming"] += 1
        elif direction == "outgoing":
            chat["outgoing"] += 1
        else:
            chat["directionless"] += 1

        ts = parse_timestamp(item["dateSent"])
        month_key = ts.strftime("%Y-%m")
        chat["monthly"][month_key] += 1

        msg_type = "unknown"
        for key in ("standardMessage", "stickerMessage", "viewOnceMessage", "poll",
                     "updateMessage", "remoteDeletedMessage", "contactMessage"):
            if key in item:
                msg_type = key
                break
        if "revisions" in item:
            contained = [k for k in ("standardMessage",) if k in item["revisions"]]
            if contained:
                msg_type = f"revised_{contained[0]}"
        chat["by_type"][msg_type] += 1

        body = ""
        if "standardMessage" in item:
            text = item["standardMessage"].get("text", {})
            if text:
                body = text.get("body", "")
        elif "contactMessage" in item:
            contact = item["contactMessage"].get("contact", {})
            n = contact.get("name", {})
            parts = [n.get("givenName", ""), n.get("familyName", "")]
            body = " ".join(p for p in parts if p)

        if body:
            blen = len(body)
            chat["text_msg_count"] += 1
            chat["text_len_total"] += blen
            chat["text_lens"].append(blen)
            chat["monthly_chars"][month_key] += blen
            if blen <= 10:
                chat["len_buckets"]["1-10"] += 1
            elif blen <= 50:
                chat["len_buckets"]["11-50"] += 1
            elif blen <= 100:
                chat["len_buckets"]["51-100"] += 1
            elif blen <= 500:
                chat["len_buckets"]["101-500"] += 1
            else:
                chat["len_buckets"]["500+"] += 1

            msg_lengths.append((blen, aid, cid, month_key, body[:200]))
            if blen > longest_msg["length"]:
                longest_msg.update({"length": blen, "text": body[:200], "author": aid, "chat": cid, "date": month_key})
            chat["top_msgs"].append((blen, aid, body, ts.strftime("%Y-%m-%d")))
            if len(chat["top_msgs"]) > 10:
                chat["top_msgs"].sort(key=lambda x: x[0], reverse=True)
                chat["top_msgs"] = chat["top_msgs"][:10]

        day_str = ts.strftime("%Y-%m-%d")
        msg_dates[cid].add(day_str)
        hourly_counts[ts.hour] += 1
        weekday_counts[ts.strftime("%A")] += 1
        msg_timestamps.append((int(item["dateSent"]), aid, cid))

        day_counts[day_str] += 1

# --- Aggregate ---
one_on_one = {cid: ch for cid, ch in chats.items() if len(ch["authors"]) == 2 and MY_AUTHOR_ID in ch["authors"]}
groups = {cid: ch for cid, ch in chats.items() if len(ch["authors"]) > 2 and MY_AUTHOR_ID in ch["authors"]}
other = {cid: ch for cid, ch in chats.items() if MY_AUTHOR_ID not in ch["authors"]}

type_counts = Counter()
for ch in chats.values():
    type_counts.update(ch["by_type"])

monthly_all = Counter()
for ch in chats.values():
    monthly_all.update(ch["monthly"])

sorted_1on1 = sorted(one_on_one.items(), key=lambda x: x[1]["total"], reverse=True)
sorted_groups = sorted(groups.items(), key=lambda x: x[1]["total"], reverse=True)

total_msgs = sum(ch["total"] for ch in chats.values())
total_text_len = sum(ch["text_len_total"] for ch in chats.values())
total_1on1 = sum(ch["total"] for ch in one_on_one.values())
total_group = sum(ch["total"] for ch in groups.values())

# --- Fun fact aggregation ---
busiest_day_str = day_counts.most_common(1)[0] if day_counts else ("", 0)
busiest_day = {"date": busiest_day_str[0], "count": busiest_day_str[1]}

peak_hour = hourly_counts.most_common(1)[0] if hourly_counts else (0, 0)
peak_weekday = weekday_counts.most_common(1)[0] if weekday_counts else ("", 0)

# Most prolific texter by message count
author_msg_counts = Counter()
author_char_counts = Counter()
for ch in chats.values():
    for aid, cnt in ch["author_counter"].items():
        author_msg_counts[aid] += cnt
    for aid in ch["authors"]:
        # approximate char count per author isn't tracked, skip for now
        pass
most_prolific_msg = author_msg_counts.most_common(1)[0] if author_msg_counts else ("", 0)

# Per-chat streaks for 1-on-1 chats
chat_streaks = {}
for cid, ch in one_on_one.items():
    days = sorted(msg_dates.get(cid, []))
    all_streaks = []
    all_gaps = []
    cur_start = ""
    cur_len = 0
    if days:
        prev = datetime.strptime(days[0], "%Y-%m-%d")
        cur_start = days[0]
        cur_len = 1
        for d_str in days[1:]:
            d = datetime.strptime(d_str, "%Y-%m-%d")
            if (d - prev).days == 1:
                cur_len += 1
            else:
                all_streaks.append({"days": cur_len, "start": cur_start, "end": prev.strftime("%Y-%m-%d")})
                gap_days = (d - prev).days - 1
                all_gaps.append({"days": gap_days, "start": prev.strftime("%Y-%m-%d"), "end": d_str})
                cur_start = d_str
                cur_len = 1
            prev = d
        all_streaks.append({"days": cur_len, "start": cur_start, "end": days[-1]})
    all_streaks.sort(key=lambda x: x["days"], reverse=True)
    all_gaps.sort(key=lambda x: x["days"], reverse=True)

    avg_len = sum(s["days"] for s in all_streaks) / len(all_streaks) if all_streaks else 0
    chat_streaks[cid] = {
        "all": all_streaks,
        "longest": all_streaks[0] if all_streaks else {"days": 0, "start": "", "end": ""},
        "top_streaks": all_streaks[:5],
        "longest_gap": all_gaps[0] if all_gaps else {"days": 0, "start": "", "end": ""},
        "total": len(all_streaks),
        "avg": avg_len,
    }

# Global longest streak (any chat, any sender)
all_global_days = sorted(day_counts.keys())
global_longest_streak = 0
global_streak_start = ""
global_streak_end = ""
if all_global_days:
    prev = datetime.strptime(all_global_days[0], "%Y-%m-%d")
    cur_start = all_global_days[0]
    cur_len = 1
    global_longest_streak = 1
    global_streak_start = all_global_days[0]
    global_streak_end = all_global_days[0]
    for d_str in all_global_days[1:]:
        d = datetime.strptime(d_str, "%Y-%m-%d")
        if (d - prev).days == 1:
            cur_len += 1
        else:
            if cur_len > global_longest_streak:
                global_longest_streak = cur_len
                global_streak_start = cur_start
                global_streak_end = prev.strftime("%Y-%m-%d")
            cur_start = d_str
            cur_len = 1
        prev = d
    if cur_len > global_longest_streak:
        global_longest_streak = cur_len
        global_streak_start = cur_start
        global_streak_end = all_global_days[-1]

# Longest gap between messages (global)
longest_gap_days = 0
gap_start = ""
gap_end = ""
if msg_timestamps:
    sorted_ts = sorted(set(t[0] for t in msg_timestamps))
    for i in range(1, len(sorted_ts)):
        diff = (sorted_ts[i] - sorted_ts[i-1]) / 86400000  # ms to days
        if diff > longest_gap_days:
            longest_gap_days = diff
            gap_start = datetime.fromtimestamp(sorted_ts[i-1]/1000, tz=timezone.utc).strftime("%Y-%m-%d")
            gap_end = datetime.fromtimestamp(sorted_ts[i]/1000, tz=timezone.utc).strftime("%Y-%m-%d")

# Average messages per active day
all_days = sorted(day_counts.keys())
avg_msgs_per_day = total_msgs / len(all_days) if all_days else 0

# --- Build plotly charts ---
all_months = sorted(monthly_all.keys())

LINE_COLORS = [
    "#4f8cf7", "#22c55e", "#ef4444", "#f59e0b", "#8b5cf6",
    "#ec4899", "#14b8a6", "#f97316", "#06b6d4", "#84cc16",
    "#a855f7", "#e11d48", "#0ea5e9", "#d946ef", "#10b981",
    "#f43f5e", "#3b82f6", "#eab308", "#6366f1", "#64748b",
]

PALETTE = LINE_COLORS


def make_trend_chart(chat_list, title, y_label, key="monthly", max_chats=10):
    top = chat_list[:max_chats]
    if not top:
        return "<p style='color:#71717a'>No data</p>"

    fig = go.Figure()
    for idx, (cid, ch) in enumerate(top):
        label = chat_label(cid, ch["authors"])
        vals = [ch[key].get(m, 0) for m in all_months]
        color = PALETTE[idx % len(PALETTE)]
        fig.add_trace(go.Scatter(
            x=all_months, y=vals,
            mode="lines+markers",
            name=label,
            text=[label] * len(vals),
            line=dict(color=color, width=2),
            marker=dict(size=4, color=color),
            hovertemplate="%{text}<br>%{x}<br><b>%{y}</b><extra></extra>",
        ))

    fig.update_layout(
        title=title,
        xaxis_title=None,
        yaxis_title=y_label,
        height=400,
        margin=dict(l=50, r=20, t=10, b=50),
        hovermode="closest",
        hoverdistance=5,
        dragmode="zoom",
        hoverlabel=dict(
            bgcolor="#1f1f23",
            bordercolor="#3f3f46",
            font=dict(color="#e4e4e7", size=12),
            namelength=-1,
        ),
        legend=dict(
            orientation="v",
            yanchor="top",
            y=0.98,
            xanchor="left",
            x=1.02,
            font=dict(size=11, color="#a1a1aa"),
            itemclick="toggleothers",
        ),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e4e4e7", size=12),
        xaxis=dict(
            gridcolor="#27272a",
            zerolinecolor="#27272a",
            tickangle=-45,
            showspikes=False,
        ),
        yaxis=dict(
            gridcolor="#27272a",
            zerolinecolor="#27272a",
        ),
    )

    config = {
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["sendDataToCloud", "toImage", "lasso2d", "select2d"],
        "modeBarButtonsToAdd": ["drawrect", "eraseshape"],
        "displaylogo": False,
        "scrollZoom": False,
    }
    return pio.to_html(fig, include_plotlyjs=False, full_html=False, div_id=None, config=config)


chart_msgs_1on1 = make_trend_chart(sorted_1on1, "", "Messages", key="monthly")
chart_chars_1on1 = make_trend_chart(sorted_1on1, "", "Characters", key="monthly_chars")
chart_msgs_groups = make_trend_chart(sorted_groups, "", "Messages", key="monthly")
chart_chars_groups = make_trend_chart(sorted_groups, "", "Characters", key="monthly_chars")

# --- HTML generation ---
H = html.escape
BUCKET_COLORS = ["#22c55e", "#4f8cf7", "#f59e0b", "#ef4444", "#8b5cf6"]
BUCKET_ORDER = ["1-10", "11-50", "51-100", "101-500", "500+"]


def fmt_num(n):
    if n >= 1000000:
        return f"{n/1000000:.1f}M"
    if n >= 1000:
        return f"{n/1000:.1f}K"
    return str(n)


def bar(val, max_val, color="#4f8cf7"):
    pct = (val / max_val * 100) if max_val else 0
    return f'<div class="bar"><div class="bar-fill" style="width:{pct:.1f}%;background:{color}"></div></div>'


html_parts = []


def w(s=""):
    html_parts.append(s)


w("""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Signal Chat Statistics</title>
<script src="https://cdn.plot.ly/plotly-3.0.1.min.js" charset="utf-8"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f0f11; color: #e4e4e7; padding: 24px; }
h1 { font-size: 28px; font-weight: 700; margin-bottom: 4px; }
h2 { font-size: 20px; font-weight: 600; margin: 32px 0 12px; color: #a1a1aa; }
h3 { font-size: 16px; font-weight: 600; margin: 0 0 4px; }
.card { background: #18181b; border-radius: 12px; padding: 20px; margin-bottom: 16px; border: 1px solid #27272a; }
.card-title { font-size: 14px; font-weight: 500; color: #a1a1aa; margin-bottom: 4px; }
.card-value { font-size: 28px; font-weight: 700; }
.card-value small { font-size: 14px; font-weight: 400; color: #71717a; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 24px; }
.subtitle { color: #71717a; font-size: 14px; margin-bottom: 24px; }

table { width: 100%; border-collapse: collapse; font-size: 14px; }
th { text-align: left; padding: 8px 10px; border-bottom: 2px solid #27272a; color: #a1a1aa; font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; white-space: nowrap; }
td { padding: 8px 10px; border-bottom: 1px solid #27272a; white-space: nowrap; }
tr:hover td { background: #1f1f23; }
.name-cell { font-weight: 500; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar-cell { min-width: 100px; }
.msg-cell { white-space: normal !important; line-height: 1.4; font-size: 13px; color: #a1a1aa; cursor: pointer; max-width: 500px; word-break: break-word; }
.msg-cell:hover { color: #d4d4d8; }

.cal-wrap { overflow-x: auto; margin-bottom: 8px; }
.cal-container { display: inline-flex; gap: 6px; align-items: start; }
.cal-dow { display: flex; flex-direction: column; gap: 2px; padding-top: 16px; }
.cal-dow span { width: 10px; height: 10px; font-size: 8px; color: #52525b; display: flex; align-items: center; justify-content: center; }
.cal-months { display: flex; gap: 0; height: 14px; font-size: 9px; color: #52525b; margin-bottom: 2px; }
.cal-months span { flex-shrink: 0; }
.cal-grid-gh { display: flex; gap: 2px; }
.cal-week { display: flex; flex-direction: column; gap: 2px; }
.cal-cell-gh { width: 10px; height: 10px; border-radius: 2px; background: #18181b; }
.cal-cell-gh.on { background: #2563eb; }
.cal-legend { display: flex; align-items: center; gap: 4px; margin-top: 6px; font-size: 10px; color: #71717a; }
.cal-legend-cell { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }
.cal-years { display: flex; gap: 4px; margin-bottom: 8px; flex-wrap: wrap; }
.cal-year-btn { padding: 3px 10px; border-radius: 4px; background: #27272a; color: #a1a1aa; cursor: pointer; font-size: 12px; border: none; }
.cal-year-btn:hover { background: #3f3f46; color: #e4e4e7; }
.cal-year-btn.active { background: #4f8cf7; color: #fff; }
.cal-stat { }
.cal-stat-label { font-size: 11px; color: #71717a; }
.cal-stat-value { font-size: 18px; font-weight: 700; }
.cal-stat-detail { font-size: 11px; color: #52525b; }
.bar { height: 8px; background: #27272a; border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }

.vbars { display: flex; align-items: end; gap: 2px; height: 80px; padding: 4px 0; }
.vbar { flex: 1; min-width: 3px; border-radius: 2px 2px 0 0; position: relative; cursor: pointer; height: 100%; }
.vbar-fill { background: #4f8cf7; border-radius: 2px 2px 0 0; min-height: 1px; }
.vbar .tooltip { display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: #27272a; color: #e4e4e7; padding: 3px 6px; border-radius: 4px; font-size: 10px; white-space: nowrap; z-index: 10; pointer-events: none; }
.vbar:hover .tooltip { display: block; }

.tabs { display: flex; gap: 4px; margin-bottom: 16px; flex-wrap: wrap; }
.tab { padding: 6px 16px; border-radius: 6px; background: #27272a; color: #a1a1aa; cursor: pointer; font-size: 14px; border: none; }
.tab:hover { background: #3f3f46; color: #e4e4e7; }
.tab.active { background: #4f8cf7; color: #fff; }
.tab-content { display: none; }
.tab-content.active { display: block; }

.monthly-grid { display: flex; align-items: end; gap: 2px; height: 120px; padding: 8px 0; }
.month-bar { flex: 1; min-width: 8px; border-radius: 3px 3px 0 0; position: relative; cursor: pointer; }
.month-bar:hover { opacity: 0.8; }
.month-bar .tooltip { display: none; position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%); background: #27272a; color: #e4e4e7; padding: 4px 8px; border-radius: 4px; font-size: 11px; white-space: nowrap; z-index: 10; }
.month-bar:hover .tooltip { display: block; }

.comparison-chart { }
.comp-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid #1f1f23; }
.comp-row:hover { background: #1f1f23; border-radius: 4px; }
.comp-label { width: 160px; font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex-shrink: 0; }
.comp-bar-wrap { flex: 1; height: 24px; background: #27272a; border-radius: 6px; overflow: hidden; display: flex; position: relative; }
.comp-bar-in { height: 100%; background: #22c55e; transition: width 0.3s; }
.comp-bar-out { height: 100%; background: #4f8cf7; transition: width 0.3s; }
.comp-num { width: 70px; text-align: right; font-size: 13px; font-variant-numeric: tabular-nums; flex-shrink: 0; }

.len-bucket { display: inline-flex; align-items: center; gap: 6px; margin: 2px 4px 2px 0; }
.len-swatch { width: 10px; height: 10px; border-radius: 2px; flex-shrink: 0; }

.fun-fact { display: flex; align-items: center; gap: 16px; padding: 16px 20px; }
.fun-fact-icon { font-size: 32px; flex-shrink: 0; width: 48px; text-align: center; }
.fun-fact-body { flex: 1; min-width: 0; }
.fun-fact-label { font-size: 13px; color: #a1a1aa; margin-bottom: 2px; }
.fun-fact-value { font-size: 20px; font-weight: 700; }
.fun-fact-detail { font-size: 13px; color: #71717a; margin-top: 4px; line-height: 1.4; }
.fun-fact-detail em { color: #a1a1aa; font-style: normal; }
.msg-preview { background: #27272a; border-radius: 8px; padding: 10px 14px; margin-top: 8px; font-size: 13px; color: #d4d4d8; line-height: 1.5; word-break: break-word; max-height: 80px; overflow: hidden; }
.msg-preview-trunc { position: relative; }
.msg-preview-trunc::after { content: ''; position: absolute; bottom: 0; left: 0; right: 0; height: 24px; background: linear-gradient(transparent, #27272a); }

.plot-container { border-radius: 8px; overflow: hidden; }
.js-plotly-plot .plotly .main-svg { border-radius: 8px; }

@media (max-width: 700px) {
  .hide-mobile { display: none; }
  .comp-label { width: 100px; font-size: 12px; }
  .comp-num { width: 50px; font-size: 12px; }
  body { padding: 12px; }
  td, th { padding: 6px 6px; font-size: 12px; }
}
</style>
</head>
<body>
<h1>Signal Chat Statistics</h1>
<p class="subtitle">""" + fmt_num(total_msgs) + """ total messages &middot; """ + str(len(one_on_one)) + """ 1-on-1 chats &middot; """ + str(len(groups)) + """ group chats &middot; """ + str(len(other)) + """ other chats</p>
""")

# Summary cards
w(f"""
<div class="grid">
  <div class="card"><div class="card-title">Total Messages</div><div class="card-value">{fmt_num(total_msgs)}</div></div>
  <div class="card"><div class="card-title">1-on-1 Messages</div><div class="card-value">{fmt_num(total_1on1)} <small>({total_1on1/total_msgs*100:.0f}%)</small></div></div>
  <div class="card"><div class="card-title">Group Messages</div><div class="card-value">{fmt_num(total_group)} <small>({total_group/total_msgs*100:.0f}%)</small></div></div>
  <div class="card"><div class="card-title">Total Characters</div><div class="card-value">{fmt_num(total_text_len)}</div></div>
  <div class="card"><div class="card-title">Timespan</div><div class="card-value">{min(monthly_all.keys())[:7]} <small>to</small> {max(monthly_all.keys())[:7]}</div></div>
</div>
""")

# Tabs
w("""
<div class="tabs">
  <button class="tab active" onclick="switchTab('overview')">Overview</button>
  <button class="tab" onclick="switchTab('trends')">Trends</button>
  <button class="tab" onclick="switchTab('1on1')">1-on-1 Chats</button>
  <button class="tab" onclick="switchTab('groups')">Group Chats</button>
  <button class="tab" onclick="switchTab('activity')">Activity</button>
  <button class="tab" onclick="switchTab('types')">Message Types</button>
  <button class="tab" onclick="switchTab('funfacts')">Fun Facts</button>
  <button class="tab" onclick="switchTab('insights')">1-on-1 Insights</button>
  <button class="tab" onclick="switchTab('detail')">Full Details</button>
</div>
""")

# === OVERVIEW TAB ===
w('<div id="tab-overview" class="tab-content active">')

w('<h2>1-on-1 Chat Comparison</h2>')
w('<div class="card">')
max_1on1_total = max(ch["total"] for _, ch in sorted_1on1) if sorted_1on1 else 1
w('<div class="comparison-chart">')
for cid, ch in sorted_1on1:
    partner = (ch["authors"] - {MY_AUTHOR_ID}).pop()
    pname = name_for(partner)
    in_pct = ch["incoming"] / max_1on1_total * 100
    out_pct = ch["outgoing"] / max_1on1_total * 100
    w(f"""<div class="comp-row">
  <div class="comp-label" title="{H(pname)}">{H(pname)}</div>
  <div class="comp-bar-wrap"><div class="comp-bar-in" style="width:{in_pct:.1f}%" title="Them: {ch['incoming']:,}"></div><div class="comp-bar-out" style="width:{out_pct:.1f}%" title="You: {ch['outgoing']:,}"></div></div>
  <div class="comp-num">{ch['total']}</div>
</div>""")
w('</div>')
w('<div style="display:flex;gap:16px;margin-top:8px;font-size:12px;color:#71717a"><span><span style="display:inline-block;width:10px;height:10px;background:#22c55e;border-radius:2px;margin-right:4px"></span>Incoming (them)</span><span><span style="display:inline-block;width:10px;height:10px;background:#4f8cf7;border-radius:2px;margin-right:4px"></span>Outgoing (you)</span></div>')
w('</div>')

w('<h2>Message Balance</h2>')
w('<div class="card">')
w("""<table><thead><tr>
  <th>Name</th>
  <th class="num">Your msgs</th>
  <th class="num">Their msgs</th>
  <th class="num hide-mobile">Total</th>
  <th>Balance</th>
</tr></thead><tbody>""")
for cid, ch in sorted_1on1:
    partner = (ch["authors"] - {MY_AUTHOR_ID}).pop()
    pname = name_for(partner)
    mine = ch["outgoing"]
    theirs = ch["incoming"]
    total = ch["total"]
    bal = mine - theirs
    bal_str = f"+{bal}" if bal > 0 else str(bal)
    bal_color = "#22c55e" if bal > 0 else ("#ef4444" if bal < 0 else "#71717a")
    max_side = max(mine, theirs) or 1
    w(f"""<tr>
  <td class="name-cell">{H(pname)}</td>
  <td class="num">{mine}</td>
  <td class="num">{theirs}</td>
  <td class="num hide-mobile">{total}</td>
  <td class="bar-cell">
    <div style="display:flex;height:8px;border-radius:4px;overflow:hidden">
      <div style="flex:{mine/max_side:.3f};background:#4f8cf7;min-width:1px"></div>
      <div style="flex:{theirs/max_side:.3f};background:#22c55e;min-width:1px"></div>
    </div>
    <span style="font-size:11px;color:{bal_color};margin-left:4px">{bal_str}</span>
  </td>
</tr>""")
w("</tbody></table></div>")

if sorted_groups:
    w('<h2>Top Groups</h2>')
    w('<div class="card">')
    max_g = max(ch["total"] for _, ch in sorted_groups)
    w('<div class="comparison-chart">')
    for cid, ch in sorted_groups[:20]:
        cname = chat_label(cid, ch["authors"])
        total = ch["total"]
        in_pct = ch["incoming"] / max_g * 100
        out_pct = ch["outgoing"] / max_g * 100
        w(f"""<div class="comp-row">
  <div class="comp-label" title="{H(cname)}">{H(cname)}</div>
  <div class="comp-bar-wrap"><div class="comp-bar-in" style="width:{in_pct:.1f}%" title="Incoming: {ch['incoming']:,}"></div><div class="comp-bar-out" style="width:{out_pct:.1f}%" title="Outgoing: {ch['outgoing']:,}"></div></div>
  <div class="comp-num">{total}</div>
</div>""")
    w('</div></div>')

w('</div>')

# === TRENDS TAB ===
w('<div id="tab-trends" class="tab-content">')
w('<h2>Messages Over Time — 1-on-1 Chats</h2>')
w('<div class="card"><div class="plot-container">' + chart_msgs_1on1 + '</div></div>')
w('<h2>Characters Over Time — 1-on-1 Chats</h2>')
w('<div class="card"><div class="plot-container">' + chart_chars_1on1 + '</div></div>')
if chart_msgs_groups:
    w('<h2>Messages Over Time — Group Chats</h2>')
    w('<div class="card"><div class="plot-container">' + chart_msgs_groups + '</div></div>')
    w('<h2>Characters Over Time — Group Chats</h2>')
    w('<div class="card"><div class="plot-container">' + chart_chars_groups + '</div></div>')
w('</div>')

# === 1-ON-1 TAB ===
max_1on1 = max(ch["total"] for _, ch in sorted_1on1) if sorted_1on1 else 1
w('<div id="tab-1on1" class="tab-content">')
w('<div class="card">')
w("""<table>
<thead><tr>
  <th>#</th>
  <th>Name</th>
  <th class="num">Total</th>
  <th class="num">In</th>
  <th class="num">Out</th>
  <th class="num hide-mobile">Avg Len</th>
  <th class="num hide-mobile">Chars</th>
  <th class="num">Months</th>
  <th>Activity</th>
</tr></thead><tbody>""")
for idx, (cid, ch) in enumerate(sorted_1on1, 1):
    partner = (ch["authors"] - {MY_AUTHOR_ID}).pop()
    avg_len = ch["text_len_total"] / ch["text_msg_count"] if ch["text_msg_count"] else 0
    months = len(ch["monthly"])
    w(f"""<tr>
  <td>{idx}</td>
  <td class="name-cell">{H(name_for(partner))}</td>
  <td class="num">{ch['total']}</td>
  <td class="num">{ch['incoming']}</td>
  <td class="num">{ch['outgoing']}</td>
  <td class="num hide-mobile">{avg_len:.1f}</td>
  <td class="num hide-mobile">{fmt_num(ch['text_len_total'])}</td>
  <td class="num">{months}</td>
  <td class="bar-cell">{bar(ch['total'], max_1on1)}</td>
</tr>""")
w("</tbody></table></div>")

# Chars-ranked table
w('<h2>Ranked by Total Characters</h2>')
w('<div class="card">')
sorted_chars = sorted(one_on_one.items(), key=lambda x: x[1]["text_len_total"], reverse=True)
max_chars = max(ch["text_len_total"] for _, ch in sorted_chars) if sorted_chars else 1
w("""<table>
<thead><tr>
  <th>#</th>
  <th>Name</th>
  <th class="num">Total Chars</th>
  <th class="num">Avg Len</th>
  <th class="num">Total Msgs</th>
  <th class="num hide-mobile">In</th>
  <th class="num hide-mobile">Out</th>
  <th>Activity</th>
</tr></thead><tbody>""")
for idx, (cid, ch) in enumerate(sorted_chars, 1):
    partner = (ch["authors"] - {MY_AUTHOR_ID}).pop()
    avg_len = ch["text_len_total"] / ch["text_msg_count"] if ch["text_msg_count"] else 0
    w(f"""<tr>
  <td>{idx}</td>
  <td class="name-cell">{H(name_for(partner))}</td>
  <td class="num">{fmt_num(ch['text_len_total'])}</td>
  <td class="num">{avg_len:.1f}</td>
  <td class="num">{ch['total']}</td>
  <td class="num hide-mobile">{ch['incoming']}</td>
  <td class="num hide-mobile">{ch['outgoing']}</td>
  <td class="bar-cell">{bar(ch['text_len_total'], max_chars, '#f59e0b')}</td>
</tr>""")
w("</tbody></table></div></div>")

# === GROUPS TAB ===
w('<div id="tab-groups" class="tab-content">')
if sorted_groups:
    max_g = max(ch["total"] for _, ch in sorted_groups)
    w('<div class="card">')
    w("""<table><thead><tr>
  <th>#</th>
  <th>Members</th>
  <th class="num">Total</th>
  <th class="num">In</th>
  <th class="num">Out</th>
  <th class="num">Members</th>
  <th class="num hide-mobile">Avg Len</th>
  <th class="num hide-mobile">Chars</th>
  <th class="num">Months</th>
  <th>Activity</th>
</tr></thead><tbody>""")
    for idx, (cid, ch) in enumerate(sorted_groups, 1):
        avg_len = ch["text_len_total"] / ch["text_msg_count"] if ch["text_msg_count"] else 0
        members = len(ch["authors"])
        months = len(ch["monthly"])
        cname = chat_label(cid, ch["authors"])
        w(f"""<tr title="{H(cname)}">
  <td>{idx}</td>
  <td class="name-cell">{H(cname)}</td>
  <td class="num">{ch['total']}</td>
  <td class="num">{ch['incoming']}</td>
  <td class="num">{ch['outgoing']}</td>
  <td class="num">{members}</td>
  <td class="num hide-mobile">{avg_len:.1f}</td>
  <td class="num hide-mobile">{fmt_num(ch['text_len_total'])}</td>
  <td class="num">{months}</td>
  <td class="bar-cell">{bar(ch['total'], max_g)}</td>
</tr>""")
    w("</tbody></table></div>")
else:
    w('<div class="card"><p>No group chats with you as a member.</p></div>')
w("</div>")

# === ACTIVITY TAB ===
w('<div id="tab-activity" class="tab-content">')
w('<div class="card">')
max_month = max(monthly_all.values()) if monthly_all else 1
w('<div class="monthly-grid">')
for m in sorted(monthly_all.keys()):
    cnt = monthly_all[m]
    pct = cnt / max_month * 100
    w(f'<div class="month-bar" style="height:{pct:.1f}%;background:#4f8cf7" title="{m}: {cnt}"><span class="tooltip">{m}: {cnt}</span></div>')
w('</div>')
w('<div style="margin-top:12px;font-size:13px;color:#71717a;display:flex;justify-content:space-between"><span></span><span style="font-weight:600;color:#e4e4e7;">Monthly Messages</span><span></span></div>')
w('</div>')
w('<div class="card">')
w("<table><thead><tr><th>Month</th><th class='num'>Messages</th><th>Bar</th></tr></thead><tbody>")
max_display = max(monthly_all.values()) if monthly_all else 1
for m in sorted(monthly_all.keys(), reverse=True):
    cnt = monthly_all[m]
    w(f"<tr><td>{m}</td><td class='num'>{cnt}</td><td class='bar-cell'>{bar(cnt, max_display)}</td></tr>")
w("</tbody></table></div></div>")

# === MESSAGE TYPES TAB ===
w('<div id="tab-types" class="tab-content">')
max_type = max(type_counts.values()) if type_counts else 1
w('<div class="card">')
type_labels = {
    "standardMessage": "Standard Message",
    "updateMessage": "Update/System",
    "remoteDeletedMessage": "Remote Deleted",
    "stickerMessage": "Sticker",
    "poll": "Poll",
    "viewOnceMessage": "View Once",
    "revised_standardMessage": "Edited Message",
    "contactMessage": "Contact",
    "unknown": "Unknown",
}
w("<table><thead><tr><th>Type</th><th class='num'>Count</th><th class='num'>%</th><th>Bar</th></tr></thead><tbody>")
for t, c in type_counts.most_common():
    label = type_labels.get(t, t)
    pct = c / total_msgs * 100
    w(f"<tr><td>{label}</td><td class='num'>{c}</td><td class='num'>{pct:.1f}%</td><td class='bar-cell'>{bar(c, max_type, '#22c55e')}</td></tr>")
w("</tbody></table></div></div>")

# === FUN FACTS TAB ===
w('<div id="tab-funfacts" class="tab-content">')
w('<h2>Fun Facts</h2>')

# Longest message
lm_author = name_for(longest_msg["author"]) if longest_msg["author"] else "?"
lm_chat = longest_msg["chat"]
lm_chat_name = chats[lm_chat]["authors"] if lm_chat in chats else set()
lm_chat_label = chat_label(lm_chat, lm_chat_name) if lm_chat else ""
lm_preview = html.escape(longest_msg["text"][:150])
w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">📜</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Longest Message Ever</div>
    <div class="fun-fact-value">{longest_msg['length']:,} characters</div>
    <div class="fun-fact-detail">Sent by <em>{H(lm_author)}</em> in <em>{H(lm_chat_label)}</em> &middot; {longest_msg['date']}</div>
    <div class="msg-preview msg-preview-trunc">{lm_preview}{'&hellip;' if len(longest_msg['text']) > 150 else ''}</div>
  </div>
</div>""")

# Busiest day
w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">🔥</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Busiest Day Ever</div>
    <div class="fun-fact-value">{busiest_day['count']:,} messages</div>
    <div class="fun-fact-detail">{busiest_day['date']}</div>
  </div>
</div>""")

# Peak hour
hour_12 = peak_hour[0] % 12 or 12
am_pm = "AM" if peak_hour[0] < 12 else "PM"
w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">⏰</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Peak Chatting Hour</div>
    <div class="fun-fact-value">{hour_12}:00 {am_pm}</div>
    <div class="fun-fact-detail">{peak_hour[1]:,} messages sent during this hour</div>
  </div>
</div>""")

# Most active weekday
w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">📅</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Most Active Day of the Week</div>
    <div class="fun-fact-value">{peak_weekday[0]}</div>
    <div class="fun-fact-detail">{peak_weekday[1]:,} messages</div>
  </div>
</div>""")

# Most prolific texter
mp_author = name_for(most_prolific_msg[0]) if most_prolific_msg[0] else "?"
w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">🏆</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Most Prolific Texter</div>
    <div class="fun-fact-value">{H(mp_author)}</div>
    <div class="fun-fact-detail">{most_prolific_msg[1]:,} messages total</div>
  </div>
</div>""")

# Longest streak overall
if global_longest_streak > 0:
    w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">🔥</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Longest Streak (Any Chat)</div>
    <div class="fun-fact-value">{global_longest_streak} days</div>
    <div class="fun-fact-detail">{global_streak_start} &rarr; {global_streak_end}</div>
  </div>
</div>""")

# Longest silence
w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">🤫</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Longest Silence</div>
    <div class="fun-fact-value">{longest_gap_days:.0f} days</div>
    <div class="fun-fact-detail">{gap_start} &rarr; {gap_end}</div>
  </div>
</div>""")

# Average messages per day
w(f"""<div class="card fun-fact">
  <div class="fun-fact-icon">📊</div>
  <div class="fun-fact-body">
    <div class="fun-fact-label">Daily Average</div>
    <div class="fun-fact-value">{avg_msgs_per_day:.1f} messages/day</div>
    <div class="fun-fact-detail">Across {len(all_days):,} active days</div>
  </div>
</div>""")

# Hourly activity chart
w('<h2>Activity by Hour of Day</h2>')
w('<div class="card">')
hour_labels = [f"{h % 12 or 12}{'a' if h < 12 else 'p'}" for h in range(24)]
hour_vals = [hourly_counts.get(h, 0) for h in range(24)]
max_hour_val = max(hour_vals) if hour_vals else 1
hour_bars = ""
for i, v in enumerate(hour_vals):
    pct = v / max_hour_val * 100
    hour_bars += f'<div class="vbar" title="{i}:00 — {v:,} msgs"><div class="vbar-fill" style="height:{pct:.1f}%"></div><span class="tooltip">{hour_labels[i]}: {v:,}</span></div>'
w(f'<div class="vbars" style="height:120px">{hour_bars}</div>')
w('<div style="display:flex;justify-content:space-between;font-size:10px;color:#71717a;margin-top:4px"><span>12a</span><span>6a</span><span>12p</span><span>6p</span><span>12a</span></div>')
w('</div>')

# Weekday activity chart
w('<h2>Activity by Day of Week</h2>')
w('<div class="card">')
day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
day_vals = [weekday_counts.get(d, 0) for d in day_order]
max_day_val = max(day_vals) if day_vals else 1
day_bars = ""
for i, (d, v) in enumerate(zip(day_order, day_vals)):
    pct = v / max_day_val * 100
    day_bars += f'<div class="vbar" title="{d}: {v:,} msgs"><div class="vbar-fill" style="height:{pct:.1f}%"></div><span class="tooltip">{d[:3]}: {v:,}</span></div>'
w(f'<div class="vbars" style="height:100px">{day_bars}</div>')
w('<div style="display:flex;justify-content:space-between;font-size:10px;color:#71717a;margin-top:4px"><span>Mon</span><span>Tue</span><span>Wed</span><span>Thu</span><span>Fri</span><span>Sat</span><span>Sun</span></div>')
w('</div>')

w('</div>')  # end fun facts tab

# === 1-ON-1 INSIGHTS TAB ===
w('<div id="tab-insights" class="tab-content">')
w('<h2>1-on-1 Insights</h2>')

for cid, ch in sorted_1on1:
    partner = (ch["authors"] - {MY_AUTHOR_ID}).pop()
    pname = name_for(partner)
    streak_info = chat_streaks.get(cid, {"all": [], "longest": {"days": 0, "start": "", "end": ""}, "top_streaks": [], "longest_gap": {"days": 0, "start": "", "end": ""}, "total": 0, "avg": 0})
    top = ch.get("top_msgs", [])
    active_days = sorted(msg_dates.get(cid, []))

    w(f'<div class="card" style="margin-bottom:24px">')
    w(f'<h3 style="margin-bottom:12px">{H(pname)}</h3>')

    # Calendar heatmap
    if active_days:
        msgs_by_day = set(active_days)
        years = sorted(set(d[:4] for d in active_days))
        cal_id = cid.replace("-", "")[:12]

        # Year selector buttons
        w(f'<div class="cal-years" id="cal-years-{cal_id}">')
        for i, yr in enumerate(years):
            active_cls = " active" if i == len(years) - 1 else ""
            w(f'<button class="cal-year-btn{active_cls}" onclick="showCalYear(\'{cal_id}\',\'{yr}\',this)">{yr}</button>')
        w('</div>')

        # Render a grid for each year
        for yr in years:
            hidden = ' style="display:none"' if yr != years[-1] else ''
            w(f'<div class="cal-wrap" id="cal-{cal_id}-{yr}"{hidden}>')
            # Month labels
            w('<div class="cal-container">')
            jan1 = datetime(int(yr), 1, 1, tzinfo=timezone.utc)
            start = jan1 - timedelta(days=jan1.weekday())
            # Calculate month label positions
            month_names = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            month_positions = []
            for m in range(12):
                m_start = datetime(int(yr), m+1, 1, tzinfo=timezone.utc)
                offset = (m_start - start).days
                week_col = offset // 7
                month_positions.append((week_col, month_names[m]))
            # Day-of-week labels
            w('<div class="cal-dow"><span></span><span>M</span><span></span><span>W</span><span></span><span>F</span><span></span></div>')
            w('<div>')
            # Month labels row
            w('<div class="cal-months">')
            prev_pos = 0
            for pos, name in month_positions:
                gap = pos - prev_pos
                if gap > 0:
                    w(f'<span style="width:{gap * 12}px"></span>')
                w(f'<span>{name}</span>')
                prev_pos = pos + len(name) // 2
            w('</div>')
            # Grid
            w('<div class="cal-grid-gh">')
            for week in range(53):
                w('<div class="cal-week">')
                for dow in range(7):
                    day = start + timedelta(weeks=week, days=dow)
                    if day.year != int(yr):
                        w('<div class="cal-cell-gh" style="visibility:hidden"></div>')
                    else:
                        ds = day.strftime("%Y-%m-%d")
                        cls = "cal-cell-gh on" if ds in msgs_by_day else "cal-cell-gh"
                        w(f'<div class="{cls}" title="{ds}"></div>')
                w('</div>')
            w('</div>')
            w('</div>')
            w('</div>')
            w('</div>')
        # Legend
        w('<div class="cal-legend"><span>No msgs</span><div class="cal-legend-cell cal-cell-gh"></div><div class="cal-legend-cell cal-cell-gh on"></div><span>Messages sent</span></div>')
    else:
        w('<p style="color:#71717a;font-size:13px;margin-bottom:8px">No active days recorded.</p>')

    # Stats row
    w('<div style="display:flex;gap:32px;margin:16px 0;flex-wrap:wrap;align-items:start">')

    # Streak tower (top 5 with 3D effect)
    top_streaks = streak_info["top_streaks"]
    if top_streaks:
        w('<div>')
        w('<div class="cal-stat-label" style="margin-bottom:8px">Top Streaks</div>')
        w('<div style="display:flex;align-items:end;gap:6px">')
        for i, s in enumerate(top_streaks):
            scale = 1 - i * 0.12
            opacity = 1 - i * 0.15
            font_size = int(22 * scale)
            bar_h = max(int(48 * scale), 16)
            w(f"""<div style="text-align:center;opacity:{opacity};cursor:default" title="{s['days']} days: {s['start']} → {s['end']}">
  <div style="font-size:{font_size}px;font-weight:700;line-height:1">{s['days']}</div>
  <div style="font-size:9px;color:#71717a;margin-top:2px">days</div>
  <div style="width:28px;height:{bar_h}px;background:linear-gradient(to top,#1e3a5f,#2563eb);border-radius:4px 4px 0 0;margin:4px auto 0"></div>
  <div style="font-size:8px;color:#52525b;margin-top:2px;max-width:60px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{s['start'][-5:]}</div>
</div>""")
        w('</div>')
        w('</div>')

    # Longest silence
    gap = streak_info["longest_gap"]
    if gap["days"] > 0:
        w(f"""<div class="cal-stat">
  <div class="cal-stat-label">Longest Silence</div>
  <div class="cal-stat-value">{gap['days']} <span class="cal-stat-detail">days</span></div>
  <div class="cal-stat-detail">{gap['start']} &rarr; {gap['end']}</div>
</div>""")

    # Total streaks + avg
    w(f"""<div class="cal-stat">
  <div class="cal-stat-label">Total Streaks</div>
  <div class="cal-stat-value">{streak_info['total']}</div>
</div>""")
    w(f"""<div class="cal-stat">
  <div class="cal-stat-label">Avg Length</div>
  <div class="cal-stat-value">{streak_info['avg']:.1f} <span class="cal-stat-detail">days</span></div>
</div>""")

    w('</div>')

    # Top longest messages
    if top:
        w('<div style="margin-top:8px"><div class="fun-fact-label" style="margin-bottom:8px">Top 10 Longest Messages</div>')
        w("""<table><thead><tr>
  <th class="num">#</th>
  <th class="num">Chars</th>
  <th>Sender</th>
  <th class="hide-mobile">Date</th>
  <th>Message</th>
</tr></thead><tbody>""")
        for i, (length, author, text, date) in enumerate(top, 1):
            sender = name_for(author)
            full_text = html.escape(text)
            if len(text) > 120:
                truncated = html.escape(text[:120]) + "&hellip;"
                w(f"""<tr>
  <td class="num">{i}</td>
  <td class="num">{length:,}</td>
  <td class="name-cell">{H(sender)}</td>
  <td class="hide-mobile">{date}</td>
  <td class="msg-cell" onclick="this.querySelector('.msg-full').style.display=this.querySelector('.msg-full').style.display==='block'?'none':'block';this.querySelector('.msg-trunc').style.display=this.querySelector('.msg-full').style.display==='block'?'none':'block'"><span class="msg-trunc">{truncated}</span><span class="msg-full" style="display:none">{full_text}</span></td>
</tr>""")
            else:
                w(f"""<tr>
  <td class="num">{i}</td>
  <td class="num">{length:,}</td>
  <td class="name-cell">{H(sender)}</td>
  <td class="hide-mobile">{date}</td>
  <td class="msg-cell">{full_text}</td>
</tr>""")
        w("</tbody></table></div>")
    else:
        w('<p style="color:#71717a;font-size:13px;margin-top:8px">No text messages in this chat.</p>')

    w('</div>')  # end chat card

w('</div>')  # end insights tab

# === FULL DETAILS TAB ===
w('<div id="tab-detail" class="tab-content">')
w('<h2>1-on-1 Chat Details</h2>')

for cid, ch in sorted_1on1:
    partner = (ch["authors"] - {MY_AUTHOR_ID}).pop()
    pname = name_for(partner)
    avg_len = ch["text_len_total"] / ch["text_msg_count"] if ch["text_msg_count"] else 0
    my_pct = ch["outgoing"] / ch["total"] * 100 if ch["total"] else 0
    their_pct = ch["incoming"] / ch["total"] * 100 if ch["total"] else 0

    max_ch_month = max(ch["monthly"].values()) if ch["monthly"] else 1
    spark = "".join(
        f'<div class="vbar" title="{m}: {cnt}"><div class="vbar-fill" style="height:{cnt/max_ch_month*100:.1f}%"></div><span class="tooltip">{m}: {cnt}</span></div>'
        for m, cnt in sorted(ch["monthly"].items())
    )

    len_bars = ""
    for bk in BUCKET_ORDER:
        bc = ch["len_buckets"].get(bk, 0)
        if bc:
            len_bars += f'<div class="len-bucket"><span class="len-swatch" style="background:{BUCKET_COLORS[BUCKET_ORDER.index(bk)]}"></span>{bk}: {bc}</div>'

    w(f"""<div class="card">
<h3 style="display:flex;justify-content:space-between;align-items:center">
  <span>{H(pname)}</span>
  <span style="font-size:14px;font-weight:400;color:#71717a">{ch['total']} messages</span>
</h3>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin:12px 0">
  <div><div style="color:#71717a;font-size:12px">Incoming</div><div style="font-size:24px;font-weight:700">{ch['incoming']} <span style="font-size:14px;color:#22c55e">({their_pct:.0f}%)</span></div></div>
  <div><div style="color:#71717a;font-size:12px">Outgoing</div><div style="font-size:24px;font-weight:700">{ch['outgoing']} <span style="font-size:14px;color:#4f8cf7">({my_pct:.0f}%)</span></div></div>
  <div><div style="color:#71717a;font-size:12px">Avg Length</div><div style="font-size:24px;font-weight:700">{avg_len:.1f} <span style="font-size:14px;font-weight:400;color:#71717a">chars</span></div></div>
  <div><div style="color:#71717a;font-size:12px">Total Text</div><div style="font-size:24px;font-weight:700">{fmt_num(ch['text_len_total'])} <span style="font-size:14px;font-weight:400;color:#71717a">chars</span></div></div>
  <div><div style="color:#71717a;font-size:12px">Months Active</div><div style="font-size:24px;font-weight:700">{len(ch['monthly'])}</div></div>
</div>
<div style="display:flex;gap:8px;height:40px;margin:8px 0;border-radius:8px;overflow:hidden">
  <div style="flex:{my_pct:.1f};background:#4f8cf7;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;min-width:fit-content;padding:0 8px">You: {ch['outgoing']}</div>
  <div style="flex:{their_pct:.1f};background:#22c55e;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;min-width:fit-content;padding:0 8px">{H(pname)}: {ch['incoming']}</div>
</div>
<div style="margin:12px 0">
  <div style="font-size:12px;color:#a1a1aa;margin-bottom:4px">Monthly Activity</div>
  <div class="vbars" style="height:80px">{spark}</div>
</div>
""")
    if len_bars:
        w(f"""<div style="margin:8px 0">
  <div style="font-size:12px;color:#a1a1aa;margin-bottom:4px">Message Length Distribution</div>
  <div>{len_bars}</div>
</div>""")
    w('<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px">')
    for month, count in ch["monthly"].most_common(10):
        w(f'<span style="background:#27272a;padding:3px 8px;border-radius:4px;font-size:12px">{month}: {count}</span>')
    w("</div></div>")

if groups:
    w('<h2>Group Chat Details</h2>')
    for cid, ch in sorted_groups:
        avg_len = ch["text_len_total"] / ch["text_msg_count"] if ch["text_msg_count"] else 0
        cname = chat_label(cid, ch["authors"])
        my_pct = ch["outgoing"] / ch["total"] * 100 if ch["total"] else 0
        their_pct = ch["incoming"] / ch["total"] * 100 if ch["total"] else 0

        max_ch_month = max(ch["monthly"].values()) if ch["monthly"] else 1
        spark = "".join(
            f'<div class="vbar" title="{m}: {cnt}"><div class="vbar-fill" style="height:{cnt/max_ch_month*100:.1f}%"></div><span class="tooltip">{m}: {cnt}</span></div>'
            for m, cnt in sorted(ch["monthly"].items())
        )

        w(f"""<div class="card">
<h3 style="display:flex;justify-content:space-between;align-items:center">
  <span>Group {cid} <span style="font-size:14px;font-weight:400;color:#71717a">({len(ch['authors'])} members)</span></span>
  <span style="font-size:14px;font-weight:400;color:#71717a">{ch['total']} messages</span>
</h3>
<p style="color:#a1a1aa;font-size:13px;margin-bottom:8px">{H(cname)}</p>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin:12px 0">
  <div><div style="color:#71717a;font-size:12px">Incoming</div><div style="font-size:24px;font-weight:700">{ch['incoming']}</div></div>
  <div><div style="color:#71717a;font-size:12px">Outgoing</div><div style="font-size:24px;font-weight:700">{ch['outgoing']}</div></div>
  <div><div style="color:#71717a;font-size:12px">Avg Length</div><div style="font-size:24px;font-weight:700">{avg_len:.1f} <span style="font-size:14px;font-weight:400;color:#71717a">chars</span></div></div>
  <div><div style="color:#71717a;font-size:12px">Total Text</div><div style="font-size:24px;font-weight:700">{fmt_num(ch['text_len_total'])} <span style="font-size:14px;font-weight:400;color:#71717a">chars</span></div></div>
  <div><div style="color:#71717a;font-size:12px">Months Active</div><div style="font-size:24px;font-weight:700">{len(ch['monthly'])}</div></div>
</div>
<div style="display:flex;gap:8px;height:40px;margin:8px 0;border-radius:8px;overflow:hidden">
  <div style="flex:{my_pct:.1f};background:#4f8cf7;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;min-width:fit-content;padding:0 8px">You: {ch['outgoing']}</div>
  <div style="flex:{their_pct:.1f};background:#22c55e;display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:600;min-width:fit-content;padding:0 8px">Incoming: {ch['incoming']}</div>
</div>
<div style="margin:12px 0">
  <div style="font-size:12px;color:#a1a1aa;margin-bottom:4px">Monthly Activity</div>
  <div class="vbars" style="height:80px">{spark}</div>
</div>
<div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:8px">
""")
        for month, count in ch["monthly"].most_common(10):
            w(f'<span style="background:#27272a;padding:3px 8px;border-radius:4px;font-size:12px">{month}: {count}</span>')
        w("</div></div>")

w("</div>")

w("""
<script>
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  document.querySelector(`.tab[onclick*="'${name}'"]`).classList.add('active');
  // Resize plotly charts in the shown tab
  setTimeout(() => {
    document.querySelectorAll('#tab-' + name + ' .js-plotly-plot').forEach(el => Plotly.Plots.resize(el));
  }, 50);
}
function showCalYear(id, year, btn) {
  const parent = btn.parentElement;
  parent.querySelectorAll('.cal-year-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  // Hide all grids for this calendar, show the selected year
  const prefix = 'cal-' + id + '-';
  document.querySelectorAll('[id^="' + prefix + '"]').forEach(el => el.style.display = 'none');
  document.getElementById(prefix + year).style.display = '';
}
</script>
</body>
</html>""")

with open("stats.html", "w") as f:
    f.write("\n".join(html_parts))

print("Generated stats.html")
