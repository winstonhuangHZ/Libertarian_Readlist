"""Typeset a scanned book from its OCR cache.

Usage:  python3 typeset/ocr_book.py <key>

Chapters are found from the printed running heads: an essay begins where the
running head changes, which is how these scans mark article boundaries.
"""

import json
import os
import re
import sys
from collections import Counter

import bookkit as bk

OUT = "typeset"

FOLIO = re.compile(r"^[^A-Za-z]*[ivxlcdmIVXLCDM0-9]{1,6}[^A-Za-z]*$")

# Printed chapter titles, as they sit on a chapter's opening page.
TITLE_RE = [
    re.compile(r"^\[?\s*\d{0,4}\s*\]?\s*(?:CHAP|Chap)[A-Za-z]*\.?\s*[IVXL]*\.?\s*(\[?\d+\]?)?$"),
    re.compile(r"^(?:Of|OF)\s+[A-Za-z][A-Za-z ,'\-]{0,60}\.?,?$"),
    re.compile(r"^(?:and|AND|of|to)\s+[A-Za-z ,'\-]{1,45}\.$"),
    re.compile(r"^Part\s?[IVX]+\.?$"),
]


def title_zone(lines, min_words=9):
    """Cut-off y for the printed title that opens a chapter page.

    The title sits above the first run of real body text; a line counts as
    body as soon as it carries a full clause.
    """
    for line in sorted(lines, key=lambda l: l["y0"]):
        if len(line["text"].split()) >= min_words:
            return line["y0"]
    return None


def is_title_line(text):
    if any(rx.match(text) for rx in TITLE_RE):
        return True
    letters = [c for c in text if c.isalpha()]
    return bool(letters) and len(text) < 46 and len(text.split()) < 7 and \
        sum(c.isupper() for c in letters) / len(letters) > 0.75

# Leviathan's chapter titles, in order, as printed in the 1651 edition.
HOBBES_CHAPTERS = [
    "Of Sense", "Of Imagination", "Of the Consequence or Train of Imaginations",
    "Of Speech", "Of Reason and Science",
    "Of the Interior Beginnings of Voluntary Motions",
    "Of the Ends or Resolutions of Discourse",
    "Of the Virtues Commonly Called Intellectual",
    "Of the Several Subjects of Knowledge",
    "Of Power, Worth, Dignity, Honour, and Worthiness",
    "Of the Difference of Manners", "Of Religion",
    "Of the Natural Condition of Mankind",
    "Of the First and Second Natural Laws, and of Contracts",
    "Of Other Laws of Nature", "Of Persons, Authors, and Things Personated",
    "Of the Causes, Generation, and Definition of a Common-wealth",
    "Of the Rights of Sovereigns by Institution",
    "Of the Several Kinds of Common-wealth by Institution",
    "Of Dominion Paternal and Despotical", "Of the Liberty of Subjects",
    "Of Systems Subject, Political, and Private",
    "Of the Public Ministers of Sovereign Power",
    "Of the Nutrition and Procreation of a Common-wealth", "Of Counsel",
    "Of Civil Laws", "Of Crimes, Excuses, and Extenuations",
    "Of Punishments and Rewards",
    "Of Those Things that Weaken a Common-wealth",
    "Of the Office of the Sovereign Representative",
    "Of the Kingdom of God by Nature",
    "Of the Principles of Christian Politics",
    "Of the Number, Antiquity, Scope, and Interpreters of Holy Scripture",
    "Of the Signification of Spirit, Angel, and Inspiration",
    "Of the Signification in Scripture of the Kingdom of God",
    "Of the Word of God, and of Prophets", "Of Miracles and Their Use",
    "Of the Signification in Scripture of Eternal Life, Hell, and Salvation",
    "Of the Signification in Scripture of the Word Church",
    "Of the Rights of the Kingdom of God",
    "Of the Office of Our Blessed Saviour", "Of Power Ecclesiastical",
    "Of What Is Necessary for a Man's Reception into the Kingdom of Heaven",
    "Of Spiritual Darkness from Misinterpretation of Scripture",
    "Of Demonology and Other Relics of the Religion of the Gentiles",
    "Of Darkness from Vain Philosophy and Fabulous Traditions",
    "Of the Benefit that Proceedeth from Such Darkness",
]

