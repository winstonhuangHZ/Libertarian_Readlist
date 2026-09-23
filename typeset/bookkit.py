"""Shared tooling: pull a book out of a PDF text layer and emit LaTeX.

Built for the earlier 1984 job and generalised here. The pipeline is:

    extract lines -> drop printed furniture -> rebuild paragraphs
    -> de-hyphenate -> emit LaTeX -> compile -> verify word by word
"""

import json
import re

import fitz

LIGATURES = {
    "\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
    "\ufb04": "ffl", "\u017f": "s",
}

ESCAPES = {
    "\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "$": r"\$",
    "&": r"\&", "#": r"\#", "%": r"\%", "_": r"\_",
    "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
}


def clean(text):
    for lig, plain in LIGATURES.items():
        text = text.replace(lig, plain)
    text = text.replace("\u00a0", " ").replace("\u00ad", "")
    return re.sub(r"[ \t]+", " ", text)


# --------------------------------------------------------------------------
# extraction


def extract_lines(doc, first=0, last=None):
    """Printed lines with their font, size and geometry, in reading order."""
    last = doc.page_count - 1 if last is None else last
    out = []
    for pno in range(first, last + 1):
        page = doc[pno]
        lines = []
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                spans = []
                for span in line["spans"]:
                    t = clean(span["text"])
                    spans.append({"font": span["font"], "size": round(span["size"], 1),
                                  "text": t})
                if not spans:
                    continue
                # Some producers emit the space between two words as its own
                # span (rather than as part of a word span). Dropping those
                # spans glues the words together, so keep every span and let
                # the join below collapse the result.
                text = re.sub(r"  +", " ", "".join(s["text"] for s in spans)).strip()
                if not text:
                    continue
                lines.append({
                    "page": pno,
                    "y0": round(line["bbox"][1], 1),
                    "y1": round(line["bbox"][3], 1),
                    "x0": round(line["bbox"][0], 1),
                    "x1": round(line["bbox"][2], 1),
                    "size": max(s["size"] for s in spans),
                    "fonts": {s["font"] for s in spans},
                    "text": text,
                    "runs": [{"italic": "Italic" in s["font"] or "italic" in s["font"],
                              "text": s["text"]} for s in spans],
                })
        lines.sort(key=lambda i: (i["y0"], i["x0"]))
        out.extend(lines)
    return out


# --------------------------------------------------------------------------
# de-hyphenation


class Hyphenator:
    """Decide whether a hyphen at a line end is a break or a real compound."""

    def __init__(self, words=None):
        from spellchecker import SpellChecker

        self.sp = SpellChecker(distance=1)
        self.extra = set()
        for path in ("/usr/share/dict/words",):
            try:
                self.extra.update(w.strip().lower() for w in open(path))
            except OSError:
                pass
        if words:
            self.extra.update(words)

    def known(self, w):
        w = w.lower().strip(".,;:!?\"'()[]")
        return bool(w) and (w in self.sp or w in self.extra)

    def join(self, a, b):
        """Join 'a-' + 'b'; return the text to keep at the join."""
        head = a.rstrip()
        assert head.endswith("-")
        stem = head[:-1]
        m = re.search(r"[\w'\u2019-]+$", stem)
        last = m.group(0) if m else stem
        first = re.match(r"[\w'\u2019-]+", b)
        b_word = first.group(0) if first else b
        # Compound broken at its own hyphen: both halves are words.
        if self.known(last) and self.known(b_word):
            return "-"
        # Plain line-break hyphenation: the closed-up form is a word.
        if self.known(last + b_word):
            return ""
        return ""


# --------------------------------------------------------------------------
# paragraph assembly


def assemble(items, step=13.2, para_gap=1.55, floor=None, hyphenator=None):
    """Group printed lines into paragraphs.

    A paragraph starts where the printed page indents its first line, where
    the vertical gap is wider than one line, or — at a page boundary — where
    the previous page did not fill its text area. Items must carry ``page``,
    ``y0``, ``runs`` and an ``indented`` flag.
    """
    blocks = []
    para = None
    para_page = None
    prev_y = None

    def flush():
        nonlocal para
        if para and "".join(r["text"] for r in para).strip():
            blocks.append({"type": "para", "runs": para})
        para = None

    for it in items:
        fresh = para is None
        if not fresh:
            if it["page"] != para_page:
                page_full = prev_y is not None and floor and prev_y + step > floor
                trailing = para[-1]["text"].rstrip()
                ends_sentence = trailing[-1:] in '.!?"\u201d\u2019'
                # Mid-word or mid-sentence across the page turn: the paragraph
                # runs on even if the printed page stopped short.
                if trailing.endswith("-"):
                    fresh = False
                elif not it.get("indented") and not ends_sentence:
                    fresh = False
                else:
                    fresh = not (page_full and not it.get("indented"))
            elif it.get("indented"):
                fresh = True
            elif prev_y is not None and it["y0"] - prev_y > step * para_gap:
                fresh = True

        if fresh:
            flush()
            para = []
            prev_text = ""
            runs = it["runs"]
        else:
            prev_text = "".join(r["text"] for r in para)
            trailing = para[-1]["text"]
            runs = it["runs"]
            if trailing.rstrip().endswith("-") and hyphenator is not None:
                keep = hyphenator.join(trailing, runs[0]["text"])
                para[-1]["text"] = trailing.rstrip()[:-1] + keep
            elif not prev_text.endswith(" "):
                para[-1]["text"] = para[-1]["text"].rstrip() + " "

        for run in runs:
            if para and para[-1]["italic"] == run["italic"]:
                para[-1]["text"] += run["text"]
            else:
                para.append(dict(run))
        para_page = it["page"]
        prev_y = it["y0"]

    flush()
    return blocks


