"""Rebuild a DingTalk conversation from its screenshot OCR cache.

Usage:  python3 typeset/chat_transcript.py <pdf> <cache-dir>
            [--json out.json] [--dropped out.txt] [--out out.md]
        python3 typeset/chat_transcript.py <parts.json> [--out out.md]

The second form joins several PDFs into one transcript, marking where one
file ends and the next begins:  {"title": ..., "parts": [{"pdf":, "cache":}]}

Reads what chat_ocr.py cached and turns lines into messages.  The screenshots
give three things the transcript needs:

  * the colour behind a line -- a blue bubble is mine, a white one is the
    other party's, and the pane's own background means the line is a date
    marker, a "Read" receipt or a "1 reply" chip rather than a message;
  * the x of a line -- the avatar's corner badge sits hard against the right
    edge, and a quoted block is indented inside the bubble that quotes it;
  * the page number -- consecutive screenshots overlap, so the end of one
    page and the start of the next have to be matched and merged, not
    concatenated.
"""

import difflib
import json
import os
import re
import sys
from collections import Counter

import chat_ocr

# a date marker is centred in the pane and reads like a date or a clock
STAMP = re.compile(r"^\d{1,2}/\d{1,2}\s*\d{1,2}[:.·]\d{2}$|"
                   r"^\d{1,2}/\d{1,2}$|"
                   r"^\d{1,2}:\d{2}$|^[昨今]\w{0,2}$|^Yesterday$|"
                   r"^\d{4}年\d{1,2}月\d{1,2}日")
DROP = re.compile(r"^(Read|已读|\d*\s*repl(y|ies)?\s*(Read|已读)?|T[oa]-\s?Do s?|"
                  r"Calendar|Body|Send|钉钉)$", re.I)
# DingTalk's own notices, centred on the pane: somebody recalled something
NOTICE = re.compile(
    r"^(?:(?P<who>.{1,20}?)\s+)?recalled\s+(?:a\s+)?(?:message|file)$"
    r"|^(?:You've|You have)\s+recalled\s+(?:a\s+)?(?:message|file)$"
    r"|^撤回了一条?(?:消息|文件)$", re.I)
# the corner badge of my own avatar, e.g. "WH"
BADGE = re.compile(r"^[A-Z]{1,3}$")


def pane_colour(data):
    """The colour of the chat pane itself.

    Read receipts and date markers always sit straight on it, so whichever
    colour they share is the pane; everything else is judged against that.
    """
    seen = Counter()
    for line in data["lines"]:
        if DROP.match(line["text"].strip()) or STAMP.match(line["text"].strip()):
            seen[tuple(line["bg"])] += 1
    return list(seen.most_common(1)[0][0]) if seen else [246, 248, 250]


def colour_kind(bg, pane):
    """What the pixels behind a line say it is."""
    r, g, b = bg
    if r >= 200 and b - r >= 20:
        return "mine"                    # a blue bubble: my message
    if sum(abs(a - c) for a, c in zip(bg, pane)) <= 8:
        return "paper"                   # the pane itself: markers, receipts
    if min(bg) >= 236 and max(bg) - min(bg) <= 12:
        return "theirs"                  # a white bubble: their message
    return "picture"                     # text read off a photo or a screenshot