HOBBES_PARTS = {
    1: "Part I. Of Man",
    17: "Part II. Of Common-wealth",
    32: "Part III. Of a Christian Common-wealth",
    44: "Part IV. Of the Kingdom of Darkness",
}

HEAD_FIX = {"to": 10, "i0": 10, "ii": 11, "iz": 12, "t3": 13, "t4": 14,
            "t5": 15, "t6": 16, "i9": 19, "2r": 21, "2a": 22, "2b": 23,
            "2c": 24, "2g": 29, "20": 29, "3r": 31, "3%": 31, "3o": 30,
            "4r": 41}


def hobbes_chapter_pages(cache):
    """First printed page of each chapter, read from the running heads."""
    parsed = {}
    for p in range(34, 593):
        head = " ".join(l["text"] for l in json.load(open(f"{cache}/{p:04d}.json"))
                        if l["y0"] < 170)
        m = re.search(r"Chap\.?\s*([0-9A-Za-z]{1,3})", head)
        if not m:
            continue
        tok = m.group(1).lower().strip(".")
        n = int(tok) if tok.isdigit() else HEAD_FIX.get(tok)
        if n and 1 <= n <= 47:
            parsed.setdefault(n, []).append(p)
    starts, prev = {}, 33
    for n in range(1, 48):
        pick = [p for p in parsed.get(n, []) if p > prev]
        starts[n] = pick[0] if pick else prev + 1
        prev = starts[n]
    return starts


CHAP_LINE = re.compile(r"^[^A-Za-z]*(?:CHAP|Chap|CHAPAF)[A-Za-z]*\.?\s*[IVXL]{0,6}\.?")


def hobbes_chapter_cuts(cfg):
    """Where each chapter really begins: a page and a y, not just a page.

    The 1651 folio starts chapters in the middle of a page and changes the
    running head only on the page after, so the chapter's printed title has
    to be found on the head page or the one before it.
    """
    cache = cfg["cache"]
    heads = hobbes_chapter_pages(cache)
    cuts, prev_page = [], 33
    for n in range(1, 48):
        head_page = max(heads[n], prev_page + 1)
        cut_page, cut_y = head_page, 0
        best = None
        for p in (head_page - 1, head_page):
            if p <= prev_page:
                continue
            try:
                lines = json.load(open(f"{cache}/{p:04d}.json"))
            except FileNotFoundError:
                continue
            for line in lines:
                text = line["text"].strip()
                if (line["y0"] > 175 and len(text) < 70
                        and CHAP_LINE.match(text)):
                    if best is None or (p, line["y0"]) > best:
                        best = (p, line["y0"])
        if best:
            cut_page, cut_y = best
        cuts.append({"page": cut_page, "y0": cut_y, "n": n,
                     "label": "Chapter %d" % n, "title": HOBBES_CHAPTERS[n - 1]})
        prev_page = head_page
    return cuts


def hobbes_chapters(cfg):
    cuts = hobbes_chapter_cuts(cfg)
    # The Epistle Dedicatory is the two-page dedication leaf (38 recto, 39
    # verso); pages 40-42 are the printed "Contents of the Chapters" and the
    # errata, which the book's own table of contents replaces; the
    # Introduction opens on 43, not 44.
    out = [{"page": 38, "y0": 0, "label": "", "title": "The Epistle Dedicatory",
            "parts": []},
           {"page": 43, "y0": 0, "label": "", "title": "The Introduction",
            "parts": []}]
    parts = []
    for cut in cuts:
        if cut["n"] in HOBBES_PARTS:
            parts = [HOBBES_PARTS[cut["n"]]]
        out.append({**cut, "parts": parts})
        parts = []
    out.append({"page": 583, "y0": 0, "label": "",
                "title": "A Review, and Conclusion", "parts": []})
    return out


def is_furniture(cfg, line):
    """Printed page furniture: folios, running heads, scan speckle."""
    text = line["text"].strip()
    if line["y0"] < cfg["top"]:
        return True
    if FOLIO.match(text) and (line["y0"] > 1850 or line["x0"] > 600):
        return True                     # page number, top or bottom
    if FOLIO.match(text) and len(text) <= 5:
        return True
    letters = sum(c.isalpha() for c in text)
    if len(text) < 45 and letters / max(1, len(text)) < 0.55:
        return True                     # speckle the OCR read as a line
    if line["x0"] > 620 and len(text) < 30:
        return True                     # marginal folio
    return False

