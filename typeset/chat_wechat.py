"""Read a WeChat chat export into the same shape as the DingTalk transcript.

Usage:  python3 typeset/chat_wechat.py <chat.jsonl> --out-prefix <path>
                                              [--json messages.json]

The export is one JSON object per message: a type code, the sender, a unix
time and a `content` that is either the text itself or the XML WeChat stores
for everything else (pictures, stickers, links, files, forwarded chats and
quoted replies).  This turns each of those into the line a reader would want
to see -- "[图片]", "[文件] 报告.pdf", or the quoted text under the reply --
and writes the conversation out the way chat_book.py already knows how.

No OCR is involved: unlike the screenshots, a WeChat export keeps the text.
"""

import datetime
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

import chat_transcript as ct

SELF = None                     # filled in from the export's own fields

SIMPLE = {3: "[图片]", 34: "[语音]", 43: "[视频]", 47: "[表情]"}
APPMSG = {"1": "链接", "5": "链接", "6": "文件", "19": "聊天记录", "24": "笔记",
          "33": "小程序", "36": "小程序", "40": "聊天记录", "51": "视频号",
          "62": "视频号", "63": "直播", "7": "视频号", "74": "文件", "82": "链接",
          "87": "笔记", "2000": "转账", "2001": "红包"}


def xml_of(raw):
    try:
        return ET.fromstring(re.sub(r"&#\d+;", "", raw))
    except ET.ParseError:
        return None


def appmsg_text(root):
    """(text, quoted author, quoted text) for one <appmsg>."""
    app = root.find("appmsg")
    if app is None:
        return None, None, None
    kind = app.findtext("type") or ""
    title = (app.findtext("title") or "").strip()
    if kind == "57":                       # a reply that quotes an earlier line
        ref = app.find("refermsg")
        who = ref.findtext("fromusr") if ref is not None else None
        quoted = (ref.findtext("content") or "").strip() if ref is not None else ""
        return title, ("我" if who == SELF else "ben") if who else None, quoted
    if kind in ("51", "62", "63", "7"):    # video-channel card: title is a stub
        feed = app.find("finderFeed")
        desc = (feed.findtext("desc") or "").strip() if feed is not None else ""
        nick = (feed.findtext("nickname") or "").strip() if feed is not None else ""
        body = desc or nick or title
        return "[%s] %s" % (APPMSG.get(kind, "卡片"), body), None, None
    label = APPMSG.get(kind, "卡片")
    body = title
    if kind in ("6", "74"):
        size = app.find("appattach")
        ext = (size.findtext("fileext") or "").strip() if size is not None else ""
        body = ("%s.%s" % (title, ext)) if ext and not title.endswith(ext) else title
    if kind == "19":
        des = (app.findtext("des") or "").strip()
        body = "%s（%s）" % (title, " ".join(des.split()[:6])) if des else title
    return "[%s] %s" % (label, body.strip()), None, None


def line_of(row):
    """One exported message as {side, text, quote, stamp}."""
    kind = row.get("messageType")
    raw = str(row.get("content") or "")
    text, quote_author, quoted = None, None, None
    if kind == 1:
        text = raw.strip()
    elif kind in SIMPLE:
        text = SIMPLE[kind]
    elif kind == 42:
        root = xml_of(raw)
        name = (root.findtext(".//nickname") or "").strip() if root is not None else ""
        text = "[名片] %s" % name
    elif kind == 48:
        root = xml_of(raw)
        where = ""
        if root is not None:
            loc = root.find("location")
            if loc is not None:
                where = (loc.get("poiname") or loc.get("label") or "").strip()
        text = "[位置] %s" % where
    elif kind == 50:
        root = xml_of(raw)
        what = (root.findtext(".//msg") or "").strip() if root is not None else ""
        text = "[通话] %s" % what
    elif kind == 49:
        root = xml_of(raw)
        if root is not None:
            text, quote_author, quoted = appmsg_text(root)
    if not text:
        text = "[%s]" % (kind if kind is not None else "未知")
    when = datetime.datetime.fromtimestamp(row.get("createTime", 0))
    return {"side": "mine" if row.get("fromSelf") else "theirs",
            "text": text, "quote": quoted or "",
            "quote_author": quote_author or "",
            "stamp": "%d/%d %02d:%02d" % (when.month, when.day, when.hour,
                                          when.minute),
            "ts": int(row.get("createTime", 0))}


def load(path, self_wxid=None):
    global SELF
    rows = [json.loads(l) for l in open(path)]
    if not self_wxid:
        for row in rows:
            if row.get("fromSelf"):
                self_wxid = row.get("fromUser")
                break
    SELF = self_wxid
    rows.sort(key=lambda r: r.get("createTime", 0))
    messages = [line_of(r) for r in rows]
    for msg in messages:                      # attach the turn's date/clock
        when = datetime.datetime.fromtimestamp(msg["ts"])
        msg["date"] = when.date()             # a date, like the chats elsewhere
        msg["clock"] = "%02d:%02d" % (when.hour, when.minute)
    return messages


def main():
    path = sys.argv[1]
    prefix = sys.argv[sys.argv.index("--out-prefix") + 1]
    messages = load(path)
    chat = [m for m in messages if m["text"]]
    title = os.path.splitext(os.path.basename(path))[0]
    head = ["# %s（微信）" % title, "",
            "由微信导出（jsonl）直接读取，共 %d 条。图片、表情、语音、"
            "文件按类型标注，不收录内容。" % len(chat), ""]
    body = ct.render([{**m, "quote": ("%s: %s" % (m["quote_author"], m["quote"]))
                       if m["quote"] and m["quote_author"] else m["quote"]}
                      for m in chat])
    text = "\n".join(head + body) + "\n"
    open(prefix + ".md", "w").write(text)
    open(prefix + ".txt", "w").write(
        text.replace("**", "").replace("- ", "  ", 1))
    if "--json" in sys.argv:
        json.dump([{**m, "date": str(m["date"])} for m in messages],
                  open(sys.argv[sys.argv.index("--json") + 1], "w"),
                  ensure_ascii=False)
    print("messages:", len(chat), "->", prefix + ".md")


if __name__ == "__main__":
    main()
