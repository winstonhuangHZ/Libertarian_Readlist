"""OCR a scanned PDF into lines with coordinates, cached page by page.

Usage:  python3 typeset/ocr.py <pdf> <cache-dir> [first last] [dpi]

Runs tesseract in parallel over the page images. Each page becomes a JSON
file of lines: {"y0","y1","x0","x1","text","h"} where "h" is the line height
in pixels (a stand-in for the printed type size).
"""

import json
import os
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import fitz


def ocr_page(args):
    pdf, cache, pno, dpi = args
    out = os.path.join(cache, "%04d.json" % pno)
    if os.path.exists(out):
        return pno
    doc = fitz.open(pdf)
    page = doc[pno]
    png = os.path.join(cache, "%04d.png" % pno)
    page.get_pixmap(dpi=dpi).save(png)
    tsv = subprocess.run(
        ["tesseract", png, "stdout", "--psm", "6", "tsv"],
        capture_output=True, text=True).stdout
    os.remove(png)

    lines = {}
    for row in tsv.splitlines()[1:]:
        f = row.split("\t")
        if len(f) < 12 or f[0] != "5":
            continue
        key = (f[1], f[2], f[3], f[4])
        left, top, w, h = int(f[6]), int(f[7]), int(f[8]), int(f[9])
        lines.setdefault(key, {"words": [], "x0": left, "y0": top,
                               "x1": left + w, "y1": top + h})
        e = lines[key]
        e["words"].append(f[11])
        e["x0"] = min(e["x0"], left)
        e["y0"] = min(e["y0"], top)
        e["x1"] = max(e["x1"], left + w)
        e["y1"] = max(e["y1"], top + h)

    out_lines = []
    for key in sorted(lines, key=lambda k: (int(k[1]), int(k[2]), int(k[3]))):
        e = lines[key]
        out_lines.append({"x0": e["x0"], "y0": e["y0"], "x1": e["x1"],
                          "y1": e["y1"], "h": e["y1"] - e["y0"],
                          "text": " ".join(e["words"])})
    json.dump(out_lines, open(out, "w"))
    return pno


def main():
    pdf, cache = sys.argv[1], sys.argv[2]
    doc = fitz.open(pdf)
    first = int(sys.argv[3]) if len(sys.argv) > 3 else 0
    last = int(sys.argv[4]) if len(sys.argv) > 4 else doc.page_count - 1
    dpi = int(sys.argv[5]) if len(sys.argv) > 5 else 300
    os.makedirs(cache, exist_ok=True)
    jobs = [(pdf, cache, p, dpi) for p in range(first, last + 1)]
    done = 0
    with ProcessPoolExecutor(max_workers=6) as ex:
        for _ in ex.map(ocr_page, jobs, chunksize=4):
            done += 1
            if done % 25 == 0:
                print("  ocr", done, "/", len(jobs), flush=True)
    print("ocr complete:", done, "pages ->", cache)


if __name__ == "__main__":
    main()
