"""Merge the scan's own text layer with the Latin re-OCR.

Usage:  python3 typeset/lingua_merge.py [first last]     (cache -> json)

Both readings of the book are incomplete in different places.  The text
layer that came with the PDF keeps the printed macrons (``Rōma``) but
stumbles over the italic type, where it reads ``u`` as ``w``, ``l`` as
``k`` and drops or invents letters.  Tesseract's Latin model reads the
italics well but renders long vowels as accents and sometimes loses them.

So the two are aligned word by word, line by line.  Where they agree once
macrons, accents and case are set aside, the scan's spelling wins and the
macrons come through.  Where they disagree, the word that the book's own
vocabulary knows is the one kept.  The result is cached with the scan's
geometry, so the rest of the pipeline cannot tell the difference.
"""

import difflib
import json
import os
import re
import sys
import unicodedata

ROOT = os.path.dirname(os.path.abspath(__file__))
LINES = os.path.join(ROOT, "build/lingua/lines.json")
OCR = os.path.join(ROOT, "build/lingua/ocr")
OUT = os.path.join(ROOT, "build/lingua/merged.json")
VERBA = os.path.join(ROOT, "build/lingua/verba.txt")
COMMON = os.path.join(ROOT, "build/lingua/latin-core.json")

MACRA = dict(zip("āēīōūȳ", "aeiouy"))
ACUTES = dict(zip("áàéèíìóòúùý", "aaeeiioouuy"))
ODDS = re.compile(r"[wWkK\\|\[\]{}<>*^_~]")


def strip_diacritics(text):
    text = "".join(ACUTES.get(c, MACRA.get(c, c)) for c in text)
    return "".join(c for c in unicodedata.normalize("NFD", text)
                   if not unicodedata.combining(c))


def norm(word):
    """Comparison form: letters only, no macrons, no u/v distinction."""
    word = strip_diacritics(word).lower()
    word = "".join(c for c in word if c.isalpha())
    return word.replace("v", "u")


def split_tokens(text):
    return [t for t in text.split() if t]


class Lexicon:
    """The words the book is allowed to use: Latin lists plus its own text.

    ``verba.txt`` is the Latin word list of the ``verba`` project (134k
    inflected forms, one per line); ``latin-core.json`` is the thousand
    commonest Latin words, from the Dickenson College Commentaries core
    vocabulary.  Both are fetched by ``lingua_words.sh``.
    """

    def __init__(self, extra, common):
        self.words = set()
        try:
            with open(VERBA) as fh:
                self.words.update(norm(w) for w in fh)
        except OSError:
            pass
        try:
            data = json.load(open(COMMON, encoding="utf-8"))
            for row in data["table"]["content"]:
                for piece in re.split(r"[/,\s]+", row[0]):
                    self.words.add(norm(piece))
        except Exception:
            pass
        self.words.update(extra)
        self.common = common
        self.words.discard("")

    def knows(self, word):
        return norm(word) in self.words

    def frequent(self, word):
        return self.common.get(norm(word), 0)

    def good(self, word):
        """Crude quality score for one reading of a word."""
        n = norm(word)
        if not n:
            return 0.0                    # punctuation: neither good nor bad
        score = 0.0
        if n in self.words:
            score += 2.0
        score += min(2.0, self.frequent(word) ** 0.5 / 3.0)
        if ODDS.search(word):
            score -= 1.5
        return score


