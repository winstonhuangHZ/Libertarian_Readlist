"""Per-book tables for typebooks.py.

Most structure tables are *derived* from the printed pages themselves: each
book marks its chapter openings in its own way (a large chapter number, an
all-capitals title line, a running head that changes), and the functions
below read those marks out of the PDF's text layer.  Where a book has no such
mark the table is written out by hand from its printed contents.

Every entry is  {page, y0, kind, label, title, drop}  where ``drop`` lists the
y of the printed heading lines that the book's own running text replaces.
"""

import re

import fitz

import bookkit as bk


def _lines(pdf, first=0, last=None, top=60, bottom=10 ** 6):
    doc = fitz.open(pdf)
    lines = bk.extract_lines(doc, first, last if last is not None
                             else doc.page_count - 1)
    pages = {}
    for line in lines:
        if line["y0"] < top or line["y0"] > bottom:
            continue
        pages.setdefault(line["page"], []).append(line)
    for page in pages:
        pages[page].sort(key=lambda l: (l["y0"], l["x0"]))
    return pages


def _entry(page, y0, kind, label, title, *drop):
    return {"page": page, "y0": y0, "kind": kind, "label": label,
            "title": title, "drop": list(drop)}


def _titlecase(text):
    """Title-case a heading without touching all-capitals acronyms."""
    out = []
    for i, word in enumerate(text.split()):
        core = word.strip(".,;:")
        if i and core.lower() in bk.SMALL_WORDS:
            out.append(word.lower())
        elif core.isalpha() and not core.isupper():
            out.append(word[0].upper() + word[1:])
        else:
            out.append(bk.smart_title(word))
    return " ".join(out)


# ------------------------------------------------------------------ Atlas ----

ATLAS_PARTS = {"I": "Part I. Non-Contradiction",
               "II": "Part II. Either-Or",
               "III": "Part III. A Is A"}


def atlas_structure():
    """`PART I` / `CHAPTER III` lines, with the title on the next line."""
    pages = _lines("Atlas_Shrugged.pdf", top=60, bottom=780, last=1587)
    out = []
    for p in sorted(pages):
        ls = pages[p]
        for i, line in enumerate(ls):
            text = line["text"].strip()
            m = re.match(r"^(PART|CHAPTER) ([IVXL]+)$", text)
            if not m or i + 1 >= len(ls):
                continue
            kind = "part" if m.group(1) == "PART" else "chapter"
            nxt = ls[i + 1]
            if kind == "part":
                out.append(_entry(p, line["y0"], "part", "",
                                  ATLAS_PARTS.get(m.group(2), nxt["text"].title()),
                                  line["y0"], nxt["y0"]))
            else:
                num = m.group(2)
                out.append(_entry(p, line["y0"], "chapter", "Chapter " + num,
                                  bk.smart_title(nxt["text"].strip()),
                                  line["y0"], nxt["y0"]))
    return out


# --------------------------------------------------------- Road to Serfdom ----

ROAD_CHAPTERS = [
    "The Abandoned Road", "The Great Utopia", "Individualism and Collectivism",
    'The "Inevitability" of Planning', "Planning and Democracy",
    "Planning and the Rule of Law", "Economic Control and Totalitarianism",
    "Who, Whom?", "Security and Freedom", "Why the Worst Get on Top",
    "The End of Truth", "The Socialist Roots of Nazism",
    "The Totalitarians in our Midst", "Material Conditions and Ideal Ends",
    "The Prospects of International Order",
]


def road_structure():
    """Chapter openers carry the chapter number in 35-point type."""
    pages = _lines("the_road_to_serfdom.pdf", top=40, bottom=520)
    big = []
    for p in sorted(pages):
        for line in pages[p]:
            t = line["text"].strip()
            if line["size"] > 30 and t.isdigit() and 100 < line["y0"] < 200:
                big.append((p, line["y0"]))
    big = big[:len(ROAD_CHAPTERS)]
    out = [_entry(5, 0, "chapter", "", "Preface"),
           _entry(8, 0, "chapter", "", "Introduction")]
    for (p, y), title in zip(big, ROAD_CHAPTERS):
        n = ROAD_CHAPTERS.index(title) + 1
        title_lines = []
        for line in pages[p]:
            if line["y0"] > y and line["size"] > 13:
                title_lines.append(line["text"].strip())
        drop = [y] + [line["y0"] for line in pages[p]
                      if line["y0"] > y and line["size"] > 13]
        out.append(_entry(p, title_lines and
                          min(line["y0"] for line in pages[p]
                              if line["y0"] > y and line["size"] > 13) or 0,
                          "chapter", "Chapter %d" % n, title, *drop))
    out.append(_entry(252, 0, "chapter", "", "Conclusion"))
    out.append(_entry(254, 0, "backmatter", "", "Bibliographical Note"))
    return out


