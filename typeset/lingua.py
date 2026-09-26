"""Typeset Lingua Latina per se Illustrata (Pars I, Familia Romana).

Usage:  cd typeset && python3 lingua_ocr.py && python3 lingua_merge.py \
            && python3 lingua.py

The source is the scan supplied with the collection.  Unlike the other
books it is set in two columns -- running text inboard, glosses and the
vocabulary in the outer margin -- and much of what it teaches is taught in
italic type, which the scan's own text layer reads badly.  So the book is
built in three passes:

    lingua_ocr.py    re-reads every page with Tesseract's Latin model,
                     body column and margin column clipped apart
    lingua_merge.py  aligns that reading with the scan's own text layer
                     word by word and keeps the better of the two, which
                     preserves the printed macrons and repairs the italics
    lingua.py        rebuilds the book: running text as flowing
                     paragraphs, glosses as LaTeX margin notes in the same
                     place, the vocabulary of each chapter gathered at its
                     end, then the grammar summary and the three pensa

Each printed chapter has the same shape -- text, grammar, exercises -- and
several carry a second reading lesson; the tables below are read off the
printed pages (the running heads carry the chapter numbers, the chapter
openings carry the titles) and are what makes the output paginate like a
book.

The two word lists the merge consults live in build/lingua/: verba.txt is
the ``verba`` project's Latin word list, latin-core.json the thousand
commonest Latin words from the Dickenson College Commentaries core
vocabulary.
"""

import json
import os
import re
import subprocess
import sys

import fitz

import bookkit as bk
import typebooks as tb

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = ROOT                            # the built book sits beside this script
MERGED = os.path.join(ROOT, "build/lingua/merged.json")
LINES = os.path.join(ROOT, "build/lingua/lines.json")

SLUG = "Lingua_Latina"

# ---------------------------------------------------------------- structure

# (first printed page of the chapter, "Cap." label, title)
CHAPTERS = [
    (6, "Cap. I", "Imperium Rōmānum"),
    (12, "Cap. II", "Familia Rōmāna"),
    (18, "Cap. III", "Puer improbus"),
    (25, "Cap. IV", "Dominus et servī"),
    (31, "Cap. V", "Vīlla et hortus"),
    (40, "Cap. VI", "Via Latīna"),
    (47, "Cap. VII", "Puella et rosa"),
    (53, "Cap. VIII", "Taberna Rōmāna"),
    (62, "Cap. IX", "Pāstor et ovēs"),
    (68, "Cap. X", "Bēstiae et hominēs"),
    (76, "Cap. XI", "Corpus hūmānum"),
    (84, "Cap. XII", "Mīles Rōmānus"),
    (94, "Cap. XIII", "Annus et mēnsēs"),
    (102, "Cap. XIV", "Novus diēs"),
    (109, "Cap. XV", "Magister et discipulī"),
    (117, "Cap. XVI", "Tempestās"),
    (125, "Cap. XVII", "Numerī difficilēs"),
    (134, "Cap. XVIII", "Litterae Latīnae"),
    (144, "Cap. XIX", "Marītus et uxor"),
    (153, "Cap. XX", "Parentēs"),
    (162, "Cap. XXI", "Pugna discipulōrum"),
    (171, "Cap. XXII", "Cavē canem"),
    (178, "Cap. XXIII", "Epistula magistrī"),
    (186, "Cap. XXIV", "Puer aegrōtus"),
    (194, "Cap. XXV", "Thēseus et Mīnōtaurus"),
    (202, "Cap. XXVI", "Daedalus et Īcarus"),
    (210, "Cap. XXVII", "Rēs rūsticae"),
    (221, "Cap. XXVIII", "Perīcula maris"),
    (231, "Cap. XXIX", "Nāvigāre necesse est"),
    (241, "Cap. XXX", "Convīvium"),
    (250, "Cap. XXXI", "Inter pōcula"),
    (260, "Cap. XXXII", "Classis Rōmāna"),
    (272, "Cap. XXXIII", "Exercitus Rōmānus"),
    (283, "Cap. XXXIV", "Dē arte poēticā"),
    (295, "Cap. XXXV", "Ars grammatica"),
]

FIRST_PAGE = CHAPTERS[0][0]
LAST_PAGE = 305                        # Cap. XXXV's last pensum page

