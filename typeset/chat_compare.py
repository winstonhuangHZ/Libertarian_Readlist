"""Put the DingTalk and WeChat records of one friendship side by side.

Usage:  python3 typeset/chat_compare.py <dingtalk parts.json> <wechat.jsonl>

Same measures on both, because the point is the comparison: the two
platforms cover different months, and how they are used differs.
"""

import collections
import json
import sys

import chat_analysis as ca
import chat_sentiment as cs
import chat_transcript as ct
import chat_wechat as cw


def measures(messages, name):
    chat = [m for m in messages if m.get("side") != "notice"]
    days = collections.defaultdict(list)
    for msg in chat:
        if msg.get("date"):
            days[str(msg["date"])].append(msg)
    months = collections.Counter(str(m["date"])[:7] for m in chat if m.get("date"))
    hours = collections.Counter(int(m["clock"][:2]) for m in chat
                                if m.get("clock"))
    mine = sum(1 for m in chat if m["side"] == "mine")
    chars = sum(len(m["text"]) for m in chat)
    per_week, per_speaker, _ = cs.counts(chat)
    reg = collections.Counter()
    for counts in per_speaker.values():
        reg.update(counts)
    table, _ = cs.cared_for(chat)
    bal = cs.balance(messages)
    return {
        "name": name,
        "messages": len(chat),
        "days": len(days),
        "span": [min(days), max(days)] if days else ["", ""],
        "share_mine": round(100 * mine / max(1, len(chat))),
        "mean_chars": round(chars / max(1, len(chat)), 1),
        "busiest_hour": hours.most_common(1)[0][0] if hours else None,
        "night_share": round(100 * sum(n for h, n in hours.items()
                                       if h >= 22 or h < 6) / max(1, sum(hours.values()))),
        "months": dict(sorted(months.items())),
        "register": dict(reg),
        "opener": bal["opener"],
        "silences_broken": bal["silences_broken"],
        "ledger": bal["ledger"],
        "ben_support": sum(bal["ben_support_by_month"].values()),
        "care": {"%s|%s" % k: v for k, v in table.items()},
    }


def main():
    manifest = json.load(open(sys.argv[1]))
    dingtalk = []
    for part in manifest["parts"]:
        msgs, dropped, kept, pages = ct.build(part["pdf"], part["cache"])
        dingtalk += ca.timed(msgs)
    wechat = cw.load(sys.argv[2])
    out = [measures(dingtalk, "钉钉"), measures(wechat, "微信")]
    for row in out:
        print("== %s" % row["name"])
        for key in ("messages", "days", "span", "share_mine", "mean_chars",
                    "busiest_hour", "night_share"):
            print("   %-14s %s" % (key, row[key]))
        print("   每月：%s" % row["months"])
        print("   语域：%s" % row["register"])
        print("   谁先开口：%s   打破沉默：%s" % (row["opener"], row["silences_broken"]))
        print("   账本：%s" % row["ledger"])
        print("   对方(ben)信息型支持：%d 条" % row["ben_support"])
    if "--json" in sys.argv:
        json.dump(out, open(sys.argv[sys.argv.index("--json") + 1], "w"),
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
