"""Measure a DingTalk transcript: volume, hours, speakers, recurring themes.

Usage:  python3 typeset/chat_analysis.py <parts.json> [--json out.json]

Reads the same manifest as chat_book.py.  Every message is given a date and a
clock from the nearest marker above it, which is what lets the conversation
be counted by day, by hour and by theme over time.
"""

import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

import chat_transcript as ct

# themes worth following through five months; each is a set of spellings the
# conversation actually uses
THEMES = {
    "Paul": [r"paul"],
    "TEDx": [r"tedx", r"ted\s?x"],
    "学生会／会长": [r"學生會", r"学生会", r"會長", r"会长", r"學生會長"],
    "seminar": [r"seminar"],
    "考试／quiz": [r"考試", r"考试", r"quiz", r"midterm", r"期中考"],
    "拉黑／免打扰": [r"拉黑", r"免打擾", r"免打扰", r"mute", r"block"],
    "睡觉": [r"睡覺", r"睡觉", r"睡了", r"睡著", r"睡着"],
    "AI 工具": [r"gpt", r"deepseek", r"gemini", r"claude", r"ai\b"],
    "巴黎／旅行": [r"巴黎", r"凱旋門", r"凯旋门", r"明信片"],
    "吃": [r"吃", r"奶茶", r"牛角包", r"茶歇"],
}

MONTHS = {9: "9月", 10: "10月", 11: "11月", 6: "6月", 7: "7月", 8: "8月"}


def timed(messages, start_year=2025):
    """Messages with (date, clock) filled down from the markers above them."""
    out, date, clock, year = [], None, None, start_year
    prev_month = None
    for msg in messages:
        stamp = msg.get("stamp")
        if stamp:
            m = re.match(r"^(\d{1,2})/(\d{1,2})", stamp)
            if m:
                if prev_month and int(m.group(1)) < prev_month:
                    year += 1
                prev_month = int(m.group(1))
                date = datetime.date(year, int(m.group(1)), int(m.group(2)))
            t = re.search(r"(\d{1,2}):(\d{2})", stamp)
            if t:
                clock = "%02d:%02d" % (int(t.group(1)), int(t.group(2)))
        out.append({**msg, "date": date, "clock": clock})
    return out


def normalised(text):
    return re.sub(r"\s+", "", text)


def themes_of(text):
    low = text.lower()
    return [name for name, pats in THEMES.items()
            if any(re.search(p, low) for p in pats)]


def analyse(parts):
    messages = []
    for part in parts:
        msgs, dropped, kept, pages = ct.build(part["pdf"], part["cache"])
        dated = timed(msgs)
        for msg in dated:
            msg["source"] = os.path.basename(part["pdf"])
        messages += dated
    chat = [m for m in messages if m["side"] != "notice"]

    per_day, per_hour, per_month = Counter(), Counter(), Counter()
    per_day_side = defaultdict(Counter)
    speaker = Counter()
    chars = Counter()
    theme_day = defaultdict(Counter)
    longest = max(chat, key=lambda m: len(m["text"]))
    for msg in chat:
        who = "我" if msg["side"] == "mine" else "ben"
        speaker[who] += 1
        chars[who] += len(msg["text"])
        if msg["date"]:
            day = msg["date"].isoformat()
            per_day[day] += 1
            per_day_side[day][who] += 1
            per_month["%d-%02d" % (msg["date"].year, msg["date"].month)] += 1
            for theme in themes_of(msg["text"]):
                theme_day[theme][day] += 1
        if msg["clock"]:
            per_hour[int(msg["clock"][:2])] += 1

    hour_side = defaultdict(Counter)
    for msg in chat:
        who = "我" if msg["side"] == "mine" else "ben"
        if msg["clock"]:
            hour_side[int(msg["clock"][:2])][who] += 1

    days = sorted(per_day)
    return {
        "total": len(chat),
        "notices": len(messages) - len(chat),
        "speaker": dict(speaker),
        "chars": dict(chars),
        "mean_len": {k: round(chars[k] / speaker[k], 1) for k in speaker},
        "days": len(days),
        "span": [days[0], days[-1]],
        "per_day": dict(per_day),
        "per_day_side": {d: dict(v) for d, v in per_day_side.items()},
        "per_hour": dict(per_hour),
        "hour_side": {str(h): dict(v) for h, v in hour_side.items()},
        "per_month": dict(per_month),
        "themes": {t: dict(v) for t, v in theme_day.items()},
        "busiest": sorted(per_day.items(), key=lambda kv: -kv[1])[:8],
        "longest": {"text": longest["text"], "len": len(longest["text"]),
                    "date": longest["date"].isoformat() if longest["date"] else None},
    }


def main():
    manifest = json.load(open(sys.argv[1]))
    result = analyse(manifest["parts"])
    if "--json" in sys.argv:
        json.dump(result, open(sys.argv[sys.argv.index("--json") + 1], "w"),
                  ensure_ascii=False, indent=1)
    print("messages", result["total"], "over", result["days"], "days")
    print("speaker", result["speaker"], "mean length", result["mean_len"])
    print("busiest days", result["busiest"][:5])
    print("hours", sorted(result["per_hour"].items(), key=lambda kv: -kv[1])[:6])


if __name__ == "__main__":
    main()