# printed headings, as the body sets them
SECTION_RE = [
    (re.compile(r"^SCAENA\s+(PRIMA|SECVNDA|TERTIA)\b"), "Scaena {}"),
    (re.compile(r"^.{0,12}LITTERAE\s+ET\s+NVMER[A-Z]*$"), None),
    (re.compile(r"^.{0,12}LIBERT[VW]S\s+LATINVS$"), None),
    (re.compile(r"^LATINVS$"), None),
    (re.compile(r"^GRAMMATICA\s+LATINA\b"), "Grammatica Latīna"),
    (re.compile(r"^PENSVM\s*([ABC])\b"), "Pēnsum {}"),
    (re.compile(r"^Dē\s+[A-Za-zāēīōū\s]{2,24}$"), None),
]

SCAENA_FORM = {"PRIMA": "prīma", "SECVNDA": "secunda", "TERTIA": "tertia"}

# lines the scan adds that carry no reading: folios, stray marks
JUNK = re.compile(r"^[^A-Za-zÀ-ÿ]*$")

VOCAB_HEAD = re.compile(r"^Voc[aā]bul(?:a|ae|arw?a)\w*\s*:?\s*", re.I)

LIG = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
       "\ufb04": "ffl", "\u017f": "s"}


def polish(text):
    """Tidy one line before it is escaped for LaTeX."""
    for lig, plain in LIG.items():
        text = text.replace(lig, plain)
    text = text.replace("\u2010", "-").replace("\u2011", "-")
    text = text.replace("\u00a0", " ")
    # a soft hyphen marks a word broken at the line's end: keep it as a
    # hyphen so that the paragraph builder can close the word up again
    text = re.sub(r"\u00ad\s*$", "-", text).replace("\u00ad", "")
    text = text.translate(ACCENTS)
    text = text.replace("\\", "").replace("^", "").replace("|", "")
    # the printed "different from" mark, however the OCR mangled it
    text = ARROW.sub(" @@MARK@@ ", text)
    # the book opens quotations with a low double quote, which the scan
    # often reads as an asterisk ("*St! Tacē!" for „St! Tacē!")
    text = re.sub(r"(^|\s)\*(?=[A-Za-zĀĒĪŌŪ])", r"\1``", text)
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"\s*//+\s*", " ", text)      # OCR marks, never the text
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"^(?:[/|*•§¶]\s+)(?=[A-ZÀ-Þ])", "", text)
    # the book's italic u, read as w by the scan's own text layer
    text = re.sub(r"\b[A-Za-zāēīōūÀ-ÿ]*[wW][A-Za-zāēīōūÀ-ÿ]*\b", _unw, text)
    return "".join(c for c in text if c in SAFE)


ARROW = re.compile(r"[«€£►■♦<>{}]+[-–—*^~«€£►■♦<>{}]*")

SAFE = set(" \t\n.,;:!?'\"()[]-–—=+*/$%&#_@\u00a7")
SAFE |= set("0123456789")
SAFE |= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
SAFE |= set("āēīōūȳĀĒĪŌŪ")
SAFE |= set("àáâäèéêëìíîïòóôöùúûüýÀÁÂÄÈÉÊËÌÍÎÏÒÓÔÖÙÚÛÜÝ")
SAFE |= set("œŒæÆßøØ")


# The printed book marks long vowels with macrons only; the OCR of the
# italic type renders them as acutes, graves or diaereses instead.
ACCENTS = str.maketrans("áàâäéèêëíìîïóòôöúùûüý",
                        "āāāāēēēēīīīīōōōōūūūūȳ")


def _unw(m):
    w = m.group(0)
    return w.replace("W", "VV").replace("w", "u") if w.isupper() else \
        w.replace("w", "u").replace("W", "V")


def page_left(items):
    """Left edge of the body column on one page."""
    xs = [round(it["x0"]) for it in items if it["size"] >= 9.5 and it["x0"] < 300]
    return max(set(xs), key=xs.count) if xs else 0


def load():
    merged = json.load(open(MERGED, encoding="utf-8"))
    lines = json.load(open(LINES, encoding="utf-8"))
    return merged, lines


def text_x0(orig, text):
    """Left edge of the text proper, with a leading line number set aside."""
    spans = [s for s in orig.get("spans", []) if s["text"].strip()]
    if spans and spans[0]["text"].strip().isdigit() and len(spans) > 1:
        return spans[1]["x0"]
    return orig["x0"]


