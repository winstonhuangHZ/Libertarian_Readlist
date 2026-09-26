"""Read the emotional register of a chat transcript without a sentiment model.

Usage:  python3 typeset/chat_sentiment.py <parts.json> [--json out.json]
                                              [--samples out.md]

Off-the-shelf Chinese sentiment models are the wrong instrument here.  Run
against this conversation they put "谢谢你" and "祝你好运" in the negative
class and "哈哈笑死我了" in the most negative one: they were trained on
reviews, where a polite formula carries no feeling, and they cannot see that
"拉黑没商量" between two friends is a joke.  What the log does show, plainly
and auditably, is *register*: how much of the talk is laughter, how much is
worry about the other person, how much is asking to be answered, how much is
the block-and-unblock game, and how much is exam fatigue.  Those counts are
lexical, so every one of them can be checked against the messages it caught.
"""

import datetime
import json
import os
import re
import sys
from collections import Counter, defaultdict

import chat_analysis as ca
import chat_transcript as ct

REGISTER = {
    "玩笑／笑": r"哈哈|笑死|笑不活|笑喷|乐死|嘿嘿|🤣|😂|笑死我|离谱|离谱|6{3,}|"
                r"太草|草了|梗|活该|逆天",
    "道谢／客气": r"謝謝|谢谢|感謝|感谢|麻煩|麻烦|辛苦|拜託|拜托|thanks|thank you|thx",
    "关心对方": r"早点睡|早點睡|注意休息|多休息|休息好|加油|保重|没事吧|沒事吧|你还好吗|"
                r"你還好嗎|怎么了|怎麼了|要不要|我帮你|我幫你|别难过|別難過|别气|別氣|"
                r"喝点水|吃饭了吗|吃药|看病|保重身体|没有关系|沒關係|没关系|没事|沒事|"
                r"别担心|別擔心|不怕|我在|我陪|抱抱|摸摸|辛苦|可怜|可憐|放心|会好|會好|"
                r"慢慢来|慢慢來|别急|別急|先睡|你先|养病|養病",
    "催回复／请求": r"求你|求你了|帮我|幫我|帮帮|幫幫|快点|快點|说话|說話|在吗|在嗎|在不在|"
                    r"你人呢|你人在哪|回我|为什么不|為什麼不|怎么不|你倒是|说啊|說啊",
    "拉黑／免打扰": r"拉黑|免打擾|免打扰|mute|block|关进小黑屋|打開免打擾|解除|unmute",
    "生气／讨厌": r"生气|生氣|讨厌|討厭|烦死|煩死|好烦|好煩|滚|滾|闭嘴|閉嘴|不理你|"
                r"别煩我|別煩我|气死|氣死|服了|我恨|恨你|过分|過分",
    "疲惫／压力": r"好累|很累|累死|撑不住|撐不住|睡不着|睡不著|焦虑|焦慮|崩溃|崩潰|压力|壓力|"
                r"不想写|不想寫|写不完|寫不完|考砸|摆烂|擺爛|困死|没力气|沒力氣",
    "亲密称呼": r"会长|會長|老哥|兄弟|哥们|哥們|亲爱的|親愛的|会长大人|會長大人",
    "AI／工具": r"gpt|deepseek|gemini|claude|chatgpt",
}

# the categories that answer the question "does the other person notice?"
WORRY = "疲惫／压力"
CARE = "关心对方"


def weeks_of(messages):
    """Messages keyed by ISO week, in order, for each of the source files."""
    out = defaultdict(list)
    for msg in messages:
        if msg["date"]:
            y, w, _ = msg["date"].isocalendar()
            out["%d-W%02d" % (y, w)].append(msg)
    return dict(sorted(out.items()))


def counts(messages):
    per_week = defaultdict(Counter)
    per_speaker = defaultdict(Counter)
    examples = defaultdict(list)
    for msg in messages:
        if msg["side"] == "notice" or not msg["date"]:
            continue
        who = "我" if msg["side"] == "mine" else "ben"
        y, w, _ = msg["date"].isocalendar()
        key = "%d-W%02d" % (y, w)
        for name, pat in REGISTER.items():
            if re.search(pat, msg["text"], re.I):
                per_week[key][name] += 1
                per_speaker[who][name] += 1
                if len(examples[name]) < 400:
                    examples[name].append((msg["date"].isoformat(), who, msg["text"]))
    return per_week, per_speaker, examples


def cared_for(messages, reach=15):
    """When one of them says they are worn out, does the other answer with care?

    The unit is the *turn*, not the message: one side often sends four or
    five lines in a row, so a fixed window of the next few messages would
    count their own words as the reply.  Instead walk forward to the next
    time the other person speaks, and judge that answer.
    """
    hits, table = [], Counter()
    for i, msg in enumerate(messages):
        if msg["side"] == "notice" or not re.search(REGISTER[WORRY], msg["text"]):
            continue
        who = "我" if msg["side"] == "mine" else "ben"
        other, seen = [], 0
        for later in messages[i + 1:i + 1 + reach]:
            if later["side"] == "notice":
                continue
            seen += 1
            if ("我" if later["side"] == "mine" else "ben") == who:
                if other:
                    break              # the other side already had its turn
                continue
            other.append(later)
        table[(who, "有人回应")] += 1 if other else 0
        table[(who, "总数")] += 1
        if other:
            kind = "关心" if any(re.search(REGISTER[CARE], m["text"])
                               for m in other) else "没关心"
            table[(who, kind)] += 1
            hits.append((msg, other, kind))
    return table, hits