# --------------------------------------------------------------------------
# LaTeX


def tex_escape(ch):
    return ESCAPES.get(ch, ch)


SMALL_WORDS = {"a", "an", "and", "as", "at", "but", "by", "for", "from", "in",
               "into", "nor", "of", "on", "or", "the", "to", "up", "upon",
               "with", "within", "without"}


def smart_title(text):
    """Title-case a heading that was printed in capitals."""
    if not text.isupper():
        return text

    def capitalise(word):
        parts = re.split(r"([\-'\u2019])", word)
        for i, part in enumerate(parts):
            if part and part not in "-'\u2019" and part.isalpha():
                parts[i] = part[0].upper() + part[1:].lower()
        return "".join(parts)

    out = []
    for i, w in enumerate(text.split()):
        core = w.strip("\u201c\u201d\"'(),;:")
        if not core:
            out.append(w)
            continue
        if i and core.lower() in SMALL_WORDS:
            new = core.lower()
        else:
            new = capitalise(core)
        out.append(w.replace(core, new))
    return " ".join(out)


# First elements that legitimately carry a hyphen in these books; a hyphen
# after one of them is a real compound, not a line-break hyphenation.
KEEP_PREFIXES = {
    "self", "well", "ill", "one", "half", "non", "anti", "co", "semi", "many",
    "single", "fellow", "ninety", "twenty", "thirty", "forty", "fifty", "three",
    "first", "last", "all", "cross", "mid", "multi", "pre", "ape", "mill",
    "steam", "corn", "gambling", "joint", "safety", "time", "hero", "high",
    "hangers", "vouched", "wrong",
}


def fix_word_hyphens(text, hyphenator, log=None):
    """Repair mid-line word breaks left by the OCR ("govern-ment")."""

    def repl(m):
        a, b = m.group(1), m.group(2)
        low_a, low_b = a.lower(), b.lower()
        if low_a in KEEP_PREFIXES:
            return m.group(0)
        if hyphenator.known(a + b) and not hyphenator.known(f"{a}-{b}"):
            if log is not None:
                log.append(m.group(0))
            return a + b
        return m.group(0)

    return re.sub(r"\b([A-Za-z]{2,})-([A-Za-z]{2,})\b", repl, text)


# Function words the OCR runs together ("ofthe", "andthe"); only pairs where
# the split is unambiguous are listed.
GLUED = {
    "ofa": "of a", "tothe": "to the", "inthe": "in the", "andthe": "and the",
    "ofthe": "of the", "tobe": "to be", "isnot": "is not", "itis": "it is",
    "thata": "that a", "forthe": "for the", "onthe": "on the", "atthe": "at the",
    "bythe": "by the", "withthe": "with the", "fromthe": "from the",
    "asthe": "as the", "tothei": "to thei", "andof": "and of", "oftheir": "of their",
    "wasthe": "was the", "hasthe": "has the", "havethe": "have the",
    "whichis": "which is", "thatis": "that is", "theother": "the other",
    "thefirst": "the first", "thesame": "the same", "onlythe": "only the",
    "allthe": "all the", "sucha": "such a", "nota": "not a", "bea": "be a",
}


def fix_glued(text):
    """Undo run-together function words and stray OCR marks."""
    text = re.sub(r"\s*_\s*", " ", text)          # OCR underscore for a space
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"([,.;:!?])([A-Za-z])", r"\1 \2", text)

    def repl(m):
        w = m.group(0)
        return GLUED[w.lower()] if w.lower() in GLUED else w

    return re.sub(r"\b[A-Za-z]{3,12}\b", repl, text)