def body_lines(pno, lines, merged, chapter_titles, hyph):
    """Body lines of one page, cleaned, as assembly items."""
    title = chapter_titles.get(pno)
    cand, texts, heads = [], {}, {}
    for l in merged[str(pno)]["body"]:
        text = polish(l["text"])
        if not text or JUNK.match(text) or text.isdigit():
            continue
        heading = heading_of(text) is not None
        if not heading and not readable(text):
            continue
        if l["size"] > 16:
            continue                        # display lettering of the pictures
        cand.append(l)
        texts[id(l)] = text
        heads[id(l)] = heading
    left = page_left(cand)
    out = []
    prev = None
    for l in cand:
        text = texts[id(l)]
        # A line set well inboard of the body's left edge is either the
        # second half of a printed line the OCR split in two, or a label
        # lettered beside one of the pictures.
        continued = (prev is not None and 0 <= l["x0"] - prev["x1"] <= 25)
        if l["x0"] - left > 30 and not continued and looks_like_a_label(text) \
                and not heads[id(l)]:
            continue
        orig = find_orig(lines, pno, l)
        numbered = (l["x0"] < 62 and pno % 2 == 0) or (
            orig is not None and orig["spans"]
            and orig["spans"][0]["text"].strip().isdigit())
        if numbered:
            text = re.sub(r"^\d{1,4}\s+(?=\S)", "", text)
        if title and _same(strip_macrons(text), title):
            continue                        # the chapter's printed title
        x0 = text_x0(orig, text) if orig is not None else l["x0"]
        out.append({"page": pno, "y0": l["y0"], "x0": x0, "size": l["size"],
                    "text": text, "runs": [{"italic": False, "text": text}]})
        prev = l
    return out


def looks_like_a_label(text):
    """Short lettering beside the pictures, as against a stray half line."""
    words = text.split()
    letters = sum(c.isalpha() for c in text)
    if len(words) < 4 or letters < 0.75 * len(text):
        return True
    return text.isupper() and len(words) < 7


def strip_macrons(text):
    return "".join(_MACRON.get(c, c) for c in text)


_MACRON = dict(zip("āēīōūȳ", "aeiouy"))


def _same(line, title):
    """Is the printed line the chapter's title? (The OCR lets itself slip.)"""
    a = re.sub(r"[^a-z]", "", strip_macrons(line).lower())
    b = re.sub(r"[^a-z]", "", strip_macrons(title).lower())
    if not a or not b:
        return False
    if a.startswith(b) or b.startswith(a):
        return abs(len(a) - len(b)) <= 8
    return False


def find_orig(lines, pno, l):
    for o in lines[str(pno)]:
        if abs(o["y0"] - l["y0"]) < 0.5 and abs(o["x0"] - l["x0"]) < 1.0:
            return o
    return None


def margin_blocks(pno, merged, hyph):
    """The marginal notes of one page, grouped into blocks."""
    rows = []
    for l in merged[str(pno)]["margin"]:
        text = polish(l["text"])
        if not text or JUNK.match(text) or not readable_note(text):
            continue
        if text.isdigit():
            continue
        rows.append({"y0": l["y0"], "x0": l["x0"], "text": text})
    rows.sort(key=lambda r: r["y0"])
    blocks, cur = [], []
    for r in rows:
        if cur and r["y0"] - cur[-1]["y0"] > 24:
            blocks.append(cur)
            cur = []
        cur.append(r)
    if cur:
        blocks.append(cur)
    return blocks


def readable(text):
    """False for the lettering of the pictures, which the OCR mangles."""
    letters = sum(c.isalpha() for c in text)
    if not text or letters < 0.5 * len(text):
        return False
    return not re.search(r"[-–—=*_./\\]{3,}", text)


def readable_note(text):
    """The margin is smaller type: hold it to a stricter test."""
    letters = sum(c.isalpha() for c in text)
    if not text or letters < 0.62 * len(text):
        return False
    words = [w for w in text.split() if sum(c.isalpha() for c in w) >= 3]
    return len(words) >= 2 or len(text.split()) == 1


def is_vocab_block(block):
    """True for the vocabulary lists printed down the outer margin."""
    if VOCAB_HEAD.match(block[0]["text"]):
        return True
    if len(block) < 3:
        return False
    single = sum(1 for r in block if len(r["text"].split()) == 1
                 and not re.search(r"[.;:!?]", r["text"]))
    return single / len(block) >= 0.7


def join_lines(lines):
    """Join printed lines, closing up any word broken at the line's end."""
    out = ""
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if out.endswith("-"):
            out = out[:-1] + line
        else:
            out = (out + " " + line).strip()
    return out


def vocab_entries(block):
    out = []
    for i, r in enumerate(block):
        text = polish(r["text"])
        if i == 0:
            text = VOCAB_HEAD.sub("", text)
        for piece in re.split(r"\s*[,;]\s*", text):
            piece = piece.strip(" .:")
            if piece:
                out.append(piece)
    return out


