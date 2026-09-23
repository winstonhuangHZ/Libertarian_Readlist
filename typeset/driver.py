"""Turn one of the reading-list PDFs into a typeset LaTeX book.

Usage:  python3 typeset/driver.py <book-key>

Each book gets a config entry describing its printed furniture, where the body
starts, and how its headings look. Everything else is shared.
"""

import json
import os
import re
import subprocess
import sys

import fitz

import bookkit as bk

OUT = "typeset"


BOOKS = {
    "on-liberty": {
        "src": "On_Liberty.pdf",
        "slug": "On_Liberty",
        "title": "On Liberty",
        "author": "John Stuart Mill",
        "year": "1859",
        "first_page": 5,
        "last_page": 108,
        "furniture": [r"^\d+\s*/\s*John Stuart Mill$", r"^On Liberty\s*/\s*\d+$"],
        "body_sizes": (11.0,),
        "heading_min": 13.0,
        "drop_texts": ["The End"],
        "chapter_re": r"^Chapter\s+(\d+)\.?$",
        "back_matter": {"Notes": "Notes"},
        # OCR slip: em dashes read as hyphens; "too often" read as one word.
        "fixes": [
            (r"\bnatures-is\b", "natures\u2014is"),
            (r"\bcharacter-and\b", "character\u2014and"),
            (r"\bcharacter-which\b", "character\u2014which"),
            (r"\btoo-often\b", "too often"),
        ],
        "source_note": (
            "The text is the 1859 essay as reprinted by Batoche Books "
            "(Kitchener, Ontario, 2001); Mill died in 1873, so both the essay "
            "and that reprint are in the public domain. The supplied PDF is "
            "an OCR'd scan, so the text layer carried scanning damage: broken "
            "words at line ends, dropped characters and misread punctuation. "
            "Those have been repaired here; spellings and punctuation follow "
            "the source."
        ),
    },
}


def to_items(doc, cfg):
    """Extracted lines split into body lines and heading/structural blocks."""
    raw = bk.extract_lines(doc, cfg["first_page"], cfg["last_page"])
    furniture = [re.compile(p) for p in cfg.get("furniture", [])]
    drop = set(cfg.get("drop_texts", []))
    body_sizes = set(cfg.get("body_sizes", ()))
    hmin = cfg.get("heading_min", 99)

    # classify
    for it in raw:
        it["kind"] = "body"
        if any(p.search(it["text"]) for p in furniture):
            it["kind"] = "furniture"
        elif it["text"] in drop:
            it["kind"] = "drop"
        elif it["size"] >= hmin:
            it["kind"] = "heading"

    # each heading line becomes its own structural item; the driver pairs a
    # "Chapter N" label with the title lines that follow it
    items = []
    i = 0
    while i < len(raw):
        it = raw[i]
        if it["kind"] == "heading":
            items.append({"block": {"type": "heading", "text": it["text"]},
                          "page": it["page"], "y0": it["y0"]})
            i += 1
            continue
        if it["kind"] == "body":
            items.append(it)
        i += 1
    return items, raw


def build_blocks(items, cfg):
    """Walk the stream, emitting chapters, sections and paragraphs."""
    chapter_re = re.compile(cfg["chapter_re"])
    back_matter = cfg.get("back_matter", {})
    blocks = []

    # Mark the lines that begin a paragraph: these start at the page's left
    # margin plus the printed indent, which varies between verso and recto.
    margins = {}
    for it in items:
        if "block" not in it:
            margins.setdefault(it["page"], []).append(it["x0"])
    margins = {p: round(max(set(v), key=v.count)) for p, v in margins.items()}
    for it in items:
        if "block" not in it:
            it["indented"] = (it["x0"] - margins[it["page"]]) >= cfg.get("indent_min", 8)

    # split the stream at structural blocks
    chunks = [[None, []]]        # (heading-block or None, [body lines after it])
    for it in items:
        if "block" in it:
            chunks.append([it["block"], []])
        else:
            chunks[-1][1].append(it)

    label = None         # "Chapter N" waiting for its title
    title_lines = []

    def flush_heading():
        nonlocal label, title_lines
        if not title_lines and not label:
            return
        title = ""
        for part in title_lines:
            if title.endswith("-"):
                keep = HYPH.join(title, part) if HYPH else ""
                title = title[:-1] + keep + part   # "Well-" + "being." -> "Well-being."
            else:
                title = (title + " " + part).strip()
        if label:
            blocks.append({"type": "chapter", "label": label, "title": title})
        elif title:
            blocks.append({"type": "section", "title": title})
        label, title_lines = None, []

    for head, body in chunks:
        if head is not None:
            text = head["text"]
            if text in back_matter:
                flush_heading()
                blocks.append({"type": "backmatter", "title": back_matter[text]})
                continue
            m = chapter_re.match(text)
            if m:
                flush_heading()
                label = text.strip(".")
            else:
                title_lines.append(text)
        if body:
            flush_heading()
            blocks.extend(bk.assemble([dict(x) for x in body],
                                      floor=cfg.get("floor", 578.5),
                                      hyphenator=HYPH))
    flush_heading()
    return blocks


HYPH = None


def emit(blocks, cfg):
    out = [bk.preamble(cfg["title"], cfg["author"])]
    out.append(bk.title_page(cfg["title"], cfg["author"],
                             [cfg.get("year", "")]))
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
        if b["type"] == "chapter":
            out.append("\\bookchapter{%s}{%s}\n" % (b["label"], b["title"]))
        elif b["type"] == "section":
            out.append("\\booksection{%s}\n" % b["title"])
        elif b["type"] == "backmatter":
            out.append("\\bookbackmatter{%s}\n" % b["title"])
        elif b["type"] == "para":
            out.append(bk.para_tex(b) + "\n")
    out.append("\n\\end{document}\n")
    return "\n".join(out)


def postprocess(blocks, cfg):
    """Apply the per-book repairs, then undo the OCR's mid-line word breaks."""
    fixed = []
    for block in blocks:
        if block["type"] != "para":
            continue
        for run in block["runs"]:
            for pattern, repl in cfg.get("fixes", []):
                run["text"] = re.sub(pattern, repl, run["text"])
            run["text"] = bk.fix_word_hyphens(run["text"], HYPH, fixed)
    return fixed


def main():
    global HYPH
    key = sys.argv[1]
    cfg = BOOKS[key]
    HYPH = bk.Hyphenator()
    doc = fitz.open(cfg["src"])
    items, raw = to_items(doc, cfg)
    blocks = build_blocks(items, cfg)
    fixed = postprocess(blocks, cfg)
    tex = emit(blocks, cfg)

    workdir = os.path.join(OUT, cfg["slug"])
    os.makedirs(workdir, exist_ok=True)
    texpath = os.path.join(workdir, cfg["slug"] + ".tex")
    open(texpath, "w").write(tex)
    json.dump({"cfg": {k: v for k, v in cfg.items() if k != "source_note"},
               "blocks": blocks}, open(os.path.join(workdir, "blocks.json"), "w"))
    print("blocks:", len(blocks),
          "paras:", sum(1 for b in blocks if b["type"] == "para"),
          "->", texpath)
    print("word breaks closed up:", len(fixed))
    print("  ", ", ".join(sorted(set(fixed))))


if __name__ == "__main__":
    main()