BOOKS = {
    "leviathan": {
        "cache": "typeset/build/ocr_hobbes",
        "slug": "Leviathan",
        "title": "Leviathan",
        "subtitle": "or the Matter, Forme, and Power of a Common-wealth",
        "author": "Thomas Hobbes",
        "year": "1651",
        "first_page": 38, "last_page": 592,
        "top": 175, "bottom": 2020, "indent": 34,
        "chapters": "hobbes",
        # printed "Contents of the Chapters" (40-42) and the errata leaf
        "skip_pages": (40, 41, 42),
        # printed headings the OCR read into the text column
        "drop_re": [
            r"^CHAP\.?\s?[IVXL]{1,6}\.?(\s*\[?\d+\]?)?$",
            r"^CHAPTER\s?[IVXL]{1,6}\.?$",
            r"^OF\s+[A-Z][A-Z ,\-]{2,}\.?\s*(\[?\d+\]?)?$",
            r"^Part\s?\d+\.?\s",
            r"^Parte\.",
            r"^THE\s+(CONTENTS|INTRODUCTION|EPISTLE)[.,]?(\s*\d+)?$",
            r"^INTRODUCTION\.?$",
            r"^A\s+REVIEW",
        ],
        "fixes": [
            # the embedded text layer splits the dedicatee's name
            (r"\bGO DOLPHIN\b", "GODOLPHIN"),
            (r"\btesti monies\b", "testimonies"),
        ],
        "source_note": (
            "The text is Thomas Hobbes's \\emph{Leviathan} (London, 1651). "
            "Hobbes died in 1679, so the work is in the public domain in the "
            "European Union. The supplied PDF is a page-image scan of the "
            "Oxford reprint edited by W. G. Pogson Smith; only the 1651 text "
            "is reproduced here, not the editor's 1909 introduction. The "
            "pages were re-OCR'd for this edition, so reading errors proper "
            "to OCR remain in places; Hobbes's spelling is unchanged."
        ),
    },
    "gospel-of-wealth": {
        "cache": "typeset/build/ocr_carnegie",
        "slug": "The_Gospel_of_Wealth",
        "title": "The Gospel of Wealth",
        "subtitle": "and Other Timely Essays",
        "author": "Andrew Carnegie",
        "year": "1900",
        "first_page": 10,
        "last_page": 332,
        "top": 170, "bottom": 1950, "indent": 34,
        "chapters": [
            (11, "Introduction"), (29, "The Gospel of Wealth"),
            (75, "The Advantages of Poverty"),
            (113, "Popular Illusions about Trusts"),
            (135, "An Employer's View of the Labor Question"),
            (155, "Results of the Labor Struggle"),
            (179, "Distant Possessions"),
            (197, "Americanism versus Imperialism"),
            (237, "Democracy in England"),
            (249, "Home Rule in America"),
            (279, "Does America Hate England?"),
            (297, "Imperial Federation"),
        ],
        "source_note": (
            "The text is Andrew Carnegie's \\emph{The Gospel of Wealth and "
            "Other Timely Essays} (New York: The Century Co., 1900). Carnegie "
            "died in 1919, so the book is in the public domain. The supplied "
            "PDF is a page-image scan whose embedded text layer was too poor "
            "to use, so the pages were re-OCR'd for this edition. Reading "
            "errors proper to OCR will remain in places; no wording has been "
            "invented or silently rewritten."
        ),
    },
}


def items_from_ocr(cfg):
    items = []
    drop = [re.compile(p) for p in cfg.get("drop_re", [])]
    skip = set(cfg.get("skip_pages", ()))
    for p in range(cfg["first_page"], cfg["last_page"] + 1):
        if p in skip:
            continue
        path = os.path.join(cfg["cache"], "%04d.json" % p)
        if not os.path.exists(path):
            continue
        for line in json.load(open(path)):
            if line["y0"] > cfg["bottom"] or is_furniture(cfg, line):
                continue
            text = line["text"].strip()
            if not text:
                continue
            if any(rx.search(text) for rx in drop):
                continue
            items.append({
                "page": p, "y0": line["y0"], "x0": line["x0"], "x1": line["x1"],
                "size": float(line["h"]), "text": text,
                "runs": [{"italic": False, "text": text}],
            })
    return items