# ------------------------------------------------------------------ LaTeX

PREAMBLE = r"""\documentclass[11pt,openany]{book}

\usepackage[T1]{fontenc}
\usepackage[utf8]{inputenc}
\usepackage{libertine}
\usepackage{microtype}
\usepackage{titlesec}
\usepackage{fancyhdr}
\usepackage{geometry}
\usepackage{textcomp}
\usepackage{multicol}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}

\geometry{paperwidth=6.7in, paperheight=9.6in,
  inner=0.72in, outer=2.05in, top=0.82in, bottom=0.9in,
  marginparwidth=1.62in, marginparsep=0.2in}

\frenchspacing
\setlength{\parindent}{1.2em}
\setlength{\parskip}{0pt}
\setlength{\emergencystretch}{2em}
\setlength{\marginparpush}{2pt}
\linespread{1.05}

\hypersetup{pdftitle={@@TITLE@@},pdfauthor={@@AUTHOR@@},bookmarksnumbered=false}

\titleformat{\chapter}[display]{\normalfont\sffamily}{}{0pt}
  {\Large\bfseries\raggedright}[\vspace{8pt}{\titlerule[0.5pt]}]
\titlespacing*{\chapter}{0pt}{56pt}{30pt}
\titleformat{\section}[block]{\normalfont\sffamily\bfseries\large}{}{0pt}{}
\titlespacing*{\section}{0pt}{22pt}{10pt}
\titleformat{\tableofcontents}[block]{\normalfont\sffamily\bfseries\Large}{}{0pt}{}
\renewcommand{\contentsname}{Index Capitulōrum}

\newcommand{\bookchapter}[2]{%
  \clearpage\phantomsection
  \chapter*{{\normalfont\sffamily\small\mdseries #1}\\[6pt]#2}%
  \addcontentsline{toc}{chapter}{#1\quad #2}%
  \markboth{#1. #2}{#1. #2}\thispagestyle{plain}}

\newcommand{\booksection}[1]{%
  \phantomsection\section*{#1}%
  \addcontentsline{toc}{section}{#1}\markright{#1}}

\newcommand{\bookbackmatter}[1]{%
  \clearpage\phantomsection\chapter*{#1}%
  \addcontentsline{toc}{chapter}{#1}\markboth{#1}{#1}\thispagestyle{plain}}

\pagestyle{fancy}
\fancyhf{}
\renewcommand{\headrulewidth}{0.3pt}
\fancyhead[LE]{\sffamily\small\thepage}
\fancyhead[LO]{\sffamily\small\itshape\nouppercase{\rightmark}}
\fancyhead[RE]{\sffamily\small\itshape\nouppercase{\leftmark}}
\fancyhead[RO]{\sffamily\small\thepage}
\fancypagestyle{plain}{\fancyhf{}\renewcommand{\headrulewidth}{0pt}%
  \fancyfoot[C]{\sffamily\small\thepage}}

\newcommand{\navnote}[1]{\marginpar{\raggedright\scriptsize\linespread{1.0}%
  \selectfont #1}}
\newcommand{\contr}{$\leftrightarrow$}

\begin{document}
"""

TITLE_PAGE = r"""\begin{titlepage}
\centering
\vspace*{1.5in}
{\sffamily\bfseries\fontsize{30}{36}\selectfont Lingua Latīna\par}
\vspace{0.16in}
{\sffamily\large per sē illūstrāta\par}
\vspace{0.5in}
{\sffamily\bfseries\large Pars I\par}
\vspace{0.14in}
{\sffamily\Large Familia Rōmāna\par}
\vspace{0.6in}
{\sffamily\large Hans H. Ørberg\par}
\vfill
{\sffamily\small Domus Latīna\par}
\end{titlepage}
"""