# ------------------------------------------------------------ Human Action ----

def human_action_structure():
    """Chapters print "IV. TITLE" at 12pt, parts "Part Two" at 14pt.

    A chapter title that runs to a second line is printed in capitals too, so
    the continuation lines are gathered with it.
    """
    pages = _lines("Human_Action.pdf", top=60, bottom=580, last=906)
    out = [_entry(4, 0, "chapter", "", "Foreword to Fourth Edition"),
           _entry(6, 0, "chapter", "", "Foreword to the Third Edition"),
           _entry(20, 0, "chapter", "", "Introduction")]
    roman = re.compile(r"^([IVXL]+)\.\s+(.+)$")
    for p in sorted(pages):
        ls = pages[p]
        for i, line in enumerate(ls):
            t = line["text"].strip()
            if (line["size"] >= 12.5
                    and re.match(r"^Part (One|Two|Three|Four|Five|Six|Seven)$", t)):
                sub = ls[i + 1]["text"].strip() if i + 1 < len(ls) else ""
                title = "%s. %s" % (t, _titlecase(sub))
                drop = [line["y0"]] + ([ls[i + 1]["y0"]] if sub else [])
                out.append(_entry(p, line["y0"], "part", "", title.strip(), *drop))
            elif 11.5 <= line["size"] <= 12.5 and line["y0"] > 100:
                m = roman.match(t)
                if not m:
                    continue
                words = [m.group(2)]
                j = i + 1
                while (j < len(ls) and 11.5 <= ls[j]["size"] <= 12.5
                       and ls[j]["text"].strip().isupper()):
                    words.append(ls[j]["text"].strip())
                    j += 1
                drop = [line["y0"]] + [ls[k]["y0"] for k in range(i + 1, j)]
                out.append(_entry(p, line["y0"], "chapter",
                                  "Chapter %s" % m.group(1),
                                  bk.smart_title(" ".join(words)), *drop))
    return out


# -------------------------------------------------- Constitution of Liberty ----

CONSTITUTION_FRONT = [
    (11, "Editorial Foreword"), (13, "Introductory Essay"),
    (35, "The Constitution of Liberty: Editions and Translations"),
    (38, "A Note on the Notes"), (40, "Editor's Acknowledgments"),
    (42, "Liberty Fund Editions Cited"), (51, "Preface"),
    (53, "Acknowledgments"), (56, "Bibliographical Abbreviations"),
    (59, "Introduction"),
]

CONSTITUTION_PARTS = [
    (49, "The Constitution of Liberty"),
    (67, "Part I. The Value of Freedom"),
    (209, "Part II. Freedom and the Law"),
    (379, "Part III. Freedom in the Welfare State"),
]

CONSTITUTION_CHAPTERS = [
    (69, "Chapter 1", "Liberty and Liberties"),
    (85, "Chapter 2", "The Creative Powers of a Free Civilization"),
    (103, "Chapter 3", "The Common Sense of Progress"),
    (119, "Chapter 4", "Freedom, Reason, and Tradition"),
    (145, "Chapter 5", "Responsibility and Freedom"),
    (160, "Chapter 6", "Equality, Value, and Merit"),
    (178, "Chapter 7", "Majority Rule"),
    (196, "Chapter 8", "Employment and Independence"),
    (211, "Chapter 9", "Coercion and the State"),
    (227, "Chapter 10", "Law, Commands, and Order"),
    (244, "Chapter 11", "The Origins of the Rule of Law"),
    (273, "Chapter 12", "The American Contribution: Constitutionalism"),
    (299, "Chapter 13", "Liberalism and Administration: The Rechtsstaat"),
    (320, "Chapter 14", "The Safeguards of Individual Liberty"),
    (341, "Chapter 15", "Economic Policy and the Rule of Law"),
    (354, "Chapter 16", "The Decline of the Law"),
    (381, "Chapter 17", "The Decline of Socialism and the Rise of the Welfare State"),
    (396, "Chapter 18", "Labor Unions and Employment"),
    (417, "Chapter 19", "Social Security"),
    (442, "Chapter 20", "Taxation and Redistribution"),
    (463, "Chapter 21", "The Monetary Framework"),
    (478, "Chapter 22", "Housing and Town Planning"),
    (494, "Chapter 23", "Agriculture and Natural Resources"),
    (510, "Chapter 24", "Education and Research"),
    (529, "Chapter 25", "Why I Am Not a Conservative"),
]


