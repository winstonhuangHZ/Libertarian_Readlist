"""Typeset the public-domain texts fetched as plain text or wiki source.

Usage:  python3 typeset/pd_texts.py locke|we-ru

Both are life+70 public domain in the EU: Locke died 1704, Zamyatin 1937.
"""

import json
import os
import re
import subprocess
import sys

import bookkit as bk

OUT = "typeset"


# ---------------------------------------------------------------- Locke ----

def locke_paragraphs(text):
    """Split PG's hard-wrapped text into blocks keyed by 'Sect. N.'."""
    body = text.split("*** START OF THE PROJECT GUTENBERG EBOOK", 1)[1]
    body = body.split("*** END OF THE PROJECT GUTENBERG EBOOK", 1)[0]
    start = body.find("Book II")
    body = body[start:]
    blocks, chapter, title_lines = [], None, []
    buf = []
    for raw in body.split("\n"):
        line = raw.rstrip()
        if not line.strip():
            if buf:
                blocks.append((" ".join(buf).strip(), chapter))
                buf = []
            continue
        stripped = line.strip()
        m = re.match(r"^CHAPTER\.\s*([IVXL]+)\.?\s*$", stripped)
        if m:
            if buf:
                blocks.append((" ".join(buf).strip(), chapter))
                buf = []
            chapter = {"label": "Chapter " + m.group(1), "title": ""}
            title_lines = []
            blocks.append(("@@CHAPTER@@", chapter))
            continue
        if (chapter is not None and not chapter.get("body_started")
                and not stripped.startswith("Sect.")):
            if len(stripped) > 1 and not stripped.startswith("("):
                title_lines.append(stripped)
                chapter["title"] = " ".join(title_lines)
                continue
        if chapter is not None:
            chapter["body_started"] = True
        buf.append(stripped)
    if buf:
        blocks.append((" ".join(buf).strip(), chapter))
    return blocks


def locke():
    text = open("sources/locke_second_treatise.txt", encoding="utf-8").read()
    blocks = locke_paragraphs(text)
    out = [bk.preamble("Second Treatise of Government", "John Locke")]
    out.append(bk.title_page("Second Treatise of Government", "John Locke",
                             ["1690"]))
    out.append(r"""\pagenumbering{roman}
\pagestyle{fancy}
\phantomsection
\chapter*{A Note on This Edition}
\addcontentsline{toc}{chapter}{A Note on This Edition}
\markboth{A Note on This Edition}{A Note on This Edition}

The text is John Locke's \emph{Second Treatise of Government} as printed in
1690. Locke died in 1704, so the work is in the public domain in the European
Union (and everywhere else with a life-plus-seventy term). The transcription
used here is the Project Gutenberg edition (eBook 7370), which states that it
contains only the original 1690 words and none of the editorial matter of any
later edition. Paragraph numbers are Locke's own section numbers.

\tableofcontents
\clearpage
\pagenumbering{arabic}
\pagestyle{fancy}
""")
    for text_block, chapter in blocks:
        if text_block == "@@CHAPTER@@":
            title = smart_title(chapter["title"])
            out.append("\\bookchapter{%s}{%s}\n" % (chapter["label"], title))
            continue
        if re.fullmatch(r"Book [IVX]+", text_block):
            continue                    # PG's part heading, already on the title page
        t = re.sub(r"</?i>", "*", text_block)
        out.append(bk.para_tex({"runs": _runs(t)}) + "\n")
    out.append("\n\\end{document}\n")
    return "\n".join(out), None


def _runs(text):
    """Turn a *starred* span into italic runs."""
    runs, italic = [], False
    for part in re.split(r"\*", text):
        if part:
            runs.append({"italic": italic, "text": part})
        italic = not italic
    return runs or [{"italic": False, "text": ""}]


SMALL = {"of", "the", "and", "in", "on", "to", "a", "an", "or", "for", "by",
         "with", "from", "as", "at", "into", "upon"}


def smart_title(text):
    """Title-case an all-caps heading, keeping small words lowercase."""
    words = text.title().split()
    return " ".join(w if i == 0 or w.lower() not in SMALL else w.lower()
                    for i, w in enumerate(words))


# ------------------------------------------------------------------- We ----