SOURCE_NOTE = (
    "The text is Hans H. \\O{}rberg's \\emph{Lingua Lat\\={\\i}na per s\\=e "
    "ill\\=ustr\\=ata}, Pars I: \\emph{Familia R\\=om\\=ana} (Domus "
    "Lat\\=ina, 1991 and later printings), from the scan supplied with this "
    "collection. The book is in copyright: this edition is a private "
    "re-typesetting for the owner's own reading, not for distribution.\n\n"
    "The scan carries the text layer made when it was digitised. That layer "
    "keeps the macrons of the printed book, which matter in a Latin reader, "
    "but it misreads badly wherever the book sets Latin in italic type --- "
    "in the grammar summaries, in the exercise paradigms and in the "
    "marginal glosses. The pages were therefore read a second time with "
    "Tesseract's Latin model and the two readings merged, word by word, "
    "checking each doubtful word against the book's own vocabulary. "
    "Reading errors remain, especially in the paradigms and in the "
    "outer margin, where the print is small and the OCR had least to work "
    "with; nothing has been invented or silently rewritten.\n\n"
    "The layout follows the printed book: the running text with the "
    "Latin glosses beside it in the outer margin, the vocabulary of each "
    "chapter gathered at its end, then the grammar summary and the three "
    "pensa. The pictures that carry much of the book's teaching are not "
    "reproduced here --- a re-typeset edition cannot redraw them --- and "
    "neither are the printed line numbers, which refer to lines of the "
    "printed page. The declension tables (Tabula "
    "D\\=ecl\\=in\\=ati\\=onum), the Roman calendar, the word list and the "
    "grammatical index of the printed book, and the list of irregular "
    "forms (Formae m\\=ut\\=atae), are not reproduced: they are set in "
    "columns of very small type, and the scan's OCR cannot be trusted with "
    "them. The abbreviations the book uses are listed under \\emph{Notae} "
    "at the end."
)

# The abbreviations, as the printed page lists them (printed page 328)
NOTAE = r"""\bookbackmatter{Notae}

\begin{description}
\item[=] idem atque (pōnitur inter vocābula quae eandem ferē rem significant)
\item[\contr{}] contrārium (pōnitur inter vocābula quae rēs contrāriās
  significant)
\item[$<$] factum ex (pōnitur inter vocābula quōrum alterum ex alterō factum
  est)
\item[$|$] haec nota pōnitur ante litterās quae in dēclīnātiōne adduntur
\end{description}

\begin{multicols}{2}\small\raggedright
\begin{description}[leftmargin=1.6em,itemsep=1pt,parsep=0pt]
\item[abl] ablātīvus
\item[acc] accūsātīvus
\item[āct] āctīvum
\item[a.\,d.] ante diem
\item[adi] adiectīvum
\item[adv] adverbium
\item[cap.] capitulum
\item[cet.] cēterī -ae -a
\item[comp] comparātīvus
\item[con] coniūnctīvus
\item[dat] datīvus
\item[dēcl] dēclīnātiō
\item[dēp] dēpōnēns
\item[f] feminīnum
\item[fut] futūrum
\item[imper] imperātīvus
\item[impf] imperfectum
\item[indēcl] indēclīnābile
\item[īnf] īnfīnītīvus
\item[kal.] kalendae
\item[loc] locātīvus
\item[m, masc.] masculīnum
\item[n, neutr.] neutrum
\item[nōm] nōminātīvus
\item[nōn.] nōnae
\item[pāg.] pāgina
\item[part] participium
\item[pass] passīvum
\item[perf] perfectum
\item[pers] persōna
\item[pl, plūr.] plūrālis
\item[praes] praesēns
\item[prōn] prōnōmen
\item[prp] praepositiō
\item[s.\,d.] salūtem dīcit
\item[sg, sing.] singulāris
\item[sup] superlātīvus
\item[voc] vocātīvus
\end{description}
\end{multicols}
"""


def tex(text, italic=False):
    """Escape a Latin line for LaTeX, keeping the macrons as they are."""
    runs = [{"italic": italic, "text": text}]
    return bk.para_tex({"runs": runs})


def para_tex(block):
    """Like bookkit's, but runs marked ``raw`` are LaTeX already."""
    out = []
    for run in block["runs"]:
        if run.get("raw"):
            out.append(run["text"])
        else:
            out.append(bk.render(run["text"],
                                 [run["italic"]] * len(run["text"])))
    return "".join(out).replace("@@MARK@@", r"\contr{}")