def render(text, italics, smart_quotes=True):
    out = []
    dq = False

    def emit(piece, italic):
        if not piece:
            return
        if out and out[-1][1] == italic:
            out[-1][0] += piece
        else:
            out.append([piece, italic])

    for i, ch in enumerate(text):
        it = italics[i]
        if ch in "'\u2019\u2018":
            prev = text[i - 1] if i else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if prev.isalnum():
                emit("'", it)
            elif nxt.isalpha() and not smart_quotes:
                emit("`", it)
            elif nxt.isalpha():
                emit("`" if ch == "\u2018" else "'", it)
            else:
                emit("'", it)
        elif ch in '"\u201c\u201d':
            prev = text[i - 1] if i else ""
            if ch == "\u201c" or (ch == '"' and not dq and (not prev.isalnum())):
                emit("``", it)
                dq = True
            elif ch == "\u201d" or ch == '"':
                emit("''", it)
                dq = False
            else:
                emit(ch, it)
        elif ch == "\u2014":
            emit("---", it)
        elif ch == "\u2013":
            emit("--", it)
        elif ch == "\u2026":
            emit(r"\ldots{}", it)
        else:
            emit(tex_escape(ch), it)

    parts = []
    for piece, italic in out:
        if italic:
            m = re.match(r"^(\s*)(.*?)(\s*)$", piece, re.S)
            lead, core, trail = m.groups()
            punct = ""
            while core and core[-1] in ".,;:!?":
                punct = core[-1] + punct
                core = core[:-1]
            if lead:
                parts.append(lead)
            if core:
                parts.append(r"\emph{" + core + "}")
            parts.append(punct + trail)
        else:
            parts.append(piece)
    return "".join(parts)


def para_tex(block):
    text, italics = [], []
    for run in block["runs"]:
        text.extend(run["text"])
        italics.extend([run["italic"]] * len(run["text"]))
    return render("".join(text), italics)


def preamble(title, author, extra=""):
    return r"""\documentclass[11pt,openany]{book}

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{libertine}
\usepackage{microtype}
\usepackage{titlesec}
\usepackage{fancyhdr}
\usepackage{geometry}
\usepackage{textcomp}
\usepackage[hidelinks]{hyperref}

\geometry{paperwidth=6in, paperheight=9in,
  inner=0.78in, outer=0.66in, top=0.78in, bottom=0.86in}

\frenchspacing
\setlength{\parindent}{1.2em}
\setlength{\parskip}{0pt}
\setlength{\emergencystretch}{2em}
\linespread{1.06}

\hypersetup{pdftitle={@@TITLE@@},pdfauthor={@@AUTHOR@@},bookmarksnumbered=false}

\titleformat{\chapter}[display]{\normalfont\sffamily}{}{0pt}
  {\Large\bfseries\raggedright}[\vspace{8pt}{\titlerule[0.5pt]}]
\titlespacing*{\chapter}{0pt}{56pt}{30pt}
\titleformat{\section}[block]{\normalfont\sffamily\bfseries\large}{}{0pt}{}
\titlespacing*{\section}{0pt}{26pt}{12pt}
\titleformat{\tableofcontents}[block]{\normalfont\sffamily\bfseries\Large}{}{0pt}{}
\renewcommand{\contentsname}{Contents}

\newcommand{\bookchapter}[2]{%
  \clearpage\phantomsection
  \chapter*{{\normalfont\sffamily\small\mdseries #1}\\[6pt]#2}%
  \addcontentsline{toc}{chapter}{#1\quad #2}%
  \markboth{#2}{#2}\thispagestyle{plain}}

\newcommand{\booksection}[1]{%
  \phantomsection\section*{#1}%
  \addcontentsline{toc}{section}{#1}\markright{#1}}

\newcommand{\bookbackmatter}[1]{%
  \clearpage\phantomsection\chapter*{#1}%
  \addcontentsline{toc}{chapter}{#1}\markboth{#1}{#1}\thispagestyle{plain}}

\newcommand{\bookchapterplain}[1]{%
  \clearpage\phantomsection
  \chapter*{#1}%
  \addcontentsline{toc}{chapter}{#1}%
  \markboth{#1}{#1}\thispagestyle{plain}}

\newcommand{\bookpart}[1]{%
  \clearpage\phantomsection
  \addcontentsline{toc}{part}{#1}%
  \vspace*{2.2in}
  \begin{center}{\sffamily\bfseries\large #1}\end{center}
  \markboth{#1}{#1}\thispagestyle{plain}\clearpage}

\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.3pt}
\fancyhead[LE]{\sffamily\small\thepage}
\fancyhead[LO]{\sffamily\small\itshape\nouppercase{\rightmark}}
\fancyhead[RE]{\sffamily\small\itshape\nouppercase{\leftmark}}
\fancyhead[RO]{\sffamily\small\thepage}
\fancypagestyle{plain}{\fancyhf{}\renewcommand{\headrulewidth}{0pt}%
  \fancyfoot[C]{\sffamily\small\thepage}}

@@EXTRA@@
\begin{document}
""".replace("@@TITLE@@", title).replace("@@AUTHOR@@", author).replace("@@EXTRA@@", extra)


def title_page(title, author, lines=()):
    body = "".join("\\vspace{0.18in}{\\sffamily\\small %s\\par}\n" % l for l in lines)
    return r"""\begin{titlepage}
\centering
\vspace*{1.7in}
{\sffamily\bfseries\fontsize{34}{40}\selectfont @@TITLE@@\par}
\vspace{0.42in}
{\sffamily\large @@AUTHOR@@\par}
@@LINES@@\vfill
\end{titlepage}
""".replace("@@TITLE@@", title).replace("@@AUTHOR@@", author).replace("@@LINES@@", body)