def we_pages():
    pages = json.load(open("sources/we_pages.json", encoding="utf-8"))
    out = []
    for name, raw in pages:
        label = re.search(r"\|\s*ЧАСТЬ\s*=\s*(.+)", raw)
        label = label.group(1).strip() if label else name
        text = re.sub(r"\{\{[^{}]*\}\}", "", raw, flags=re.S)
        text = re.sub(r"<ref[^>]*>.*?</ref>", "", text, flags=re.S)
        text = re.sub(r"<div[^>]*>|</div>", "", text)
        text = text.replace("'''", "").replace("''", "*")
        text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
        text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = text.replace("&nbsp;", " ").replace("&mdash;", "\u2014")
        text = text.replace("&laquo;", "«").replace("&raquo;", "»")
        centered, paras, buf = [], [], []
        for raw_line in text.split("\n"):
            line = raw_line.strip()
            if not line:
                if buf:
                    paras.append(" ".join(buf))
                    buf = []
                continue
            paras.append(line) if line else None
        out.append({"label": label, "blocks": paras})
    return out


def we_ru():
    pages = we_pages()
    out = [WE_PREAMBLE]
    out.append(WE_TITLE)
    out.append(WE_NOTE)
    for i, page in enumerate(pages, 1):
        label = page["label"]
        out.append("\\bookchapterplain{%s}\n" % label)
        blocks = page["blocks"]
        body = []
        for b in blocks:
            if re.match(r"^\*?Запись\s+\d+", b.strip("*")) \
                    or re.match(r"^\*?Конспект", b.strip("*")):
                continue
            if b.startswith("*") and b.endswith("*"):
                body.append(("center", b.strip("*")))
            elif b.isupper() and len(b) < 90:
                body.append(("subtitle", b))
            else:
                body.append(("para", b))
        for kind, b in body:
            if kind == "center":
                continue
            if kind == "subtitle":
                out.append("\\begin{center}\\textsc{%s}\\end{center}\n"
                           % bk.render(b, [False] * len(b)))
            else:
                out.append(bk.para_tex({"runs": _runs(b)}) + "\n")
    out.append("\n\\end{document}\n")
    return "\n".join(out), "xelatex"


def we_en():
    """Zilboorg's 1924 English translation (public domain in the US)."""
    raw = open("sources/we_en_zilboorg.txt", encoding="utf-8").read()
    raw = raw.split("*** START OF THE PROJECT GUTENBERG", 1)[1]
    raw = raw.split("*** END OF THE PROJECT GUTENBERG", 1)[0]
    lines = raw.split("\n")

    records, cur, buf = [], None, []
    for line in lines:
        s = line.strip()
        if re.fullmatch(r"RECORD [A-Z]+", s):
            if cur:
                records.append((cur, buf))
            cur, buf = s.title(), []
            continue
        if cur is None:
            continue
        if not s:
            if buf and buf[-1] != "":
                buf.append("")
            continue
        buf.append(s)
    if cur:
        records.append((cur, buf))

    out = [bk.preamble("We", "Yevgeny Zamyatin")]
    out.append(bk.title_page("We", "Yevgeny Zamyatin",
                             ["Translated by Gregory Zilboorg", "1924"]))
    out.append(r"""\pagenumbering{roman}
\pagestyle{fancy}
\phantomsection
\chapter*{A Note on This Edition}
\addcontentsline{toc}{chapter}{A Note on This Edition}
\markboth{A Note on This Edition}{A Note on This Edition}

The text is Gregory Zilboorg's English translation of Yevgeny Zamyatin's
\emph{We}, published in New York in 1924 (E. P. Dutton). Under United States
law every work published before 1929 has entered the public domain, so this
translation became free on 1 January 2020; it is also free in countries with
a life-plus-fifty term, since Zamyatin died in 1937 and Zilboorg in 1959.
It is \emph{not} yet public domain in the European Union, where the
translator's life-plus-seventy term runs to 1 January 2030.

The translation is a product of its time: it renders Zamyatin's Russian into
the English of the 1920s, and it condenses some passages. The Russian
original, set from the Wikisource text, is issued separately as
\emph{Мы}.

\tableofcontents
\clearpage
\pagenumbering{arabic}
\pagestyle{fancy}
""")
    for title, buf in records:
        out.append("\\bookchapterplain{%s}\n" % title)
        block = "\n".join(buf)
        for para in block.split("\n\n"):
            text = " ".join(x.strip() for x in para.split("\n") if x.strip())
            if not text:
                continue
            if len(text) < 70 and text[0].isalpha() and text.count(".") <= 1 \
                    and text[:1].isupper() and not text.endswith("."):
                out.append("\\begin{center}\\textsc{%s}\\end{center}\n"
                           % bk.render(text, [False] * len(text)))
            else:
                out.append(bk.para_tex({"runs": _runs(text.replace("_", "*"))})
                           + "\n")
    out.append("\n\\end{document}\n")
    return "\n".join(out), None