def assemble(items, step, floor, hyph):
    """bookkit.assemble, with the marginal notes carried into the stream."""
    blocks, para, para_page, prev_y = [], None, None, None

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
                if trailing.endswith("-"):
                    fresh = False
                elif not it.get("indented") and not ends_sentence:
                    fresh = False
                else:
                    fresh = not (page_full and not it.get("indented"))
            elif it.get("indented"):
                fresh = True
            elif prev_y is not None and it["y0"] - prev_y > step * 1.55:
                fresh = True

        if fresh:
            flush()
            para = []
            runs = [dict(r) for r in it["runs"]]
        else:
            trailing = para[-1]["text"]
            runs = [dict(r) for r in it["runs"]]
            if trailing.rstrip().endswith("-") and hyph is not None:
                keep = hyph.join(trailing, runs[0]["text"])
                para[-1]["text"] = trailing.rstrip()[:-1] + keep
            elif not "".join(r["text"] for r in para).endswith(" "):
                para[-1]["text"] = para[-1]["text"].rstrip() + " "

        for note in it.get("notes", ()):
            if para is None:
                para = []
            para.append({"italic": False, "raw": True,
                         "text": r"\navnote{%s}" % bk.render(
                             note, [False] * len(note)).replace(
                                 "@@MARK@@", r"\contr{}")})
        for run in runs:
            if para and not para[-1].get("raw") \
                    and para[-1]["italic"] == run["italic"]:
                para[-1]["text"] += run["text"]
            else:
                para.append(dict(run))
        para_page = it["page"]
        prev_y = it["y0"]

    flush()
    return blocks


def chapter_of(pno):
    return max([c for c in CHAPTERS if c[0] <= pno], default=CHAPTERS[0])


def heading_of(text):
    """The printed heading a body line carries, or None."""
    for rx, form in SECTION_RE:
        m = rx.match(text)
        if not m:
            continue
        if form:
            group = m.group(1) if m.groups() else ""
            if group in SCAENA_FORM:
                return form.format(SCAENA_FORM[group])
            return form.format(group)
        if "LITTERAE" in text:
            return "Litterae et numerī"
        if "LIBERT" in text or text.strip() == "LATINVS":
            return "Lībertus Latīnus"
        return text if text[0].isupper() and not text.isupper() else \
            bk.smart_title(text)
    return None


def collect(merged, lines, hyph):
    """Body items (with their margin notes) and the vocabulary, by chapter."""
    chapter_titles = {p: bk.smart_title(t) for p, _, t in CHAPTERS}
    pages, vocab, heads = {}, {}, []
    for pno in range(FIRST_PAGE, LAST_PAGE + 1):
        items = []
        for it in body_lines(pno, lines, merged, chapter_titles, hyph):
            title = heading_of(it["text"])
            if title:
                heads.append((pno, it["y0"], title))
                continue
            items.append(it)
        anchors = []
        for block in margin_blocks(pno, merged, hyph):
            if is_vocab_block(block):
                vocab.setdefault(chapter_of(pno)[1], []).extend(
                    vocab_entries(block))
            else:
                anchors.append((block[0]["y0"],
                                join_lines(polish(r["text"]) for r in block)))
        for i, it in enumerate(items):
            while anchors and anchors[0][0] <= it["y0"]:
                text = anchors.pop(0)[1]
                # a word broken at the line's end must be closed up before
                # the note goes in, or the note lands inside the word
                host = it
                if i and it["text"][:1].islower():
                    prev = items[i - 1]
                    if prev["text"].rstrip().endswith("-"):
                        host = prev
                host.setdefault("notes", []).append(text)
        pages[pno] = items
    return pages, vocab, heads


def restore_macrons(pages, vocab, lines):
    """Give back the macrons the OCR lost, from the book's own spellings.

    The scan's own text layer keeps the macrons (``Rōma``); the Latin OCR
    of the italics does not.  So the spellings are tallied from the text
    layer, and a word that the merge took from the OCR is given the macrons
    the layer writes elsewhere -- but only where one spelling clearly
    wins, since the book writes ``Italiā`` in the ablative and ``Italia``
    in the nominative and both are common.
    """
    forms = {}
    for page in lines.values():
        for l in page:
            if 46 <= l["y0"] <= 568:
                _count_forms(forms, polish(l["text"]))

    canon = {}
    for key, variants in forms.items():
        if len(key) < 3:
            continue                    # ā, ē, i, o: too short to be safe
        best = max(variants, key=lambda w: (len(_long(w)), variants[w],
                                            _macrons(w)))
        longs = _long(best)
        if not longs:
            continue
        # A spelling with fewer long vowels is taken for a macron the OCR
        # lost only where it is rare: the book writes "Italia" in the
        # nominative beside "Italiā" in the ablative, and it writes "Iūlius"
        # nearly every time, so only the second wants repairing.
        lost = sum(n for w, n in variants.items()
                   if _long(w) < longs and _long(w) <= longs)
        if lost <= 0.2 * variants[best]:
            canon[key] = best

    def fix(chunk):
        return re.sub(r"[A-Za-zÀ-ÿ\u0100-\u017f]+",
                      lambda m: _fix_word(m.group(0), canon), chunk)

    for items in pages.values():
        for it in items:
            it["text"] = fix(it["text"])
            it["runs"] = [{"italic": False, "text": it["text"]}]
            if it.get("notes"):
                it["notes"] = [fix(n) for n in it["notes"]]
    for key in vocab:
        vocab[key] = [fix(w) for w in vocab[key]]


