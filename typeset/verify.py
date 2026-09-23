"""Check a typeset book against the text layer of its source PDF.

Usage:  python3 typeset/verify.py <book-key>

Compares word sequences, ignoring hyphens (so line-break hyphenation and
de-hyphenation cannot create false alarms) and printing every difference
with its context.
"""

import collections
import json
import re
import sys

import fitz

import driver

LIG = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi",
       "\ufb04": "ffl", "\u017f": "s"}


def normalise(text):
    for k, v in LIG.items():
        text = text.replace(k, v)
    return (text.replace("\u2019", "'").replace("\u2018", "'")
                .replace("\u201c", '"').replace("\u201d", '"')
                .replace("\u2014", "-").replace("\u2013", "-")
                .replace("\u2026", "..."))


def tokens(text):
    return [w for w in (t.replace("-", "").strip(".,;:\"'()[]") for t in text.split())
            if w]


def source_words(cfg):
    """Words of the source PDF, in printed order, with the furniture dropped."""
    doc = fitz.open(cfg["src"])
    lines = driver.to_items(doc, cfg)[0]
    out = []
    pending = None
    for it in lines:
        if "block" in it:
            out.append(it["block"]["text"])
            continue
        text = it["text"]
        if pending:
            text = pending + it["text"]
            pending = None
        if text.endswith("-") and not it["text"].endswith("--"):
            pending = text[:-1]
            continue
        out.append(text)
    return tokens(normalise(" ".join(out)))


def pdf_words(path, skip_to):
    doc = fitz.open(path)
    out, pending = [], None
    for page in doc:
        top, bottom = page.rect.height * 0.070, page.rect.height * 0.94
        lines = []
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in block["lines"]:
                text = "".join(s["text"] for s in line["spans"]).strip()
                if text and not (line["bbox"][1] < top or line["bbox"][3] > bottom):
                    lines.append((line["bbox"][1], text))
        lines.sort()
        merged = [pending] if pending else []
        pending = None
        for _, text in lines:
            if merged and merged[-1].endswith("-"):
                merged[-1] = merged[-1][:-1] + text
            else:
                merged.append(text)
        if merged and merged[-1].endswith("-"):
            pending = merged.pop()
        out.extend(merged)
    words = tokens(normalise(" ".join(out)))
    return words[words.index(skip_to):]


def main():
    cfg = driver.BOOKS[sys.argv[1]]
    out_pdf = "typeset/%s/%s.pdf" % (cfg["slug"], cfg["slug"])
    src = source_words(cfg)
    if sys.argv[1] == "on-liberty":
        src = src[src.index("subject"):]          # drop the front matter
    pdf = pdf_words(out_pdf, "subject")
    print("source words:", len(src), "pdf words:", len(pdf))

    i = j = 0
    reports = []
    while i < len(src) and j < len(pdf):
        if src[i] == pdf[j]:
            i += 1
            j += 1
            continue
        for k in range(1, 25):
            if i + k < len(src) and src[i + k] == pdf[j]:
                reports.append(("source-only", " ".join(src[i:i + k]),
                                " ".join(src[max(0, i - 8):i + k + 8])))
                i += k
                break
            if j + k < len(pdf) and pdf[j + k] == src[i]:
                reports.append(("pdf-only", " ".join(pdf[j:j + k]),
                                " ".join(pdf[max(0, j - 8):j + k + 8])))
                j += k
                break
        else:
            reports.append(("mismatch", "%s -> %s" % (src[i], pdf[j]),
                            " ".join(src[max(0, i - 8):i + 8])))
            i += 1
            j += 1
    for kind, what, ctx in reports[: int(sys.argv[2]) if len(sys.argv) > 2 else 40]:
        print("[%s] %s\n      ...%s" % (kind, what, ctx))
    print("differences:", len(reports))


if __name__ == "__main__":
    main()