def mark_paragraphs(items, cfg):
    margins = {}
    for it in items:
        margins.setdefault(it["page"], []).append(it["x0"])
    margins = {p: round(max(set(v), key=v.count)) for p, v in margins.items()}
    for it in items:
        it["indented"] = (it["x0"] - margins.get(it["page"], it["x0"])) >= cfg["indent"]


def build(items, cfg, hyph):
    chapters = cfg["chapters"]
    if chapters == "hobbes":
        cuts = hobbes_chapters(cfg)
    else:
        cuts = [{"page": p, "y0": 0, "label": "", "title": t, "parts": []}
                for p, t in chapters]

    # line step in pixels, from the data itself
    gaps, by_page = [], {}
    for it in items:
        by_page.setdefault(it["page"], []).append(it["y0"])
    for ys in by_page.values():
        ys.sort()
        gaps += [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 120]
    gaps.sort()
    step = gaps[len(gaps) // 2] if gaps else 60
    cfg["_step"] = step

    def past(cut, it):
        return (it["page"], it["y0"]) >= (cut["page"], cut["y0"])

    # split the stream at the cuts; each run of lines is labelled with the
    # cut that opened it
    segments, body, ci, cur = [], [], 0, None
    for it in items:
        while ci < len(cuts) and past(cuts[ci], it):
            segments.append((cur, body))
            cur = cuts[ci]
            body = []
            ci += 1
        body.append(it)
    segments.append((cur, body))
    segments = [(c, b) for c, b in segments if b]

    # a chapter's printed title sits just above its cut; the part heading
    # ("OF MAN.") just above that.  Neither belongs to the running text.
    for cut, its in segments:
        if not cut:
            continue
        for it in its:
            if (it["page"] == cut["page"]
                    and -320 <= it["y0"] - cut["y0"] <= 260
                    and is_title_line(it["text"])):
                it["_drop"] = True

    blocks = []
    for cut, its in segments:
        if cut:
            for part in cut["parts"]:
                blocks.append({"type": "part", "title": part})
            blocks.append({"type": "chapter", "label": cut["label"],
                           "title": cut["title"]})
        its = [it for it in its if not it.get("_drop")]
        if its:
            blocks.extend(bk.assemble(its, step=step, floor=cfg["bottom"] - 30,
                                      hyphenator=hyph))
    return blocks


def emit(blocks, cfg):
    out = [bk.preamble(cfg["title"], cfg["author"])]
    out.append(bk.title_page(cfg["title"], cfg["author"],
                             [cfg.get("subtitle", ""), cfg["year"]]))
    out.append(r"""\pagenumbering{roman}
\pagestyle{fancy}
\phantomsection
\chapter*{A Note on This Edition}
\addcontentsline{toc}{chapter}{A Note on This Edition}
\markboth{A Note on This Edition}{A Note on This Edition}

%s

\tableofcontents
\clearpage
\pagenumbering{arabic}
\pagestyle{fancy}
""" % cfg["source_note"])
    for b in blocks:
        if b["type"] == "part":
            out.append("\\bookpart{%s}\n" % b["title"])
        elif b["type"] == "chapter":
            if b["label"]:
                out.append("\\bookchapter{%s}{%s}\n" % (b["label"], b["title"]))
            else:
                out.append("\\bookchapterplain{%s}\n" % b["title"])
        elif b["type"] == "para":
            out.append(bk.para_tex(b) + "\n")
    out.append("\n\\end{document}\n")
    return "\n".join(out)


def main():
    cfg = BOOKS[sys.argv[1]]
    hyph = bk.Hyphenator()
    items = items_from_ocr(cfg)
    mark_paragraphs(items, cfg)
    blocks = build(items, cfg, hyph)
    fixes = [(re.compile(p), r) for p, r in cfg.get("fixes", [])]
    for b in blocks:            # close up mid-line word breaks from the OCR
        if b["type"] == "para":
            for run in b["runs"]:
                for pat, repl in fixes:
                    run["text"] = pat.sub(repl, run["text"])
                run["text"] = bk.fix_glued(
                    bk.fix_word_hyphens(run["text"], hyph))
    tex = emit(blocks, cfg)
    workdir = os.path.join(OUT, cfg["slug"])
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, cfg["slug"] + ".tex")
    open(path, "w").write(tex)
    json.dump(blocks, open(os.path.join(workdir, "blocks.json"), "w"))
    print("blocks:", len(blocks),
          "paras:", sum(1 for b in blocks if b["type"] == "para"), "->", path)


if __name__ == "__main__":
    main()
