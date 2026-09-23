"""Typeset the reading-list PDFs that have a usable text layer.

Usage:  python3 typeset/typebooks.py <book-key> [--no-compile]

This is the generalised sibling of ``driver.py`` (one PDF, one config) and
``ocr_book.py`` (scanned pages, OCR cache).  Every book is described with the
same small vocabulary:

    first_page/last_page  which printed pages carry the text
    drop_re               folios, running heads and junk, dropped by pattern
    top/bottom            y-bands outside which a line is furniture
    body_size             font size (range) of the running text
    note_size             font size of the footnotes (kept, per chapter)
    section_size          font size of numbered section headings
    head_drop_re          printed headings already carried by ``structure``
    structure             the table of parts, chapters and back matter

The structure tables were read off the printed pages themselves (running
heads, chapter openings, tables of contents): they are data, not guesswork,
and they are what makes the output paginate like a book.
"""

import json
import os
import re
import subprocess
import sys

import fitz

import bookkit as bk

OUT = "typeset"


# --------------------------------------------------------------------------
# extraction


def merge_dropcaps(items):
    """Fold a huge single-letter drop cap into the line it opens.

    Chapter openings in several of these PDFs set the first letter at two or
    three times the body size, so the extractor reports it as a line of its
    own ("H") next to the rest of the word ("UMAN action ...").  The cap's
    box sits lower than the line it belongs to, so it does not reliably come
    first in reading order; the cap is matched to its line by position.
    """
    out = list(items)
    body_size = sorted(it["size"] for it in out)[len(out) // 2] if out else 10
    caps = [it for it in out
            if len(it["text"]) <= 2 and it["text"].isalpha()
            and it["size"] > 1.8 * body_size]
    for cap in caps:
        near = [it for it in out
                if it is not cap and it["page"] == cap["page"]
                and abs(it["y0"] - cap["y0"]) < cap["size"]]
        if not near:
            continue
        host = min(near, key=lambda it: (abs(it["y0"] - cap["y0"]), it["x0"]))
        host["text"] = cap["text"][0] + host["text"]
        # the runs, not ``text``, are what reach the page: keep them in step
        runs = [dict(r) for r in host.get("runs") or []]
        if runs:
            runs[0]["text"] = cap["text"][0] + runs[0]["text"]
        else:
            runs = [{"italic": False, "text": host["text"]}]
        host["runs"] = runs
        out.remove(cap)
    return out


SEPARATOR = re.compile(r"^[\s*\u2217]+$")


def page_lines(cfg):
    """Body lines, footnote lines, and a count of dropped furniture."""
    doc = fitz.open(cfg["src"])
    lines = bk.extract_lines(doc, cfg["first_page"], cfg["last_page"])
    drop = [re.compile(p) for p in cfg.get("drop_re", [])]
    body_lo, body_hi = cfg["body_size"]
    note = cfg.get("note_size")
    sec = cfg.get("section_size")
    body, notes, caps, dropped = [], [], [], 0
    for it in lines:
        if it["y0"] < cfg.get("top", 0) or it["y0"] > cfg.get("bottom", 10 ** 6):
            dropped += 1
            continue
        if any(p.search(it["text"]) for p in drop):
            dropped += 1
            continue
        if body_lo <= it["size"] <= body_hi:
            body.append(it)
        elif sec and sec[0] <= it["size"] <= sec[1] and _is_section(cfg, it):
            it["section"] = True
            body.append(it)
        elif (it["size"] > 12 and SEPARATOR.match(it["text"])
              and "*" in it["text"]):
            it["separator"] = True       # printed "* * * *" section break
            body.append(it)
        elif note and note[0] <= it["size"] <= note[1]:
            notes.append(it)
        elif (len(it["text"]) <= 2 and it["text"].isalpha()
              and it["size"] > 1.8 * body_lo):
            caps.append(it)              # a chapter drop cap, awaiting its line
        else:
            dropped += 1
    if cfg.get("merge_dropcaps", True):
        merged = merge_dropcaps(sorted(body + caps,
                                      key=lambda it: (it["page"], it["y0"],
                                                      it["x0"])))
        body = [it for it in merged
                if it.get("section") or it.get("separator")
                or body_lo <= it["size"] <= body_hi]
    return body, notes, dropped


def _is_section(cfg, it):
    """True if a line in the section-size band is a heading, not a note.

    Where the section band overlaps the footnote band (small-caps headings in
    Capitalism and Freedom sit at the same size as its notes) only an
    all-capitals line counts as a heading.
    """
    note = cfg.get("note_size")
    if not note or not (note[0] <= it["size"] <= note[1]):
        return True
    return len(it["text"]) < 70 and it["text"].strip().isupper()


def steps_and_margins(items):
    """Line step and per-page left margin, measured from the data itself."""
    gaps, by_page = [], {}
    for it in items:
        by_page.setdefault(it["page"], []).append(it)
    for its in by_page.values():
        ys = sorted(x["y0"] for x in its)
        gaps += [b - a for a, b in zip(ys, ys[1:]) if 0 < b - a < 200]
    gaps.sort()
    step = gaps[len(gaps) // 2] if gaps else 13.0
    margins = {}
    for page, its in by_page.items():
        xs = [x["x0"] for x in its]
        margins[page] = round(max(set(xs), key=xs.count))
    return step, margins


def text_floor(items, fallback):
    ys = sorted(it["y1"] for it in items)
    return round(ys[int(len(ys) * 0.97)], 1) if ys else fallback


# --------------------------------------------------------------------------
# building


NOTE_START = re.compile(r"^(?:\d{1,3}|[a-z])[.)]?\s+\S")


def note_paragraphs(lines):
    """Group the footnote lines of one page into note-sized paragraphs."""
    paras, buf = [], []
    for it in lines:
        text = it["text"]
        if buf and NOTE_START.match(text):
            paras.append(" ".join(buf))
            buf = [text]
        else:
            buf.append(text)
    if buf:
        paras.append(" ".join(buf))
    return [p for p in paras if p.strip()]


def build(cfg, items, note_lines, step):
    """Cut the page stream at the structure table and assemble paragraphs."""
    table = sorted((e["page"], e.get("y0", 0), e) for e in cfg["structure"])
    # printed heading lines, by (page, y), already carried by ``structure``
    drops = set()
    for e in cfg["structure"]:
        for y in e.get("drop", ()):
            drops.add((e["page"], round(y, 1)))

    def is_dropped(it):
        if (it["page"], round(it["y0"], 1)) in drops:
            return True
        return any((it["page"], round(it["y0"] - delta, 1)) in drops
                   for delta in (-0.6, -0.3, 0.3, 0.6))

    head_re = [re.compile(p) for p in cfg.get("head_drop_re", [])]
    body_hi = cfg["body_size"][1]
    margins, floor, hyph = cfg["_margins"], cfg["_floor"], cfg["_hyph"]

    notes_by_page = {}
    for it in note_lines:
        notes_by_page.setdefault(it["page"], []).append(it)

    blocks, body, chapter_notes = [], [], []

    def flush(with_notes=True):
        nonlocal body, chapter_notes
        if body:
            for it in body:
                it["indented"] = (
                    it["x0"] - margins.get(it["page"], it["x0"])
                    >= cfg.get("indent", 9.0))
            blocks.extend(bk.assemble(body, step=step, floor=floor,
                                      hyphenator=hyph))
            body = []
        if with_notes and chapter_notes:
            blocks.append({"type": "notes", "paras": chapter_notes})
            chapter_notes = []

    def emit(e):
        if e["kind"] == "part":
            blocks.append({"type": "part", "title": e["title"]})
        elif e["kind"] == "chapter":
            blocks.append({"type": "chapter", "label": e.get("label", ""),
                           "title": e["title"]})
        elif e["kind"] == "backmatter":
            blocks.append({"type": "backmatter", "title": e["title"]})
        if e["page"] in notes_by_page:
            chapter_notes.extend(note_paragraphs(notes_by_page.pop(e["page"])))

    cursor = 0
    for it in items:
        if is_dropped(it):
            continue
        while cursor < len(table) and (table[cursor][0], table[cursor][1]) <= \
                (it["page"], it["y0"]):
            flush()
            emit(table[cursor][2])
            cursor += 1
        if it.get("section"):
            flush(with_notes=False)      # the chapter's notes stay at its end
            blocks.append({"type": "section",
                           "title": section_title(cfg, it["text"])})
            continue
        if it.get("separator"):
            flush(with_notes=False)
            blocks.append({"type": "separator"})
            continue
        if any(p.search(it["text"]) for p in head_re):
            continue
        if it["page"] in notes_by_page:
            chapter_notes.extend(note_paragraphs(notes_by_page.pop(it["page"])))
        body.append(it)

    while cursor < len(table):           # headings with no body left to follow
        flush()
        emit(table[cursor][2])
        cursor += 1
    for page in sorted(notes_by_page):
        chapter_notes.extend(note_paragraphs(notes_by_page[page]))
    flush()
    return blocks


def section_title(cfg, text):
    for pat in cfg.get("section_strip", []):
        text = re.sub(pat, "", text).strip()
    return bk.smart_title(text) if cfg.get("section_titlecase", True) else text


# --------------------------------------------------------------------------
# LaTeX


NOTES_EXTRA = r"""
\newcommand{\booknoteshead}{%
  \phantomsection\section*{Notes}%
  \addcontentsline{toc}{section}{Notes}\markright{Notes}}
"""


def emit(cfg, blocks):
    out = [bk.preamble(cfg["title"], cfg["author"], extra=NOTES_EXTRA)]
    out.append(bk.title_page(cfg["title"], cfg["author"],
                             [cfg.get("subtitle", ""), cfg.get("year", "")]))
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
            if b.get("label"):
                out.append("\\bookchapter{%s}{%s}\n" % (b["label"], b["title"]))
            else:
                out.append("\\bookchapterplain{%s}\n" % b["title"])
        elif b["type"] == "section":
            out.append("\\booksection{%s}\n" % b["title"])
        elif b["type"] == "separator":
            out.append("\\begin{center}\\textasteriskcentered\\hspace{0.9em}"
                       "\\textasteriskcentered\\hspace{0.9em}"
                       "\\textasteriskcentered\\end{center}\n")
        elif b["type"] == "backmatter":
            out.append("\\bookbackmatter{%s}\n" % b["title"])
        elif b["type"] == "notes":
            out.append("\\booknoteshead\n")
            out.append("{\\footnotesize\\setlength{\\parindent}{0pt}"
                       "\\setlength{\\parskip}{5pt}\n")
            for p in b["paras"]:
                out.append(bk.para_tex({"runs": [{"italic": False, "text": p}]})
                           + "\\par\n")
            out.append("}\n")
        elif b["type"] == "para":
            out.append(bk.para_tex(b) + "\n")
    out.append("\n\\end{document}\n")
    return "\n".join(out)


def repair(cfg, blocks):
    """Per-book word repairs, then close up OCR word breaks."""
    hyph = cfg["_hyph"]
    fixes = [(re.compile(p), r) for p, r in cfg.get("fixes", [])]
    for b in blocks:
        runs = b.get("runs")
        if not runs:
            continue
        for run in runs:
            for pat, repl in fixes:
                run["text"] = pat.sub(repl, run["text"])
            run["text"] = bk.fix_word_hyphens(run["text"], hyph)
    return blocks


# --------------------------------------------------------------------------
# verification


def words(text):
    lig = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
           "\ufb04": "ffl", "\u017f": "s"}
    for k, v in lig.items():
        text = text.replace(k, v)
    text = (text.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2014", "-").replace("\u2013", "-"))
    text = (text.replace("\u2010", "-").replace("\u2011", "-")
                .replace("\u00bf", "?").replace("\u00a1", "!").replace("`", ""))
    return [w for w in (t.replace("-", "").strip(".,;:\"'()[]?!")
                        for t in text.split()) if w]


def verify(cfg, blocks, pdfpath, marker, show=12):
    src = []
    for b in blocks:
        if b["type"] == "para":
            src.extend(words("".join(r["text"] for r in b["runs"])))
        elif b["type"] in ("section", "chapter", "backmatter"):
            src.extend(words(b["title"]))
        elif b["type"] == "notes":
            for p in b["paras"]:
                src.extend(words(p))
    doc = fitz.open(pdfpath)
    got = []
    for page in doc:
        h = page.rect.height
        lines, pending = [], None
        for blk in page.get_text("dict")["blocks"]:
            if blk["type"] != 0:
                continue
            for line in blk["lines"]:
                if line["bbox"][1] < h * 0.06 or line["bbox"][3] > h * 0.94:
                    continue
                text = "".join(s["text"] for s in line["spans"]).strip()
                if text:
                    lines.append((line["bbox"][1], text))
        lines.sort()
        for _, text in lines:
            if pending is not None:
                text, pending = pending + text, None
            if text.endswith(("-", "\u2010", "\u2011")) and not text.endswith("--"):
                pending = text[:-1]
                continue
            got.extend(words(text))
        if pending is not None:
            got.extend(words(pending))
            pending = None
    # start comparing at the first source words, so that front matter, the
    # table of contents and the running heads cannot shift the alignment
    probe = src[:8]
    for j in range(len(got)):
        if got[j:j + 8] == probe:
            got = got[j:]
            break
    else:
        try:
            got = got[got.index(marker):]
        except ValueError:
            pass
    i = j = 0
    diffs = []
    while i < len(src) and j < len(got):
        if src[i] == got[j]:
            i += 1
            j += 1
            continue
        for k in range(1, 30):
            if i + k < len(src) and src[i + k] == got[j]:
                diffs.append(("source-only", " ".join(src[i:i + k])))
                i += k
                break
            if j + k < len(got) and got[j + k] == src[i]:
                diffs.append(("pdf-only", " ".join(got[j:j + k])))
                j += k
                break
        else:
            diffs.append(("mismatch", "%s -> %s" % (src[i], got[j])))
            i += 1
            j += 1
    print("   verify: source=%d pdf=%d differences=%d"
          % (len(src), len(got), len(diffs)))
    for kind, what in diffs[:show]:
        print("      [%s] %s" % (kind, what))
    return len(diffs)


# --------------------------------------------------------------------------
# driver


def main():
    key = sys.argv[1]
    cfg = dict(BOOKS[key])
    if callable(cfg["structure"]):
        cfg["structure"] = cfg["structure"]()
    hyph = bk.Hyphenator()
    items, note_lines, dropped = page_lines(cfg)
    step, margins = steps_and_margins(items)
    cfg["_hyph"] = hyph
    cfg["_margins"] = margins
    cfg["_floor"] = cfg.get("floor") or text_floor(items, cfg["bottom"])
    blocks = build(cfg, items, note_lines, step)
    blocks = repair(cfg, blocks)
    tex = emit(cfg, blocks)
    workdir = os.path.join(OUT, cfg["slug"])
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, cfg["slug"] + ".tex")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(tex)
    json.dump(blocks, open(os.path.join(workdir, "blocks.json"), "w"),
              ensure_ascii=False)
    print("blocks: %d  paras: %d  notes: %d  dropped: %d  step: %.1f  floor: %.1f"
          % (len(blocks), sum(1 for b in blocks if b["type"] == "para"),
             sum(1 for b in blocks if b["type"] == "notes"), dropped, step,
             cfg["_floor"]))
    if "--no-compile" not in sys.argv:
        run = ["pdflatex", "-interaction=nonstopmode", cfg["slug"] + ".tex"]
        for _ in range(2):
            subprocess.run(run, cwd=workdir, capture_output=True)
        pdf = os.path.join(workdir, cfg["slug"] + ".pdf")
        print("   ->", pdf)
        verify(cfg, blocks, pdf, cfg.get("verify_marker", "subject"))


from configs import BOOKS        # noqa: E402


if __name__ == "__main__":
    main()
