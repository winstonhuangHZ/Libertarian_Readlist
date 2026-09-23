"""Audit a typeset book against its source PDF and its config.

Usage:  python3 typeset/audit.py <book-key> [...]       (or --all)

Four checks, each of which has caught a real defect:

  coverage   words of the source body vs words of the built PDF
  furniture  the config's own drop patterns must not survive into the book
  structure  every part/chapter entry must sit on a page that carries its
             printed heading (this is what caught two wrong hand-typed pages)
  contents   page numbers must not go backwards; no empty entries
"""

import glob
import os
import re
import sys

import fitz

import bookkit as bk
import typebooks as tb
from configs import BOOKS


def out_pdf(cfg):
    return "typeset/%s/%s.pdf" % (cfg["slug"], cfg["slug"])


def pdf_words(cfg):
    """Words of the built book, from the first body line onwards."""
    doc = fitz.open(out_pdf(cfg))
    out, pending = [], None
    for page in doc:
        h = page.rect.height
        lines = []
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
            out.append(text)
        if pending is not None:
            out.append(pending)
            pending = None
    words = tb.words(" ".join(out))
    header = tb.words(cfg["title"])
    for i in range(len(words)):
        if words[i:i + len(header)] == header:
            return words[i:]
    return words


def leaked_furniture(cfg):
    """Lines of the built book that the config says are page furniture."""
    pats = [re.compile(p) for p in cfg.get("drop_re", [])]
    doc = fitz.open(out_pdf(cfg))
    hits, body_start = [], 0
    for page in doc:
        text = page.get_text()
        if ("A Note on This Edition" in text or "Contents" in text
                or ". . . ." in text or ". . . " in text):
            body_start = page.number + 1
    for page in doc:
        if page.number < body_start:
            continue
        h = page.rect.height
        lines = []
        for blk in page.get_text("dict")["blocks"]:
            if blk["type"] != 0:
                continue
            for line in blk["lines"]:
                text = "".join(s["text"] for s in line["spans"]).strip()
                if not text or line["bbox"][1] < h * 0.08 or line["bbox"][3] > h * 0.92:
                    continue
                lines.append((line["bbox"][1], text))
        # the contents and the note on the edition are front matter: a page
        # number there is a contents entry, not page furniture in the text
        head = min(lines)[1] if lines else ""
        if head.startswith("Contents") or head.startswith("A Note on"):
            continue
        for _, text in lines:
            if any(p.search(text) for p in pats):
                hits.append((page.number + 1, text[:60]))
    return hits


def structure_problems(cfg, structure):
    """Entries whose page does not carry the printed heading they describe."""
    def squash(text):
        return re.sub(r"[^a-z]", "", text.lower())

    doc = fitz.open(cfg["src"])
    pages = {}
    for p in range(doc.page_count):
        pages[p] = bk.extract_lines(doc, p, p)
    problems = []
    for e in structure:
        lines = pages.get(e["page"], [])
        if not e["title"]:
            continue
        ys = [round(l["y0"], 1) for l in lines]
        if e.get("drop") and any(
                abs(y - d) <= 1.0 for y in ys for d in e["drop"]):
            continue
        # accept any short window of the title: printed titles wrap, and the
        # OCR sometimes splits a word ("TH E ABAN DON ED ROAD")
        words = tb.words(e["title"])
        windows = [squash(" ".join(words[i:i + 3]))
                   for i in range(max(1, len(words) - 2))]
        page_text = squash(" ".join(l["text"] for l in lines))
        if not any(w and w in page_text for w in windows):
            problems.append((e["page"], e["kind"], e["title"]))
    return problems


def contents_problems(cfg):
    path = "typeset/%s/%s.toc" % (cfg["slug"], cfg["slug"])
    if not os.path.exists(path):
        return ["no .toc"]
    problems, last, count = [], 0, 0
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\\contentsline \{(chapter|part|section)\}\{(.*?)\}"
                     r"\{(\d+|[ivxl]+)\}", line)
        if not m:
            continue
        count += 1
        title = re.sub(r"\\[a-zA-Z]+\s*", "", m.group(2)).strip()
        page = m.group(3)
        if not title:
            problems.append("empty title: %s" % line.strip()[:60])
        if page.isdigit():
            if int(page) < last:
                problems.append("page goes backwards: %s" % title[:40])
            last = max(last, int(page))
    return problems, count


def overfull(cfg):
    log = "typeset/%s/%s.log" % (cfg["slug"], cfg["slug"])
    if not os.path.exists(log):
        return 0, 0.0
    body = open(log, encoding="utf-8", errors="replace").read()
    boxes = [float(x) for x in
             re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide", body)]
    return len(boxes), (max(boxes) if boxes else 0.0)


def missing_pages(cfg):
    """Source pages whose opening words never reach the built book.

    This catches dropped pages, which an aggregate word ratio hides.
    """
    hyph = bk.Hyphenator()
    structure_pages = {e["page"] for e in cfg["structure"]}
    items, notes, _ = tb.page_lines(cfg)
    by_page = {}
    for it in items:
        by_page.setdefault(it["page"], []).append(it)
    got = pdf_words(cfg)
    joined = " " + " ".join(got) + " "
    missing = []
    for page, lines in sorted(by_page.items()):
        if page in structure_pages:
            continue                    # its printed heading is dropped by design
        tokens = tb.words(" ".join(l["text"] for l in lines[:3]))
        # a page whose first words continue a hyphenated word ("govern-" /
        # "mental arrangements") starts with a fragment: skip leading tokens
        # the dictionary does not know
        while tokens and not hyph.known(tokens[0]):
            tokens.pop(0)
        probe = tokens[:8]
        if not probe:
            continue
        if (" " + " ".join(probe) + " ") not in joined:
            missing.append((page, " ".join(probe)))
    return missing


def audit(key):
    cfg = dict(BOOKS[key])
    if callable(cfg["structure"]):
        cfg["structure"] = cfg["structure"]()
    items, notes, _ = tb.page_lines(cfg)
    src = tb.words(" ".join(it["text"] for it in items + notes))
    got = pdf_words(cfg)
    cover = len(got) / max(1, len(src))
    leaks = leaked_furniture(cfg)
    probs = structure_problems(cfg, cfg["structure"])
    toc, count = contents_problems(cfg)
    over, worst = overfull(cfg)
    missing = missing_pages(cfg)
    print("== %-22s source=%7d pdf=%7d cover=%5.1f%%  chapters=%2d  toc=%3d"
          % (key, len(src), len(got), 100 * cover, len(cfg["structure"]), count))
    for label, issues in (("leak", [h[1] for h in leaks]),
                          ("struct", ["p%s %s %s" % p for p in probs]),
                          ("toc", toc)):
        if issues:
            print("   %-7s %d: %s" % (label, len(issues),
                                      " | ".join(issues[:4])))
    if over:
        print("   overfull %d (max %.1fpt)" % (over, worst))
    if missing:
        print("   MISSING  %d pages: %s" % (
            len(missing), " | ".join("p%d %s" % m for m in missing[:6])))
    return {"cover": cover, "leaks": len(leaks), "struct": len(probs),
            "toc": len(toc), "over": over, "missing": len(missing)}


def main():
    keys = sys.argv[1:]
    if not keys or keys == ["--all"]:
        keys = [k for k in BOOKS
                if os.path.exists(out_pdf(BOOKS[k]).replace("configs", ""))
                and os.path.exists("typeset/%s/%s.pdf" % (BOOKS[k]["slug"],
                                                          BOOKS[k]["slug"]))]
    for key in keys:
        audit(key)


if __name__ == "__main__":
    main()
