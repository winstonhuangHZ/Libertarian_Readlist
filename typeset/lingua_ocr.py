"""Re-OCR the Lingua Latina scan, column by column, into a page cache.

Usage:  python3 typeset/lingua_ocr.py [first last] [dpi]

The supplied PDF carries the text layer produced by the scanning software.
It keeps the macrons of the printed book (which matters in a Latin reader)
but misreads badly wherever the book sets Latin in italic type: in the
grammar summaries, in the exercise paradigms and in the marginal glosses.

So every page is read again with Tesseract's Latin model, with the body
column and the marginal column clipped apart first (the printed page has
the running text inboard and the vocabulary/grammar notes in the outer
margin).  The two readings are merged later, page by page: ``lingua.py``
keeps the scan's macrons and takes Tesseract's word wherever the scan's
own text layer is the one that stumbled.

The Latin model is not part of the system tessdata; fetch it once with

    curl -o "$TESSDATA/lat.traineddata" \
      https://github.com/tesseract-ocr/tessdata_best/raw/main/lat.traineddata

and point TESSDATA_PREFIX at that directory when running this script.
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import fitz

ROOT = os.path.dirname(os.path.abspath(__file__))
PDF = os.path.join(ROOT, "Lingua Latina per se Illustrata.pdf")
CACHE = os.path.join(ROOT, "build/lingua/ocr")
DPI = 400


def clips(pno):
    """(body, margin) rectangles for one page, in points.

    The scan alternates: on a recto the running text sits at the inner
    (left) edge and the glosses in the outer right margin; on a verso the
    glosses are in the outer left margin.  Page 6 of the PDF is the first
    recto of the body.
    """
    if pno % 2 == 0:                       # recto
        return fitz.Rect(32, 46, 318, 568), fitz.Rect(318, 46, 452, 568)
    return fitz.Rect(136, 46, 440, 568), fitz.Rect(30, 46, 140, 568)


def ocr_rect(page, rect, png, lang="lat"):
    page.get_pixmap(dpi=DPI, clip=rect).save(png)
    tsv = subprocess.run(
        ["tesseract", png, "stdout", "--psm", "6", "-l", lang, "tsv"],
        capture_output=True, text=True).stdout
    os.remove(png)
    lines = {}
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5" or not f[11].strip():
            continue
        key = (f[1], f[2], f[3], f[4])
        left, top, w, h = int(f[6]), int(f[7]), int(f[8]), int(f[9])
        e = lines.setdefault(key, {"words": [], "x0": left, "y0": top,
                                   "x1": left + w, "y1": top + h})
        e["words"].append(f[11])
        e["x0"] = min(e["x0"], left)
        e["y0"] = min(e["y0"], top)
        e["x1"] = max(e["x1"], left + w)
        e["y1"] = max(e["y1"], top + h)
    scale = 72.0 / DPI
    out = []
    for key in sorted(lines, key=lambda k: (int(k[1]), int(k[2]))):
        e = lines[key]
        out.append({"y0": round(rect.y0 + e["y0"] * scale, 1),
                    "x0": round(rect.x0 + e["x0"] * scale, 1),
                    "x1": round(rect.x0 + e["x1"] * scale, 1),
                    "text": " ".join(e["words"])})
    return out


def ocr_page(pno):
    out = os.path.join(CACHE, "%04d.json" % pno)
    if os.path.exists(out):
        return pno
    doc = fitz.open(PDF)
    page = doc[pno]
    body_rect, margin_rect = clips(pno)
    png = os.path.join(CACHE, "_%04d.png" % pno)
    data = {"body": ocr_rect(page, body_rect, png),
            "margin": ocr_rect(page, margin_rect, png)}
    json.dump(data, open(out, "w"), ensure_ascii=False)
    return pno


def main():
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 327
    os.makedirs(CACHE, exist_ok=True)
    jobs = list(range(first, last + 1))
    done = 0
    with ProcessPoolExecutor(max_workers=6) as ex:
        for _ in ex.map(ocr_page, jobs, chunksize=1):
            done += 1
            if done % 10 == 0:
                print("  ocr", done, "/", len(jobs), flush=True)
    print("ocr complete:", done, "pages ->", CACHE)


if __name__ == "__main__":
    main()