def constitution_structure():
    """Top-level headings are the only 14pt lines in the book."""
    pages = _lines("The_Constitution_of_Liberty.pdf", top=60, bottom=580)
    out = []
    for p, title in CONSTITUTION_FRONT:
        out.append(_entry(p, 0, "chapter", "", title))
    for p, title in CONSTITUTION_PARTS:
        out.append(_entry(p, 0, "part", "", title))
    for p, label, title in CONSTITUTION_CHAPTERS:
        drop = [l["y0"] for l in pages.get(p, []) if 13.5 <= l["size"] <= 14.5]
        out.append(_entry(p, 0, "chapter", label, title, *drop))
    out.append(_entry(547, 0, "backmatter", "", "Analytical Table of Contents"))
    return sorted(out, key=lambda e: e["page"])


# ----------------------------------------------------- Capitalism & Freedom ----

CAPITALISM_CHAPTERS = [
    (19, "Chapter I", "The Relation between Economic Freedom and Political Freedom"),
    (34, "Chapter II", "The Role of Government in a Free Society"),
    (49, "Chapter III", "The Control of Money"),
    (68, "Chapter IV", "International Financial and Trade Arrangements"),
    (87, "Chapter V", "Fiscal Policy"),
    (97, "Chapter VI", "The Role of Government in Education"),
    (120, "Chapter VII", "Capitalism and Discrimination"),
    (131, "Chapter VIII", "Monopoly and the Social Responsibility of Business and Labor"),
    (149, "Chapter IX", "Occupational Licensure"),
    (173, "Chapter X", "The Distribution of Income"),
    (189, "Chapter XI", "Social Welfare Measures"),
    (202, "Chapter XII", "The Alleviation of Poverty"),
    (208, "Chapter XIII", "Conclusion"),
]


def capitalism_structure():
    pages = _lines("Capitalism_and_freedom.pdf", top=42, bottom=520)
    out = [_entry(3, 0, "chapter", "", "Preface, 2002"),
           _entry(7, 0, "chapter", "", "Preface, 1982"),
           _entry(12, 0, "chapter", "", "Preface"),
           _entry(17, 0, "chapter", "", "Introduction")]
    for p, label, title in CAPITALISM_CHAPTERS:
        # The heading block is the chapter number, the "+" rule and the title,
        # all set close together; the chapter's opening words follow in large
        # type after a blank gap and are part of the text, not part of the
        # heading.
        lines = pages.get(p, [])
        drop = []
        for i, line in enumerate(lines):
            if not re.match(r"^Chapter\s*[IVXL]+\s*$", line["text"].strip()):
                continue
            drop.append(line["y0"])
            prev, j = line, i + 1
            while (j < len(lines) and lines[j]["size"] >= 11.5
                   and lines[j]["y0"] - prev["y0"] < 60):
                drop.append(lines[j]["y0"])
                prev, j = lines[j], j + 1
            break
        out.append(_entry(p, 0, "chapter", label, title, *drop))
    return out


# ------------------------------------------------- Anarchy, State, and Utopia ----

ANARCHY_STRUCTURE = [
    (7, 0, "chapter", "", "Preface"),
    (13, 0, "chapter", "", "Acknowledgments"),
    (15, 0, "part", "", "Part I. State-of-Nature Theory"),
    (17, 0, "chapter", "Chapter 1", "Why State-of-Nature Theory?"),
    (24, 0, "chapter", "Chapter 2", "The State of Nature"),
    (40, 0, "chapter", "Chapter 3", "Moral Constraints and the State"),
    (68, 0, "chapter", "Chapter 4", "Prohibition, Compensation, and Risk"),
    (102, 0, "chapter", "Chapter 5", "The State"),
    (134, 0, "chapter", "Chapter 6", "Further Considerations on the Argument"),
    (161, 0, "part", "", "Part II. Beyond the Minimal State?"),
    (163, 0, "chapter", "Chapter 7", "Distributive Justice"),
    (246, 0, "chapter", "Chapter 8", "Equality, Envy, Exploitation, Etc."),
    (290, 0, "chapter", "Chapter 9", "Demoktesis"),
    (309, 0, "part", "", "Part III. Utopia"),
    (311, 0, "chapter", "Chapter 10", "A Framework for Utopia"),
    (349, 0, "backmatter", "", "Notes"),
    (369, 0, "backmatter", "", "Bibliography"),
]