def _macrons(word):
    return sum(1 for c in word if c in "āēīōūȳ")


def _long(word):
    return frozenset(i for i, c in enumerate(word.lower()) if c in "āēīōūȳ")


def _fix_word(seen, canon):
    target = canon.get(strip_macrons(seen).lower())
    if not target or not _long(seen) <= _long(target):
        return seen
    return _match_case(target, seen)


def _match_case(canon, seen):
    """Take the macrons of ``canon`` but the capitalisation of ``seen``."""
    if strip_macrons(canon).lower() != strip_macrons(seen).lower():
        return seen
    return "".join(c.upper() if seen[i].isupper() else c
                   for i, c in enumerate(canon))


def _count_forms(forms, chunk):
    """Tally how often each spelling (macrons and all) of a word occurs."""
    for word in re.findall(r"[A-Za-zÀ-ÿ\u0100-\u017f]+", chunk):
        key = strip_macrons(word).lower()
        seen = word.lower()
        forms.setdefault(key, {})
        forms[key][seen] = forms[key].get(seen, 0) + 1


def mark_indents(pages):
    """Paragraph starts: the printed page indents their first line."""
    for pno, items in pages.items():
        xs = [it["x0"] for it in items if it["size"] >= 9.5]
        if not xs:
            continue
        left = max(set(xs), key=xs.count)
        for it in items:
            it["indented"] = it["size"] >= 9.5 and it["x0"] - left >= 8.0