def read_page(path):
    """Lines worth keeping, and -- for the record -- the ones thrown away."""
    data = json.load(open(path))
    pane_w = data["box"][2] - data["box"][0]
    pane_h = data["box"][3] - data["box"][1]
    pane = pane_colour(data)
    out, dropped, weak = [], [], []
    for line in data["lines"]:
        line = dict(line)
        line["flat"] = line.get("flat", 1.0)
        height = line["y1"] - line["y0"]
        line["kind"] = colour_kind(line["bg"], pane)
        if line["kind"] == "picture":
            dropped.append((line, "read off a picture"))
            continue
        if DROP.match(line["text"].strip()):
            dropped.append((line, "window furniture"))
            continue
        if line["kind"] == "mine" and line["x0"] > pane_w - 100:
            dropped.append((line, "my avatar's badge"))
            continue
        if BADGE.match(line["text"]) and line["x0"] > pane_w - 100:
            dropped.append((line, "my avatar's badge"))
            continue
        if line["kind"] == "paper":
            text = line["text"].strip()
            if STAMP.match(text):
                # a date or a clock drawn straight on the pane is a marker;
                # the same text on a bubble would be a message
                line["kind"] = "stamp"
                out.append(line)
                continue
            if NOTICE.match(text):
                line["kind"] = "notice"
                out.append(line)
                continue
            dropped.append((line, "pane background, not a bubble"))
            continue
        # A bubble's text is whatever the app set it to, so a line clipped by
        # the bottom of the screenshot still belongs to the conversation; the
        # next screenshot will have it whole.
        clipped = line["y1"] > pane_h - 20
        if line["kind"] == "theirs" and not clipped:
            # Small type on a white bubble is a screenshot pasted into the
            # chat, not the chat: the app's own text is taller and flatter.
            if height < 34 and not (line["conf"] >= 0.95 and line["flat"] >= 0.6):
                dropped.append((line, "read off a pasted screenshot"))
                continue
        if line["conf"] < 0.90:
            weak.append(line)
            continue
        out.append(line)

    # A shaky reading is not the same as a bad line.  Text inside a pasted
    # screenshot comes in dense runs -- a table, a page of notes -- while a
    # real message that the recogniser merely struggled with stands alone.
    # Keep the loners, drop the clusters, and record both.
    for line in weak:
        height = max(line["y1"] - line["y0"], 30)
        neighbours = sum(1 for other in weak
                         if other is not line
                         and abs(other["y0"] - line["y0"]) < 3 * height)
        if (neighbours < 2 and line["conf"] >= 0.6
                and len(normalise(line["text"])) >= 4):
            line["weak"] = True
            out.append(line)
        else:
            dropped.append((line, "low confidence"
                            + ("（成簇）" if neighbours else "")))
    out.sort(key=lambda l: (l["y0"], l["x0"]))
    return out, pane_w, dropped


def bubbles(lines, pane_w):
    """Group lines into bubbles and mark what each line is inside one."""
    out = []
    for line in lines:
        text = line["text"].strip()
        if STAMP.match(text):
            out.append({"type": "stamp", "text": text, "x0": line["x0"],
                        "y0": line["y0"]})
            continue
        side = line["kind"]
        height = line["y1"] - line["y0"]
        if (out and out[-1]["type"] == "line" and out[-1]["side"] == side
                and line["y0"] - out[-1]["lines"][-1]["y1"] < height * 1.2):
            out[-1]["lines"].append(line)
        else:
            out.append({"type": "line", "side": side, "y0": line["y0"],
                        "lines": [line]})
    return out


AUTHOR = re.compile(r"^.{1,24}[:：]$")
RECALL = re.compile(r"(?:\S+\s+)?(?:recalled a message|撤回了一条?消息)"
                    r"(?:\s+\S+\s+(?:recalled a message|撤回了一条?消息))*")


def tidy(text):
    """DingTalk's own notices, which sit inside the bubbles."""
    return RECALL.sub("（撤回了一条消息）", text).strip()


def message(block):
    """One bubble as {side, time, quote, text}."""
    lines = block["lines"]
    if block["side"] == "notice":
        text = " ".join(l["text"].strip() for l in lines)
        found = NOTICE.match(lines[0]["text"].strip())
        who = found.group("who") if found else None
        if not who or who.lower().replace(" ", "") in ("you've", "youhave"):
            who = "我"
        return {"side": "notice", "quote": "",
                "text": "（系统消息）%s撤回了一条消息" % who}
    quote, body = [], []
    inside = False
    edge = min(l["x0"] for l in lines)
    for i, line in enumerate(lines):
        text = line["text"].strip()
        if i == 0 and AUTHOR.match(text):
            inside = True                     # "Winston Huang:" opens a quote
        if inside and line["x0"] >= edge + 15:
            quote.append(text)
            continue
        inside = False
        body.append(text)
    text = tidy(" ".join(body))
    quoted = " ".join(quote).strip(" :：")
    if AUTHOR.match(text) and len(lines) == 1:
        # a quote whose own message was a picture: only the author survived
        return {"side": block["side"], "quote": "",
                "text": "（引用 %s 的消息，正文为图片）" % text.rstrip(":：")}
    if not text and quoted:
        text = "（引用 %s 的消息，正文为图片）" % quoted.rstrip(":：")
        quoted = ""
    return {"side": block["side"], "quote": quoted, "text": text}


