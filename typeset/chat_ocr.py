"""OCR a DingTalk screenshot PDF into per-page JSON of chat lines.

Usage:  python3 typeset/chat_ocr.py <pdf> <cache-dir> [first last] [dpi]

These PDFs are full-desktop screenshots: the same window furniture -- icon
rail, conversation list, search bar, chat header, composer -- sits on every
page.  The chat pane is cut out before OCR, so only the messages are read.

The cut is measured, not guessed.  The conversation list and the chat pane
are divided by a vertical rule that runs the height of the window and never
varies, so the divider is the column whose pixels are most nearly a single
colour; the top of the scroll area is the header's lower edge and the bottom
is where the composer begins.  Both sit at the same y on every page.

Each line is stored with its box and with the colour behind it, which is what
later tells a blue bubble (sent) from a white one (received), a quoted block
from the message that quotes it, and a photo from either.
"""

import json
import os
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor

import fitz
import numpy as np
from PIL import Image

DPI = 150
TOP = 0.115          # below the chat header
BOTTOM = 0.730       # above the composer's tabs
RIGHT = 0.985
WORKERS = 6

_OCR = None


def engine():
    """One recogniser per worker process; loading it is the slow part."""
    global _OCR
    if _OCR is None:
        from rapidocr import RapidOCR
        # two threads each, so that a handful of workers fill the machine
        # instead of each of them trying to use all of it
        _OCR = RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": 2})
    return _OCR


def divider(img):
    """x of the conversation-list/chat-pane rule, in pixels of the page."""
    a = np.asarray(img.convert("RGB"), dtype=int)
    h, w, _ = a.shape
    band = a[int(0.15 * h):int(0.75 * h)]
    med = np.median(band, axis=0)
    straight = (np.abs(band - med[None, :, :]).max(axis=2) < 6).mean(axis=0)
    lo, hi = int(0.10 * w), int(0.35 * w)
    return lo + int(np.argmax(straight[lo:hi]))


def ground(a, box):
    """Colour behind a line, and how evenly that colour covers it.

    A message sits on a bubble: one flat colour, with only the glyphs cut out
    of it.  Text read off a picture -- a screenshot pasted into the chat, say
    -- has no such colour underneath, which is what the second number
    measures and what keeps those readings out of the transcript.
    """
    x0, y0, x1, y1 = [int(v) for v in (min(p[0] for p in box),
                                       min(p[1] for p in box),
                                       max(p[0] for p in box),
                                       max(p[1] for p in box))]
    pad = max(2, (y1 - y0) // 3)
    region = a[max(0, y0 - pad):y1 + pad, max(0, x0 - pad):x1 + pad]
    flat = region.reshape(-1, 3)
    ink = flat.sum(axis=1) > 400          # drop the glyphs themselves
    paper = flat[ink] if ink.any() else flat
    quant = (paper // 4 * 4)
    colour, hits = Counter(map(tuple, quant)).most_common(1)[0]
    return [int(v) for v in colour], round(hits / len(paper), 3)


def ocr_page(args):
    pdf, cache, pno, dpi = args
    out = os.path.join(cache, "%04d.json" % pno)
    if os.path.exists(out):
        return pno
    doc = fitz.open(pdf)
    pm = doc[pno].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pm.width, pm.height), pm.samples)
    w, h = img.size
    div = divider(img)
    box = (int(div + 6), int(TOP * h), int(RIGHT * w), int(BOTTOM * h))
    pane = img.crop(box)
    a = np.asarray(pane)                  # uint8: what the recogniser wants
    rgb = a.astype(int)                   # arithmetic on colours needs width

    res = engine()(a)
    boxes = res.boxes if res.boxes is not None else []
    texts = res.txts if res.txts is not None else []
    scores = res.scores if res.scores is not None else []
    lines = []
    for bbox, text, conf in zip(boxes, texts, scores):
        text = text.strip()
        if not text:
            continue
        bg, flat = ground(rgb, bbox)
        xs = [p[0] for p in bbox]
        ys = [p[1] for p in bbox]
        lines.append({"x0": round(float(min(xs)), 1),
                      "x1": round(float(max(xs)), 1),
                      "y0": round(float(min(ys)), 1),
                      "y1": round(float(max(ys)), 1),
                      "text": text, "conf": round(float(conf), 3),
                      "bg": bg, "flat": flat})
    lines.sort(key=lambda l: (l["y0"], l["x0"]))
    # write through a temp name: a page half-written by a crash must not look
    # like a page already done
    tmp = out + ".part"
    json.dump({"page": pno + 1, "size": [w, h], "divider": div,
               "box": list(box), "lines": lines}, open(tmp, "w"))
    os.replace(tmp, out)
    return pno


def main():
    pdf, cache = sys.argv[1], sys.argv[2]
    doc = fitz.open(pdf)
    first = int(sys.argv[3]) if len(sys.argv) > 3 else 1
    last = int(sys.argv[4]) if len(sys.argv) > 4 else doc.page_count
    dpi = int(sys.argv[5]) if len(sys.argv) > 5 else DPI
    os.makedirs(cache, exist_ok=True)
    jobs = [(pdf, cache, p, dpi) for p in range(first - 1, last)]
    done = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        for _ in ex.map(ocr_page, jobs, chunksize=2):
            done += 1
            if done % 20 == 0 or done == len(jobs):
                print("  ocr", done, "/", len(jobs), flush=True)
    print("ocr complete:", done, "pages ->", cache)


if __name__ == "__main__":
    main()