def anarchy_structure():
    """Chapter openers set the title at ~24pt and the chapter's opening words
    at ~37pt.  The title is the heading; the sentence is text, not furniture.
    """
    pages = _lines("Anarchy_State_and_Utopia.pdf", top=26, bottom=585)
    out = []
    for p, y, kind, label, title in ANARCHY_STRUCTURE:
        drop = []
        if kind in ("chapter", "part"):
            for line in pages.get(p, []):
                t = line["text"].strip()
                if re.fullmatch(r"(CHAPTER|PART)", t) or re.fullmatch(
                        r"[IVXL]+|\d{1,2}", t):
                    drop.append(line["y0"])
                elif 20 <= line["size"] <= 32 and len(t) < 70 and not t.endswith("."):
                    drop.append(line["y0"])
        out.append(_entry(p, 0, kind, label, title, *drop))
    return out


# ------------------------------------------------------------ Fahrenheit 451 ----

FAHRENHEIT_STRUCTURE = [
    (1, "part", "Part I. It Was a Pleasure to Burn"),
    (70, "part", "Part II. The Sieve and the Sand"),
    (112, "part", "Part III. Burning Bright"),
]


BOOKS = {
    "fahrenheit": {
        "src": "Fahrenheit_451.pdf",
        "slug": "Fahrenheit_451",
        "title": "Fahrenheit 451",
        "author": "Ray Bradbury",
        "year": "1953",
        "first_page": 1, "last_page": 161,
        "top": 60, "bottom": 715,
        "body_size": (14.0, 15.5),
        "indent": 999,                      # paragraphs are marked by a blank line
        "drop_re": [
            r"^RAY BRADBURY$", r"^FAHRENHEIT 451:?$",
            r"^This one, with gratitude",
            r"^The temperature at which book-paper",
            r"^PART (I|II|III)$", r"^IT WAS A PLEASURE TO BURN$",
            r"^THE SIEVE AND THE SAND$", r"^BURNING BRIGHT$",
        ],
        "structure": lambda: [_entry(p, 0, kind, "", title)
                              for p, kind, title in FAHRENHEIT_STRUCTURE],
        "source_note": (
            "The text is Ray Bradbury's \\emph{Fahrenheit 451}, from the "
            "electronic text supplied with this collection. The novel is in "
            "copyright: this edition is a private re-typesetting for the "
            "owner's own reading, not for distribution. Part divisions "
            "follow the printed book; the text was taken from the supplied "
            "file and is not otherwise altered."
        ),
    },
    "atlas": {
        "src": "Atlas_Shrugged.pdf",
        "slug": "Atlas_Shrugged",
        "title": "Atlas Shrugged",
        "author": "Ayn Rand",
        "year": "1957",
        "first_page": 4, "last_page": 1586,
        "top": 60, "bottom": 780,
        "body_size": (14.0, 14.9),
        "indent": 9.0,
        "drop_re": [r"^THE END$", r"^ABOUT THE AUTHOR$"],
        "structure": atlas_structure,
        "source_note": (
            "The text is Ayn Rand's \\emph{Atlas Shrugged}, from the "
            "electronic text supplied with this collection. The novel is in "
            "copyright: this edition is a private re-typesetting for the "
            "owner's own reading, not for distribution. The three parts and "
            "thirty chapters are as printed."
        ),
    },
    "road": {
        "src": "the_road_to_serfdom.pdf",
        "slug": "The_Road_to_Serfdom",
        "title": "The Road to Serfdom",
        "author": "F. A. Hayek",
        "year": "1944",
        "first_page": 5, "last_page": 256,
        "top": 40, "bottom": 500,
        "body_size": (8.0, 11.2),
        "indent": 11.0,
        "drop_re": [r"^THE ROAD TO SERFDOM\s*\d*$", r"^\d{1,3}$"],
        "structure": road_structure,
        "source_note": (
            "The text is F. A. Hayek's \\emph{The Road to Serfdom} as "
            "reprinted in the Routledge Classics edition (2001). The work is "
            "in copyright: this edition is a private re-typesetting of the "
            "supplied PDF for the owner's own reading, not for distribution. "
            "The numbered chapters, the conclusion and the bibliographical "
            "note are as printed."
        ),
    },
    "human-action": {
        "src": "Human_Action.pdf",
        "slug": "Human_Action",
        "title": "Human Action",
        "author": "Ludwig von Mises",
        "year": "1949",
        "first_page": 4, "last_page": 904,
        "top": 60, "bottom": 580,
        "body_size": (9.8, 10.6),
        "note_size": (9.2, 9.7),
        "section_size": (11.5, 12.5),
        "indent": 9.0,
        "drop_re": [r"^HUMAN ACTION\s*\d*$", r"^\d{1,4}$"],
        "structure": human_action_structure,
        "source_note": (
            "The text is Ludwig von Mises's \\emph{Human Action} (1949), from "
            "the supplied PDF. The book is in copyright: this edition is a "
            "private re-typesetting for the owner's own reading, not for "
            "distribution. The footnotes are kept, gathered at the end of "
            "each chapter, and the numbered sections are set as they are "
            "printed."
        ),
    },
    "constitution": {
        "src": "The_Constitution_of_Liberty.pdf",
        "slug": "The_Constitution_of_Liberty",
        "title": "The Constitution of Liberty",
        "author": "F. A. Hayek",
        "subtitle": "The Definitive Edition",
        "year": "1960",
        "first_page": 11, "last_page": 533,
        "top": 50, "bottom": 580,
        "body_size": (9.4, 11.0),
        "note_size": (6.6, 8.7),
        "indent": 7.0,
        "drop_re": [
            r"^THE CONSTITUTION OF LIBERTY$",
            r"^\d{1,3}$",
            # the PDF's own text layer puts a space after fi/fl ligatures
            r"^\s*$",
        ],
        "ligature_fix": True,
        "fixes": [
            (r"(\b[A-Za-z]{2,})\s-([a-z])", r"\1-\2"),   # "eighteenth- century"
            (r"\b(\w+)- (\w+)", r"\1-\2"),
        ],
        "structure": constitution_structure,
        "source_note": (
            "The text is F. A. Hayek's \\emph{The Constitution of Liberty} in "
            "the Definitive Edition of the Collected Works (University of "
            "Chicago Press, 2011), from the supplied PDF. The work is in "
            "copyright: this edition is a private re-typesetting for the "
            "owner's own reading, not for distribution. The editorial matter "
            "of that edition (foreword, introductory essay, note on the "
            "notes) is kept; the printed footnotes are gathered at the end of "
            "each chapter."
        ),
    },
    "capitalism": {
        "src": "Capitalism_and_freedom.pdf",
        "slug": "Capitalism_and_Freedom",
        "title": "Capitalism and Freedom",
        "author": "Milton Friedman",
        "year": "1962",
        "first_page": 3, "last_page": 208,
        "top": 42, "bottom": 510,
        "body_size": (10.2, 35.0),
        "note_size": (7.0, 8.4),
        "section_size": (7.0, 8.4),
        "indent": 7.0,
        "drop_re": [
            r"^CAPITALISM AND FREEDOM$", r"^CAP\s*I\s*TAL\s*ISM\s*AND$",
            r"^F\s*R\s*E\s*E\s*D\s*0\s*M$", r"^\d{1,3}$", r"^\+$",
        ],
        "structure": capitalism_structure,
        "source_note": (
            "The text is Milton Friedman's \\emph{Capitalism and Freedom} in "
            "the fortieth-anniversary edition (University of Chicago Press, "
            "2002), from the supplied PDF. The book is in copyright: this "
            "edition is a private re-typesetting for the owner's own reading, "
            "not for distribution. The three prefaces, the introduction and "
            "the thirteen chapters are as printed."
        ),
    },
    "anarchy": {
        "src": "Anarchy_State_and_Utopia.pdf",
        "slug": "Anarchy_State_and_Utopia",
        "title": "Anarchy, State, and Utopia",
        "author": "Robert Nozick",
        "year": "1974",
        "first_page": 7, "last_page": 374,
        "top": 26, "bottom": 585,
        "body_size": (7.6, 40.0),
        "note_size": (6.4, 7.5),
        "indent": 7.0,
        "drop_re": [r"^\d{1,3}$", r"^CHAPTER$", r"^\d{1,2}$"],
        "structure": anarchy_structure,
        "source_note": (
            "The text is Robert Nozick's \\emph{Anarchy, State, and Utopia} "
            "(Blackwell, 1974), from the supplied PDF. The book is in "
            "copyright: this edition is a private re-typesetting for the "
            "owner's own reading, not for distribution. The supplied scan "
            "carries an old OCR text layer, so reading errors proper to that "
            "scan remain in places."
        ),
    },
}