# ---------------------------------------------------------------- balance

ASKED = (r"说话|說啊|你人呢|你人在哪|在吗|在嗎|回我|为什么不|為什麼不|你倒是|"
         r"你特么|你他妈|where are you|不回|喂")
SOLID = (r"https?://|分享链接|\.pdf|下载|文件|資料|资料|日程|时间表|時間表|"
         r"答案|帮你|幫你|给你看|发你|发我|表格")
SORRY = r"對不起|对不起|我錯了|我错了|抱歉|sry|sorry|原諒|原谅"
WITHDRAW = r"免打扰|免打擾|mute|不解|解我|呆在|block|拉黑"


def balance(messages):
    """Who opens, who goes quiet, who takes things back, who does the work.

    The conversation is not symmetrical, and the interesting part is *how*:
    one side pursues and takes words back, the other side answers, does the
    practical favours, and occasionally withholds.
    """
    chat = [m for m in messages if m["side"] != "notice"]
    notices = [m for m in messages if m["side"] == "notice"]

    def who(msg):
        return "我" if msg["side"] == "mine" else "ben"

    days = {}
    for msg in chat:
        if msg["date"]:
            days.setdefault(msg["date"], []).append(msg)
    opener = Counter(who(v[0]) for v in days.values())

    silences = Counter()
    for a, b in zip(chat, chat[1:]):
        if not all([a["date"], b["date"], a["clock"], b["clock"]]):
            continue
        t1 = datetime.datetime.combine(
            a["date"], datetime.time(int(a["clock"][:2]), int(a["clock"][3:])))
        t2 = datetime.datetime.combine(
            b["date"], datetime.time(int(b["clock"][:2]), int(b["clock"][3:])))
        hours = (t2 - t1).total_seconds() / 3600
        if 24 <= hours < 240:
            silences[who(b)] += 1

    taken_back = Counter()
    for notice in notices:
        name = re.search(r"（系统消息）(.+?)撤回", notice["text"])
        taken_back[name.group(1) if name else "?"] += 1

    ledger = Counter()
    support = Counter()
    for msg in chat:
        side, text = who(msg), msg["text"]
        month = "%d-%02d" % (msg["date"].year, msg["date"].month) if msg["date"] else "?"
        if re.search(SORRY, text, re.I):
            ledger[(side, "道歉")] += 1
        if re.search(WITHDRAW, text, re.I):
            ledger[(side, "提拉黑／免打扰")] += 1
        if re.search(ASKED, text, re.I):
            ledger[(side, "催回复／追问")] += 1
        if side == "ben" and re.search(SOLID, text, re.I):
            support[month] += 1
    return {
        "opener": dict(opener),
        "days": len(days),
        "silences_broken": dict(silences),
        "taken_back": dict(taken_back),
        "ledger": {"%s|%s" % k: v for k, v in ledger.items()},
        "ben_support_by_month": dict(sorted(support.items())),
    }


def main():
    manifest = json.load(open(sys.argv[1]))
    everything = []
    for part in manifest["parts"]:
        msgs, dropped, kept, pages = ct.build(part["pdf"], part["cache"])
        everything += ca.timed(msgs)
    chat = [m for m in everything if m["side"] != "notice"]

    per_week, per_speaker, examples = counts(chat)
    table, cared = cared_for(chat)
    result = {
        "per_week": {k: dict(v) for k, v in per_week.items()},
        "per_speaker": {k: dict(v) for k, v in per_speaker.items()},
        "care": {"%s|%s" % k: v for k, v in table.items()},
        "balance": balance(everything),
        "total": len(chat),
    }
    if "--json" in sys.argv:
        json.dump(result, open(sys.argv[sys.argv.index("--json") + 1], "w"),
                  ensure_ascii=False, indent=1)
    if "--samples" in sys.argv:
        with open(sys.argv[sys.argv.index("--samples") + 1], "w") as fh:
            for name in REGISTER:
                fh.write("\n## %s（%d 条样例）\n" % (name, len(examples[name])))
                for date, who, text in examples[name][:40]:
                    fh.write("- %s %s：%s\n" % (date, who, text[:120]))
    for name in REGISTER:
        n = sum(v.get(name, 0) for v in per_speaker.values())
        mine = per_speaker["我"].get(name, 0)
        print("%-12s %5d 次（我 %d / ben %d）" % (name, n, mine, n - mine))
    print("\n对方喊累/难受之后的 3 条内回应：")
    for who in ("我", "ben"):
        total = table[(who, "总数")]
        if not total:
            continue
        print("  %s 喊累 %d 次 · 对方回了 %d 次 · 其中带关心 %d 次"
              % (who, total, table[(who, "有人回应")], table[(who, "关心")]))
    bal = result["balance"]
    print("\n谁先开口（每天第一条）：%s（共 %d 天）"
          % (bal["opener"], bal["days"]))
    print("24 小时以上的静默由谁打破：%s" % bal["silences_broken"])
    print("撤回：%s" % bal["taken_back"])
    for key in sorted(bal["ledger"]):
        print("  %-16s %d" % (key, bal["ledger"][key]))
    print("ben 发链接／文件／资料／答案：%s（合计 %d）"
          % (bal["ben_support_by_month"], sum(bal["ben_support_by_month"].values())))
    print("\n样例已写出" if "--samples" in sys.argv else "")


if __name__ == "__main__":
    main()