def step_of(pages):
    gaps = []
    for items in pages.values():
        ys = sorted(it["y0"] for it in items)
        gaps += [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 40]
    gaps.sort()
    return gaps[len(gaps) // 2] if gaps else 16.0


def segments(pages, heads):
    """Cut the page stream into chapters and printed sections."""
    by_page = {}
    for pno, y, title in heads:
        by_page.setdefault(pno, []).append((y, title))
    out, cur = [], None
    for page, label, title in CHAPTERS:
        if cur:
            out.append(cur)
        cur = {"kind": "chapter", "label": label, "title": title, "items": []}
        nxt = [c[0] for c in CHAPTERS if c[0] > page]
        stop = nxt[0] if nxt else LAST_PAGE + 1
        for pno in range(page, stop):
            pending = sorted(by_page.get(pno, []))
            for it in pages.get(pno, []):
                while pending and pending[0][0] <= it["y0"]:
                    out.append(cur)
                    cur = {"kind": "section", "title": pending.pop(0)[1],
                           "items": []}
                cur["items"].append(it)
            for y, title in pending:            # a heading with no text after
                out.append(cur)
                cur = {"kind": "section", "title": title, "items": []}
    if cur:
        out.append(cur)
    # a printed heading is kept even where no running text follows it on the
    # page (the pensa often end a section at the foot of a page)
    return [s for s in out if s["items"] or s["kind"] == "chapter"
            or s.get("title")]


def build(pages, vocab, heads, hyph, step, floor):
    out, prev = [], None
    for seg in segments(pages, heads):
        if seg["kind"] == "chapter" and prev is not None:
            words = vocab.get(prev, [])
            if words:
                out.append({"type": "vocab", "words": words})
        if seg.get("label"):
            prev = seg["label"]
        head = {k: v for k, v in seg.items() if k != "items"}
        head["type"] = head.pop("kind")
        out.append(head)
        out.extend(assemble(seg["items"], step, floor, hyph))
    words = vocab.get(prev, [])
    if words:
        out.append({"type": "vocab", "words": words})
    return out


def emit(blocks):
    out = [PREAMBLE.replace("@@TITLE@@", "Lingua Latina per se Illustrata")
                    .replace("@@AUTHOR@@", "Hans H. Ørberg"),
           TITLE_PAGE,
           r"""\pagenumbering{roman}
\pagestyle{fancy}
\phantomsection
\chapter*{Dē hāc editīone}
\addcontentsline{toc}{chapter}{Dē hāc editīone}
\markboth{Dē hāc editīone}{Dē hāc editīone}

%s

\tableofcontents
\clearpage
\pagenumbering{arabic}
\pagestyle{fancy}
""" % SOURCE_NOTE]
    for b in blocks:
        if b["type"] == "chapter":
            out.append("\\bookchapter{%s}{%s}\n" % (b["label"], b["title"]))
        elif b["type"] == "section":
            out.append("\\booksection{%s}\n" % b["title"])
        elif b["type"] == "para":
            out.append(para_tex(b) + "\n")
        elif b["type"] == "vocab":
            out.append("\\booksection{Vocābula nova}\n\\noindent\\small %s\n"
                       % _vocab_tex(b["words"]))
    out.append(NOTAE)
    out.append("\n\\end{document}\n")
    return "\n".join(out)


def _vocab_tex(words):
    seen, parts = set(), []
    for w in words:
        w = polish(w)
        key = w.lower()
        if key in seen:
            continue
        seen.add(key)
        parts.append(bk.render(w, [False] * len(w)))
    return (", ".join(parts) + ".").replace("@@MARK@@", r"\contr{}")


HYPH = None


def verify(pages, pdfpath, show=10):
    """Every printed page of the body must have reached the built book.

    Only the words of the running text are read back: the marginal notes
    are set outside the text block, and reading order there crosses the
    text at every note.
    """
    doc = fitz.open(pdfpath)
    got, pending = [], None
    for page in doc:
        h = page.rect.height
        lines = []
        for blk in page.get_text("dict")["blocks"]:
            if blk["type"] != 0:
                continue
            for line in blk["lines"]:
                if line["bbox"][1] < h * 0.05 or line["bbox"][3] > h * 0.95:
                    continue
                if line["bbox"][0] > 340 or line["bbox"][2] < 145:
                    continue            # a marginal note: outside the text
                text = "".join(s["text"] for s in line["spans"]).strip()
                if text:
                    # pdflatex writes a macron as a separate mark before the
                    # vowel (and a long i as a dotless i), so the extracted
                    # text reads "R¯oma" and "N¯ılus"
                    lines.append((line["bbox"][1],
                                  text.replace("\u00af", "")
                                      .replace("\u0131", "i")))
        lines.sort()
        for _, text in lines:
            if pending is not None:
                text, pending = pending + text, None
            if text.endswith(("-", "\u2010", "\u2011")) and not text.endswith("--"):
                pending = text[:-1]
                continue
            got.extend(tb.words(text))
        if pending is not None:
            got.extend(tb.words(pending))
            pending = None
    total = len(got)
    got = set(strip_macrons(w) for w in got)
    src, missing = [], []
    for pno in sorted(pages):
        items = pages[pno]
        if not items:
            continue
        words = [strip_macrons(w)
                 for w in tb.words(" ".join(it["text"] for it in items))]
        src.extend(words)
        probe = [w for w in words[:14] if len(w) > 2][:8]
        if len(probe) < 5:
            continue
        # a page whose first words never reach the book has been dropped;
        # words are compared one by one because the paragraph builder joins
        # printed lines, so a sequence cannot be expected to survive intact
        if sum(1 for w in probe if w in got) < 0.75 * len(probe):
            missing.append((pno, " ".join(probe[:6])))
    print("   verify: source=%d pdf=%d  pages missing=%d"
          % (len(src), total, len(missing)))
    for pno, probe in missing[:show]:
        print("      [p%d] %s" % (pno, probe))
    return len(missing)


def main():
    global HYPH
    merged, lines = load()
    HYPH = bk.Hyphenator()
    pages, vocab, heads = collect(merged, lines, HYPH)
    restore_macrons(pages, vocab, lines)
    mark_indents(pages)
    step = step_of(pages)
    floor = max((it["y0"] for items in pages.values() for it in items),
                default=560.0)
    blocks = build(pages, vocab, heads, HYPH, step, floor)
    tex_out = emit(blocks)

    workdir = os.path.join(OUT, SLUG)
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, SLUG + ".tex")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tex_out)
    json.dump(blocks, open(os.path.join(workdir, "blocks.json"), "w"),
              ensure_ascii=False)
    print("blocks: %d  paras: %d  sections: %d  chapters: %d  step: %.1f"
          % (len(blocks), sum(1 for b in blocks if b["type"] == "para"),
             sum(1 for b in blocks if b["type"] == "section"),
             sum(1 for b in blocks if b["type"] == "chapter"), step))
    print("   ->", path)
    if "--no-compile" in sys.argv:
        return
    run = ["pdflatex", "-interaction=nonstopmode", SLUG + ".tex"]
    for _ in range(2):
        subprocess.run(run, cwd=workdir, capture_output=True)
    pdf = os.path.join(workdir, SLUG + ".pdf")
    print("   ->", pdf)
    verify(pages, pdf)


if __name__ == "__main__":
    main()