def merge_column(embedded, ocr, lex):
    """Align the two readings of a column and keep the better word each time.

    The alignment is made over the whole column, not line by line, because
    the two readings disagree about where lines end: Tesseract will run a
    three-cell paradigm row into one line where the scan's layer keeps
    three.  Every token therefore remembers which printed line it came
    from, and the printed lines are rebuilt afterwards.
    """
    emb_tokens, emb_line = [], []
    for i, l in enumerate(embedded):
        for t in split_tokens(l["text"]):
            emb_tokens.append(t)
            emb_line.append(i)
    ocr_tokens = [t for l in ocr for t in split_tokens(l["text"])]
    if not emb_tokens:
        return [l for l in embedded if l["text"].strip()]
    if not ocr_tokens:
        return list(embedded)

    matcher = difflib.SequenceMatcher(a=[norm(t) for t in emb_tokens],
                                      b=[norm(t) for t in ocr_tokens],
                                      autojunk=False)
    kept = []                              # (printed line, word)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        e = emb_tokens[i1:i2]
        o = ocr_tokens[j1:j2]
        if tag == "equal":
            for k in range(i1, i2):
                kept.append((emb_line[k], emb_tokens[k]))
            continue
        if not e:
            line = emb_line[i1 - 1] if i1 else (emb_line[0] if emb_line else 0)
            kept.extend((line, w) for w in o)
            continue
        if not o:
            kept.extend((emb_line[k], emb_tokens[k]) for k in range(i1, i2))
            continue
        se = sum(lex.good(w) for w in e) / len(e)
        so = sum(lex.good(w) for w in o) / len(o)
        if so > se + 0.35:
            keep = o
            lines = [emb_line[k] for k in range(i1, i2)] or [emb_line[i1]]
            kept.extend(zip(_spread(lines, len(o)), o))
        elif se > so + 0.35 or not ODDS.search(" ".join(o)) \
                or ODDS.search(" ".join(e)):
            kept.extend((emb_line[k], emb_tokens[k]) for k in range(i1, i2))
        else:
            lines = [emb_line[k] for k in range(i1, i2)] or [emb_line[i1]]
            kept.extend(zip(_spread(lines, len(o)), o))

    words = {i: [] for i in range(len(embedded))}
    for line, word in kept:
        words[max(0, min(line, len(embedded) - 1))].append(word)
    out = []
    for i, l in enumerate(embedded):
        text = " ".join(words[i])
        if text.strip():
            # the scan writes a soft hyphen where the printed line breaks a
            # word; keep it so the paragraph builder can close the word up
            if l["text"].rstrip().endswith("\u00ad") and not text.endswith(("-", "\u00ad")):
                text += "\u00ad"
            out.append({**l, "text": text})
    return out


def _spread(keys, n):
    """Distribute n words over the given printed lines."""
    if n <= len(keys):
        return keys[:n]
    out = list(keys)
    while len(out) < n:
        out.append(keys[-1] if keys else 0)
    return out


def main():
    lines = json.load(open(LINES, encoding="utf-8"))
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    last = int(sys.argv[2]) if len(sys.argv) > 2 else len(lines) - 1

    # Every token either reading offers, with how often it occurs.  The book
    # repeats its vocabulary heavily, so frequency is a good referee: the
    # lexicon needs the book's own words before the lines can be merged.
    counts = {}
    for pno in range(first, last + 1):
        emb, ocr = _embedded(lines[str(pno)], pno), _ocr(pno)
        for col in ("body", "margin"):
            _count(counts, emb[col] + ocr[col])
    lex = Lexicon(set(k for k, v in counts.items() if v >= 6), counts)

    merged = {}
    for pno in range(first, last + 1):
        emb = _embedded(lines[str(pno)], pno)
        ocr = _ocr(pno)
        body = merge_column(emb["body"], ocr["body"], lex)
        margin = merge_column(emb["margin"], ocr["margin"], lex)
        merged[str(pno)] = {"body": body, "margin": margin}
    json.dump(merged, open(OUT, "w"), ensure_ascii=False)
    print("merged %d pages -> %s" % (len(merged), OUT))


def _count(counts, lines):
    for l in lines:
        for t in split_tokens(l["text"]):
            n = norm(t)
            if n:
                counts[n] = counts.get(n, 0) + 1


def _embedded(page_lines, pno):
    """The scan's text layer, split into the two columns."""
    body, margin = [], []
    for l in page_lines:
        if l["y0"] < 46 or l["y0"] > 568:
            continue
        if _is_margin(pno, l):
            margin.append(l)
        else:
            body.append(l)
    return {"body": sorted(body, key=lambda l: l["y0"]),
            "margin": sorted(margin, key=lambda l: l["y0"])}


def _is_margin(pno, l):
    return l["x0"] >= 316 if pno % 2 == 0 else l["x1"] <= 136


def _ocr(pno):
    path = os.path.join(OCR, "%04d.json" % pno)
    try:
        return json.load(open(path, encoding="utf-8"))
    except OSError:
        return {"body": [], "margin": []}


if __name__ == "__main__":
    main()