def page_messages(lines, pane_w):
    """Messages of one page, in order, each with its nearest date above it."""
    out, stamp = [], None
    for block in bubbles(lines, pane_w):
        if block["type"] == "stamp":
            stamp = block["text"]
            continue
        msg = message(block)
        if not msg["text"]:
            continue
        msg["stamp"] = stamp
        out.append(msg)
    return out


def same(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()


def normalise(text):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", text)


def stitch(pages):
    """Join overlapping pages: match the tail of one against the head of the
    next and keep whichever reading of a repeated message is fuller."""
    out = []
    for msgs in pages:
        best, best_score = 0, 0.0
        for n in range(1, min(len(out), len(msgs), 8) + 1):
            hits = [same(normalise(a["text"]), normalise(b["text"]))
                    for a, b in zip(out[-n:], msgs[:n])]
            if min(hits) < 0.78:
                continue               # every message of the overlap must match
            score = sum(hits) / n
            if score > best_score:
                best, best_score = n, score
        for a, b in zip(out[-best:] if best else [], msgs[:best]):
            if len(b["text"]) > len(a["text"]):
                a["text"] = b["text"]          # the later page clipped less
            a["stamp"] = a["stamp"] or b["stamp"]
        out.extend(msgs[best:])
    return out


def render(messages):
    """The conversation itself, one bullet per message."""
    out = []
    stamp = None
    for msg in messages:
        now = tidy_stamp(msg["stamp"]) if msg["stamp"] else None
        if now and now != stamp:
            stamp = now
            out += ["", "**%s**" % stamp, ""]
        if msg["side"] == "notice":
            out.append("- %s" % msg["text"])
            continue
        who = "我" if msg["side"] == "mine" else "ben"
        if msg["quote"]:
            out.append("- %s：%s" % (who, msg["text"]))
            out.append("  > 引用 %s" % msg["quote"])
        else:
            out.append("- %s：%s" % (who, msg["text"]))
    return out


def header(title, pages, source):
    return ["# %s" % title, "",
            "由 %d 页全屏截图 OCR 而成（%s）。说话人按气泡底色区分："
            "「我」为蓝色气泡，对方为白色气泡。" % (pages, source), ""]


def tidy_stamp(text):
    """The OCR often closes the gap in "11/12 22:35", or opens it as a dot."""
    m = re.match(r"^(\d{1,2}/\d{1,2})\s*(\d{1,2})[:.·](\d{2})$", text)
    return "%s %s:%s" % m.groups() if m else text


def build(pdf, cache):
    """Read one screenshot PDF's cache into a list of messages."""
    files = sorted(f for f in os.listdir(cache) if f.endswith(".json"))
    pages, dropped, kept = [], [], []
    for name in files:
        lines, pane_w, gone = read_page(os.path.join(cache, name))
        pages.append(page_messages(lines, pane_w))
        dropped += [(name, line, why) for line, why in gone]
        kept += [(name, line) for line in lines]
    return stitch(pages), dropped, kept, len(files)


def audit(dropped):
    return "".join("%s  %-28s  %s\n" % (name, why, line["text"])
                   for name, line, why in dropped)


def _n_grams(text, n=6):
    return {text[i:i + n] for i in range(max(1, len(text) - n + 1))}


def lost_candidates(dropped, messages):
    """Dropped lines that the transcript does not contain anywhere else.

    Consecutive screenshots overlap, so a message clipped by the bottom of
    one page is usually read again on the next.  A dropped line that matches
    no surviving message is the only kind worth checking by hand.
    """
    index = {}
    for i, msg in enumerate(messages):
        for gram in _n_grams(normalise(msg["text"])):
            index.setdefault(gram, set()).add(i)
    out = []
    for name, line, why in dropped:
        if why in ("window furniture", "my avatar's badge"):
            continue
        if line["kind"] not in ("mine", "theirs"):
            continue
        text = line["text"].strip()
        norm = normalise(text)
        if len(norm) < 3 or line["conf"] < 0.6:
            continue
        near = set()
        for gram in _n_grams(norm):
            near |= index.get(gram, set())
        best = 0.0
        for i in near:
            other = normalise(messages[i]["text"])
            if norm in other or other in norm:
                best = 1.0
                break
            best = max(best, same(norm, other))
        if best < 0.8:
            out.append((name, line, why, best))
    return out


def audit_report(title, dropped, kept, messages):
    """What was thrown away, and what is worth a second look.

    Two lists matter.  A dropped line that sat on a bubble colour could be a
    real message lost to a rule; a kept line whose background was busy is
    probably text read off a picture that the rules let through.  Both are
    listed with the source page so they can be checked against the
    screenshot.
    """
    counts = Counter(why for _, _, why in dropped)
    out = ["# %s — 审计" % title, "",
           "OCR 读到 %d 行，保留 %d 行，丢弃 %d 行。"
           % (len(kept) + len(dropped), len(kept), len(dropped)), "",
           "| 原因 | 行数 |", "| --- | --- |"]
    out += ["| %s | %d |" % (why, n) for why, n in counts.most_common()]

    risky = lost_candidates(dropped, messages)
    out += ["", "## 可能真的丢了（气泡底色，且在别处找不到同一条）", "",
            "相邻截图重叠，被裁在页边的消息通常会在下一页读全，所以下面这些"
            "是唯一值得人工核对的。抽查时重点看这些页。", ""]
    if not risky:
        out.append("（无）")
    by_page = Counter(name for name, _, _, _ in risky)
    out += ["| 截图页 | 候选行数 | 该页样例 |", "| --- | --- | --- |"]
    for name, n in by_page.most_common():
        sample = next(l["text"] for nm, l, _, _ in risky if nm == name)
        out.append("| 第 %d 页 | %d | %s |" % (int(name[:4]) + 1, n, sample[:34]))
    out += ["", "### 逐条", ""]
    for name, line, why, best in risky:
        out.append("- 第 %d 页 · %s · %s · conf %.2f · 最像 %.2f · %s"
                   % (int(name[:4]) + 1, why,
                      "我" if line["kind"] == "mine" else "对方",
                      line["conf"], best, line["text"]))
    return "\n".join(out) + "\n"


def join(parts):
    """One transcript out of several files, with the seams marked."""
    out = ["# %s" % parts["title"], "",
           "钉钉聊天记录转录，由全屏截图 OCR 而成。说话人按气泡底色区分："
           "「我」为蓝色气泡，对方为白色气泡。", ""]
    audit_dir = parts.get("audit_dir")
    for i, part in enumerate(parts["parts"], 1):
        name = os.path.basename(part["pdf"])
        if i > 1:
            out += ["", "---", "",
                    "## ⑂ 文件分割 %d/%d：接续 %s → %s"
                    % (i, len(parts["parts"]),
                       os.path.basename(parts["parts"][i - 2]["pdf"]), name),
                    ""]
        else:
            out += ["", "## 文件 1/%d：%s" % (len(parts["parts"]), name), ""]
        messages, dropped, kept, pages = build(part["pdf"], part["cache"])
        out += render(messages)
        print("%-22s %4d pages  %5d messages  %4d lines dropped"
              % (name, pages, len(messages), len(dropped)), file=sys.stderr)
        if audit_dir:
            stem = os.path.splitext(name)[0]
            open(os.path.join(audit_dir, "审计_%s.md" % stem), "w").write(
                audit_report(stem, dropped, [l for _, l in kept], messages))
    return "\n".join(out) + "\n"


def main():
    if sys.argv[1].endswith(".json") and not os.path.isdir(sys.argv[1]):
        parts = json.load(open(sys.argv[1]))
        text = join(parts)
        if "--out" in sys.argv:
            open(sys.argv[sys.argv.index("--out") + 1], "w").write(text)
        sys.stdout.write(text)
        return

    pdf, cache = sys.argv[1], sys.argv[2]
    messages, dropped, kept, pages = build(pdf, cache)
    title = os.path.splitext(os.path.basename(pdf))[0]
    text = "\n".join(header(title, pages, os.path.basename(pdf))
                     + render(messages)) + "\n"
    if "--json" in sys.argv:
        json.dump(messages, open(sys.argv[sys.argv.index("--json") + 1], "w"),
                  ensure_ascii=False, indent=1)
    if "--dropped" in sys.argv:
        open(sys.argv[sys.argv.index("--dropped") + 1], "w").write(audit(dropped))
    if "--out" in sys.argv:
        open(sys.argv[sys.argv.index("--out") + 1], "w").write(text)
    if "--audit" in sys.argv:
        open(sys.argv[sys.argv.index("--audit") + 1], "w").write(
            audit_report(os.path.splitext(os.path.basename(pdf))[0], dropped,
                         [l for _, l in kept], messages))
    sys.stdout.write(text)
    print("messages:", len(messages), " dropped lines:", len(dropped),
          file=sys.stderr)


if __name__ == "__main__":
    main()