WE_PREAMBLE = r"""\documentclass[11pt,openany]{book}
\usepackage{fontspec}
\setmainfont{LinLibertine_R.otf}[Ligatures=TeX,
  BoldFont=LinLibertine_RB.otf, ItalicFont=LinLibertine_RI.otf,
  BoldItalicFont=LinLibertine_RBI.otf]
\setsansfont{LinBiolinum_R.otf}[
  BoldFont=LinBiolinum_RB.otf, ItalicFont=LinBiolinum_RI.otf]
\usepackage{polyglossia}
\setmainlanguage{russian}
\setotherlanguage{english}
\usepackage{microtype}
\usepackage{titlesec}
\usepackage{fancyhdr}
\usepackage{geometry}
\usepackage[hidelinks]{hyperref}
\geometry{paperwidth=6in,paperheight=9in,
  inner=0.78in,outer=0.66in,top=0.78in,bottom=0.86in}
\setlength{\parindent}{1.2em}
\setlength{\parskip}{0pt}
\linespread{1.06}
\hypersetup{pdftitle={Мы},pdfauthor={Евгений Замятин}}
\titleformat{\chapter}[display]{\normalfont\sffamily}{}{0pt}
  {\Large\bfseries\raggedright}[\vspace{8pt}{\titlerule[0.5pt]}]
\titlespacing*{\chapter}{0pt}{56pt}{30pt}
\newcommand{\bookchapterplain}[1]{%
  \clearpage\phantomsection\chapter*{#1}%
  \addcontentsline{toc}{chapter}{#1}\markboth{#1}{#1}\thispagestyle{plain}}
\titleformat{\tableofcontents}[block]{\normalfont\sffamily\bfseries\Large}{}{0pt}{}
\renewcommand{\contentsname}{Содержание}
\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.3pt}
\fancyhead[LE]{\sffamily\small\thepage}
\fancyhead[LO]{\sffamily\small\itshape\nouppercase{\rightmark}}
\fancyhead[RE]{\sffamily\small\itshape\nouppercase{\leftmark}}
\fancyhead[RO]{\sffamily\small\thepage}
\fancypagestyle{plain}{\fancyhf{}\renewcommand{\headrulewidth}{0pt}%
  \fancyfoot[C]{\sffamily\small\thepage}}
\begin{document}
"""

WE_TITLE = r"""\begin{titlepage}
\centering
\vspace*{1.7in}
{\sffamily\bfseries\fontsize{40}{46}\selectfont МЫ\par}
\vspace{0.30in}
{\sffamily\large Евгений Замятин\par}
\vspace{0.18in}
{\sffamily\small 1920\par}
\vfill
\end{titlepage}
"""

WE_NOTE = r"""\pagenumbering{roman}
\pagestyle{fancy}
\phantomsection
\chapter*{Note on This Edition}
\addcontentsline{toc}{chapter}{Note on This Edition}
\markboth{Note on This Edition}{Note on This Edition}
\begin{english}
This is Yevgeny Zamyatin's \emph{We} in the original Russian. Zamyatin died
in 1937, so under the European Union's life-plus-seventy rule the Russian
text has been in the public domain since 1 January 2008. The text was taken
from the Russian Wikisource transcription, which is marked PD-old-70.

No English translation is included. Every English version of \emph{We} is
still protected in the EU: the 1924 Gregory Zilboorg translation is free in
the United States but its translator died in 1959, so it enters the EU public
domain only on 1 January 2030; the later translations (Ginsburg 1972, Brown
1993, Randall 2005, Aplin 2009) are further away still.
\end{english}

\tableofcontents
\clearpage
\pagenumbering{arabic}
\pagestyle{fancy}
"""


def main():
    which = sys.argv[1]
    tex, engine = {"locke": locke, "we-ru": we_ru, "we-en": we_en}[which]()
    slug = {"locke": "Second_Treatise", "we-ru": "We_Russian",
            "we-en": "We_English"}[which]
    workdir = os.path.join(OUT, slug)
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, slug + ".tex")
    open(path, "w", encoding="utf-8").write(tex)
    engine = engine or "pdflatex"
    for _ in range(2):
        subprocess.run([engine, "-interaction=nonstopmode", slug + ".tex"],
                       cwd=workdir, capture_output=True)
    print(engine, "->", os.path.join(workdir, slug + ".pdf"))


if __name__ == "__main__":
    main()
