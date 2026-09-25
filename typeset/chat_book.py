"""Set a DingTalk transcript as a book: plain text and typeset PDF.

Usage:  python3 typeset/chat_book.py <parts.json> --out-prefix <path>
                [--author NAME] [--tex-only]

The manifest is the one chat_transcript.py already reads.  Each screenshot
PDF becomes a part; each date in the conversation becomes a chapter; every
message is a paragraph with its speaker in the margin of the line.  Quotes
are set small and indented under the message that quotes them, and DingTalk's
own notices run centred, the way the app draws them.
"""

import datetime
import json
import os
import re
import subprocess
import sys

import bookkit as bk

import chat_transcript as ct

RULE = "-" * 72


def escape(text):
    return "".join(bk.tex_escape(ch) for ch in text)


def date_of(stamp):
    """(month, day) of a stamp, or None when it only carries a clock."""
    m = re.match(r"^(\d{1,2})/(\d{1,2})", stamp or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def clock(stamp):
    m = re.search(r"(\d{1,2}:\d{2})", stamp or "")
    return m.group(1) if m else ""


def title_of(day, year):
    names = "一二三四五六日"
    when = datetime.date(year, *day)
    return "%d月%d日\u3000星期%s" % (day[0], day[1], names[when.weekday()])


def chapters(messages, start_year=2025):
    """Messages grouped by date, with a weekday for each date heading.

    The screenshots carry no year, so it is inferred: the captures are from
    November 2025 and the conversation runs forward from September.
    """
    year, prev, current = start_year, None, None
    groups = []
    for msg in messages:
        day = date_of(msg.get("stamp"))
        if day and prev and day[0] < prev[0]:
            year += 1                      # the conversation crossed new year
        if day:
            prev = day
        # a stamp that carries only a clock stays inside the day it belongs to
        chapter = day or current
        if not groups or chapter != groups[-1][0]:
            groups.append((chapter, title_of(chapter, year) if chapter else None,
                           []))
        groups[-1][2].append(msg)
        current = chapter
    return groups


def line_of(msg):
    """One message as (speaker, text, quote, notice)."""
    if msg["side"] == "notice":
        return None, None, None, msg["text"]
    who = "我" if msg["side"] == "mine" else "ben"
    return who, msg["text"], msg["quote"], None


# ------------------------------------------------------------------- text

def text_book(parts, manifest):
    out = ["%s" % manifest.get("title", "钉钉聊天记录"), "=" * 72, "",
           "由钉钉全屏截图 OCR 而成。说话人按气泡底色区分："
           "「我」为蓝色气泡，对方为白色气泡。", "",
           "图片、表情、语音不收录；粘贴截图里的文字按规则剔除。", ""]
    for i, (part, messages, pages) in enumerate(parts, 1):
        out += ["", RULE,
                "第 %d 部分 / %d：%s（%d 页）"
                % (i, len(parts), os.path.basename(part["pdf"]), pages), RULE, ""]
        for day, title, msgs in chapters(messages):
            if title:
                out += ["", title, ""]
            for msg in msgs:
                who, text, quote, notice = line_of(msg)
                if notice:
                    out.append("        %s" % notice)
                    continue
                out.append("  %s：%s" % (who, text))
                if quote:
                    out.append("        ［引用 %s］" % quote)
    return "\n".join(out) + "\n"


# --------------------------------------------------------------- latex book

PREAMBLE = r"""\documentclass[10pt,openany,fontset=mac]{ctexbook}

\usepackage[a5paper]{geometry}
\geometry{inner=15mm, outer=13mm, top=16mm, bottom=18mm}
\usepackage{titlesec}
\usepackage{fancyhdr}
\usepackage{xcolor}
\usepackage[hidelinks]{hyperref}

\setlength{\parindent}{0pt}
\setlength{\parskip}{0pt}
\linespread{1.12}
\frenchspacing
\hypersetup{pdftitle={@@TITLE@@},pdfauthor={@@AUTHOR@@}}

\titleformat{\chapter}[display]{\normalfont\sffamily}{}{0pt}
  {\Large\bfseries\raggedright}[\vspace{6pt}{\titlerule[0.5pt]}]
\titlespacing*{\chapter}{0pt}{40pt}{24pt}

\fancypagestyle{chat}{%
  \fancyhf{}\fancyhead[LE,RO]{\small\sffamily\thepage}%
  \fancyhead[RE]{\small\sffamily\leftmark}%
  \fancyhead[LO]{\small\sffamily\rightmark}\renewcommand{\headrulewidth}{0pt}}
\pagestyle{chat}

% a date heading, and the seam between two of the source files
\newcommand{\chatday}[1]{\par\addvspace{20pt}\phantomsection
  \noindent{\sffamily\bfseries\large #1}\par\addvspace{7pt}%
  \addcontentsline{toc}{section}{#1}\markboth{#1}{#1}}
\newcommand{\chatseam}[2]{\clearpage\thispagestyle{empty}%
  \vspace*{0.28\textheight}%
  {\sffamily\small\color{black!60}#1}\par\vspace{8pt}%
  {\sffamily\Large\bfseries #2}\par\vspace{10pt}%
  {\color{black!30}\hrule height 0.5pt}\clearpage
  \addcontentsline{toc}{chapter}{#1\quad #2}\markboth{#2}{#2}}

% one message: the speaker hangs in the left margin, the text wraps under it
\newcommand{\chatmsg}[2]{\par\vspace{1.4pt}\noindent
  \hangindent=3.6em\hangafter=1
  \makebox[3.6em][l]{\sffamily\bfseries\small #1}#2\par}
\newcommand{\chatquote}[1]{\par\vspace{0.6pt}\noindent\hspace*{3.6em}%
  \parbox[t]{\dimexpr\linewidth-3.6em\relax}%
  {\small\color{black!55}\itshape #1}\par\vspace{0.6pt}}
\newcommand{\chatnotice}[1]{\par\vspace{2.4pt}%
  \centerline{\footnotesize\color{black!55}#1}\vspace{1.6pt}}

\begin{document}
\frontmatter
"""


def note_on_edition(parts, total, pages_total):
    rows = "\n".join(
        r"\item %s（%d 页，%s 至 %s）"
        % (escape(os.path.basename(p["pdf"])), pages,
           day_range(msgs)[0], day_range(msgs)[1])
        for p, msgs, pages in parts)
    return r"""\chapter*{编者说明}
\addcontentsline{toc}{chapter}{编者说明}
\markboth{编者说明}{编者说明}

这份文字来自 @@NFILES@@ 个 PDF 里的钉钉桌面端全屏截图，共 @@NPAGES@@ 页、
@@NMSGS@@ 条消息。

\begin{itemize}\small
@@ROWS@@
\end{itemize}

每页截图都被切掉了窗口本身的东西——左侧会话列表、顶部搜索栏、聊天
标题、底部输入框——只留下对话区，再用 PP-OCRv6 识别。说话人不是猜
的：钉钉给自己的气泡上蓝色、给对方的上白色，颜色就是归属；引用块在
气泡里缩进一级；头像角标贴着右边缘；日期、已读回执和撤回提示直接画
在面板底色上，据此分开。

相邻截图是滚动的，会重叠一到两条消息，所以上一页的结尾要和下一页的
开头对齐合并，取读得更全的那份；被裁在页边的半句话因此能补回来。

\textbf{收录范围与局限}：图片、表情、语音、文件不收录，粘贴在聊天里
的截图和照片也不收录（连同照片里的文字一起剔除），所以对话中那些
「图」的位置是空的。繁体字和粘贴截图里的小字识别得差一些，个别字
可能读错。翻译工具与 AI 自动附在消息尾部的水印文字按原样保留。
每一页被剔除的行都在同目录的 \texttt{审计\_*.md} 里逐条列出，可以
对照原截图核对。

截图本身不带年份。日期按截图拍摄时间（2025 年 11 月）和对话的先后
顺序推定为 2025 年，因而星期几也是据此算出的。

\clearpage
""".replace("@@NFILES@@", str(len(parts))).replace("@@NPAGES@@", str(pages_total)) \
   .replace("@@NMSGS@@", "%d" % total).replace("@@ROWS@@", rows)


def first_day(messages):
    for msg in messages:
        day = date_of(msg.get("stamp"))
        if day:
            return day
    return (1, 1)


def last_day(messages):
    for msg in reversed(messages):
        day = date_of(msg.get("stamp"))
        if day:
            return day
    return (1, 1)


def day_range(messages):
    """First and last date as printable text, e.g. ("9月14日", "11月14日")."""
    first, last = first_day(messages), last_day(messages)
    if first == (1, 1) or last == (1, 1):
        return "日期未读出", ""
    return "%d月%d日" % first, "%d月%d日" % last


def tex_book(parts, manifest):
    title = manifest.get("title", "钉钉聊天记录")
    out = [PREAMBLE.replace("@@TITLE@@", title)
                   .replace("@@AUTHOR@@", manifest.get("author", ""))]
    out += [r"\begin{titlepage}\centering",
            r"\vspace*{0.26\textheight}",
            r"{\sffamily\Huge\bfseries %s\par}" % escape(title),
            r"\vspace{1.4em}",
            r"{\sffamily\large %s\par}" % escape(manifest.get("subtitle", "")),
            r"\vfill",
            r"{\small\sffamily %s\par}" % escape(manifest.get("author", "")),
            r"\end{titlepage}",
            r"\markboth{%s}{%s}" % (escape(title), escape(title)),
            note_on_edition(parts, sum(len(m) for _, m, _ in parts),
                            sum(p for _, _, p in parts)),
            r"\tableofcontents", r"\clearpage", r"\mainmatter"]
    for i, (part, messages, pages) in enumerate(parts, 1):
        name = os.path.basename(part["pdf"])
        kicker = ("文件分割 %d/%d：接续 %s" % (i, len(parts),
                  os.path.basename(parts[i - 2][0]["pdf"])) if i > 1
                  else "文件 %d/%d" % (1, len(parts)))
        out.append(r"\chatseam{%s}{%s}" % (escape(kicker), escape(name)))
        for day, day_title, msgs in chapters(messages):
            if day_title:
                out.append(r"\chatday{%s}" % escape(day_title))
            last_clock = None
            for msg in msgs:
                stamp = clock(msg.get("stamp"))
                if stamp and stamp != last_clock:
                    out.append(r"\chatnotice{%s}" % escape(stamp))
                    last_clock = stamp
                who, text, quote, notice = line_of(msg)
                if notice:
                    out.append(r"\chatnotice{%s}" % escape(notice))
                    continue
                out.append(r"\chatmsg{%s}{%s}" % (who, escape(text)))
                if quote:
                    out.append(r"\chatquote{引用 %s}" % escape(quote))
    out.append(r"\end{document}")
    return "\n".join(out) + "\n"


def main():
    manifest = json.load(open(sys.argv[1]))
    prefix = sys.argv[sys.argv.index("--out-prefix") + 1]
    parts = []
    for part in manifest["parts"]:
        messages, dropped, kept, pages = ct.build(part["pdf"], part["cache"])
        parts.append((part, messages, pages))
        print("%-22s %4d pages  %5d messages"
              % (os.path.basename(part["pdf"]), pages, len(messages)))

    open(prefix + ".txt", "w").write(text_book(parts, manifest))
    tex = tex_book(parts, manifest)
    open(prefix + ".tex", "w").write(tex)
    print("wrote", prefix + ".txt", "and", prefix + ".tex")
    if "--tex-only" in sys.argv:
        return
    for _ in range(2):                    # twice: contents, then page numbers
        r = subprocess.run(["xelatex", "-interaction=nonstopmode",
                            os.path.basename(prefix) + ".tex"],
                           cwd=os.path.dirname(prefix) or ".",
                           capture_output=True, text=True)
    log = r.stdout
    if not os.path.exists(prefix + ".pdf"):
        print(log[-3000:])
        sys.exit("xelatex failed")
    print("wrote", prefix + ".pdf")


if __name__ == "__main__":
    main()
