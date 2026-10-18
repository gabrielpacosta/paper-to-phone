#!/usr/bin/env python3
"""
paper2phone: turn journal-article PDFs into phone-friendly reading files.

What it does
  * Reflowable text. One-, two- and three-column layouts are put back into
    reading order; running heads, page numbers and line numbers are stripped;
    words hyphenated at line breaks are repaired.
  * Every figure is kept: cropped from the page at high resolution and placed
    after the paragraph that first mentions it, with its full caption. Tables
    become text (cards when cells are wordy, the original image when complex);
    boxes, glossaries and display equations are kept.
  * Citations (numbered, bracketed or author-year) link to the parsed reference
    list, with DOI and PubMed links for each reference.
  * Output: a self-contained reader page (.html) with citation previews, a
    zoomable figure viewer, search, themes and remembered position; and an
    EPUB for Apple Books with a cover built from the paper's lead figure.

Usage
  python3 paper2phone.py paper.pdf                    # reader + EPUB beside the PDF
  python3 paper2phone.py ~/Downloads/*.pdf -o ~/Papers
  python3 paper2phone.py paper.pdf --format epub      # EPUB only
  python3 paper2phone.py paper.pdf --figures in-place # keep the PDF's figure position
  python3 paper2phone.py --watch "~/OneDrive/Paper to Phone"            # drop folder
  python3 paper2phone.py --install-service "~/OneDrive/Paper to Phone"  # start at login

Needs PyMuPDF:  pip install pymupdf
"""

import argparse
import base64
import bisect
import collections
import datetime
import html
import math
import os
import re
import shutil
import sys
import time
import traceback
import urllib.parse
import uuid
import zipfile
from itertools import accumulate
from pathlib import Path

try:
    import pymupdf
except ImportError:  # older installs only expose the "fitz" name
    try:
        import fitz as pymupdf
    except ImportError:
        sys.exit("paper2phone needs PyMuPDF:  pip install pymupdf")
try:   # damaged objects inside a PDF are handled below; MuPDF's own console noise is not useful
    pymupdf.TOOLS.mupdf_display_errors(False)
except Exception:
    pass

Rect = pymupdf.Rect
__version__ = "1.0"

# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

_TRANS = str.maketrans({
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st", "­": "", "\t": " ", "\xa0": " ",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "﻿": "",
})
BOLD_RE = re.compile(r"bold|semibold|black|heavy|demi", re.I)
HEAVY_RE = re.compile(r"bold|semibold|black|heavy|demi|medium", re.I)
ITALIC_RE = re.compile(r"italic|oblique", re.I)
ANCHOR_RE = re.compile(
    r"^\s*((?:Extended\s+Data|Supplementary|Suppl\.)\s+)?"
    r"(Fig(?:ure)?s?\.?|FIG(?:URE)?\.?|Table|TABLE|Box|BOX)\s*(S?\d{1,2})\b\s*(\||\.|:|—|–|-|\(|)",
    re.I)
MENTION_RE = re.compile(r"\b(Fig(?:ure)?s?\.?|Box(?:es)?|Tables?)\s*(S?\d{1,2})(?![\d])(?=[A-Za-z]?\b)")   # "Fig 2", "Fig 2A"
MENTION_LIST = re.compile(r"\b(Fig(?:ure)?s?\.?|Box(?:es)?|Tables?)\s*((?:S?\d{1,2}[A-Za-z]?)(?:\s*(?:,|and|&|–|-|to)\s*(?:S?\d{1,2}[A-Za-z]?))*)")
REFNUM_RE = re.compile(r"^\s*\[?(\d{1,4})(?:[.\])]\s*|\s+)(?=[^\s\d])")
CITE_RE = re.compile(r"[\d\s,–\-−‒]*\d[\d\s,–\-−‒]*")
TERMINAL_RE = re.compile(r"([.!?:;)\]”\"’]|\.\d+(?:[,–-]\d+)*)$")
BULLET_RE = re.compile(r"^[•●▪◦‣■□•▪●]\s?")
REFS_H = re.compile(r"^(references( and notes)?|bibliography|literature cited|works cited|cited literature)$", re.I)
TOP_H = re.compile(
    r"^(abstract|summary|introduction|background|results|discussion|conclusions?|concluding remarks|"
    r"methods|materials and methods|online methods|references( and notes)?|bibliography|glossary|"
    r"acknowledg(e)?ments?|author contributions|competing interests|declaration of interests|"
    r"data availability|code availability|supplementary (information|material)|limitations|additional information|"
    r"funding|author information|ethics( declarations)?|declarations)$", re.I)
BRACKET_CITE = re.compile(r"\[(\d{1,3}(?:\s*[,–\-]\s*\d{1,3})*)\]")
AY_CITE = re.compile(r"((?:(?:van|von|de|da|del|der|le|la|di)\s+)*[A-ZÀ-Ý][A-Za-zÀ-ÿ'’\-]+)"
                     r"(?:\s+et\s+al\.?|\s+(?:and|&amp;|&)\s+(?:(?:van|von|de|der)\s+)*[A-ZÀ-Ý][A-Za-zÀ-ÿ'’\-]+)?"
                     r",?\s+\(?((?:19|20)\d{2}[a-z]?)\b")
MATH_WORDS = {"cos", "sin", "tan", "exp", "log", "max", "min", "lim", "det", "arg", "sup", "inf", "tanh",
              "sinh", "cosh", "mod", "var", "cov", "diag", "sgn", "sign", "erf", "sech"}
MATH_FONT = re.compile(r"(CM(MI|SY|EX|BSY|MIB)\d|MSBM|MSAM|rsfs|eufm|esint|.*Math|Symbol|STIX|Euclid|MT-?Extra|MTSY|MTMI|Cambria)", re.I)
TOC_H = re.compile(r"^(sections|contents|in this (article|review)|outline)$", re.I)
AFFIL_RE = re.compile(r"^\W{0,3}(\d{1,2}\s*|[a-z]\s+)?(Institute|Department|Dept\.|University|Faculty|School of|Division|"
                      r"Laborator(y|ies)|Cent(re|er) for|Hospital|Correspond|\*?\s*Corresponding|e-?mail|Received|"
                      r"Accepted|Published( online)?:|Citation:|Editor:|Academic Editor|Copyright|©|Funding:|"
                      r"Competing interests?:|Conflicts? of interest:|Data Availability( Statement)?:|Financial Disclosure|"
                      r"Open Access|Reviewed by:|Edited by:|Keywords?:|Abbreviations:)", re.I)
ASIDE_RE = re.compile(r"^(glossary|key points|abbreviations|highlights|in brief|at a glance)\b", re.I)


def bare(text):
    """Heading text without its section number: '3.2 Methods' -> 'Methods'."""
    return re.sub(r"^\s*(?:\d+(?:\.\d+)*|[IVX]+|[A-Z])\.?\s+(?=[A-Za-z])", "", text).strip()


TEX_TEXT = re.compile(r"^(CM|SF|EC|TC|LM)(R|TI|BX|SL|BXTI|BXSL|CSC|SS|SSBX|SSI|SSDC|TT|SLTT|ITT|U|DUNH|B|RM|BI|SX|XC|CC|SI|SO)\d*$")
TEX_MATH = re.compile(r"^(CM|LM)(MI|SY|EX|MIB|BSY)\d*$|^(MSAM|MSBM|EUFM|EUSM|RSFS)\d*$")


def family(font):
    name = font.split("+", 1)[-1]
    fam = re.split(r"[-,]", name)[0] or name
    fam = re.sub(r"(PSMT|MT|PS)$", "", fam) or fam
    if TEX_TEXT.match(fam):
        return "TeXText"
    if TEX_MATH.match(fam):
        return "TeXMath"
    return fam


def area(r):
    return max(0.0, r.x1 - r.x0) * max(0.0, r.y1 - r.y0)


def inter_area(a, b):
    x0, y0, x1, y1 = max(a.x0, b.x0), max(a.y0, b.y0), min(a.x1, b.x1), min(a.y1, b.y1)
    return (x1 - x0) * (y1 - y0) if x1 > x0 and y1 > y0 else 0.0


def urect(page):
    """The page rectangle in unrotated coordinates (the space text positions use)."""
    if not page.rotation:
        return page.rect
    r = page.rect * page.derotation_matrix
    r.normalize()
    return r


def union(a, b):
    return Rect(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1))


def touches(a, b, pad=0.0):
    return a.x0 - pad <= b.x1 and b.x0 <= a.x1 + pad and a.y0 - pad <= b.y1 and b.y0 <= a.y1 + pad


def center(r):
    return ((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)


def inside(r, pt, pad=0.0):
    return r.x0 - pad <= pt[0] <= r.x1 + pad and r.y0 - pad <= pt[1] <= r.y1 + pad


def gap(a, b):
    dx = max(0.0, b.x0 - a.x1, a.x0 - b.x1)
    dy = max(0.0, b.y0 - a.y1, a.y0 - b.y1)
    return math.hypot(dx, dy)


def rkey(r):
    return (round(r.x0), round(r.y0), round(r.x1), round(r.y1))


def is_white(fill):
    try:
        return all(c > 0.96 for c in fill)
    except TypeError:
        return False


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")


def esc(t):
    return html.escape(_CTRL.sub("", t), quote=False)


def slug(text, used):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "s"
    base, i = "s-" + s, 2
    s = base
    while s in used:
        s, i = f"{base}-{i}", i + 1
    used.add(s)
    return s


# ---------------------------------------------------------------------------
# Text lines
# ---------------------------------------------------------------------------

class Span:
    __slots__ = ("text", "font", "size", "flags", "oy", "bold", "heavy", "italic", "x0", "x1")

    def __init__(self, text, font, size, flags, origin, x0=None, x1=None):
        self.text, self.font, self.size, self.flags = text, font, size, flags
        self.oy = origin[1]
        self.x0, self.x1 = (origin[0] if x0 is None else x0), (origin[0] if x1 is None else x1)
        base = font.split("+", 1)[-1]
        tex_bold = bool(re.match(r"(CM|SF|EC|LM)(BX|B\d|SSBX|BXTI|BXSL)", base)) or \
            bool(re.search(r"[.\-_](B|BI|BdIt|Bd|SB|Sb|Semibd|Hv|Blk)$|[\-_ ](6|7|8|9)00(It|Italic)?$", base))
        tex_ital = bool(re.match(r"(CM|SF|EC|LM)(TI|SL|SI|SO|BXTI|BXSL|ITT|SLTT)", base)) or \
            bool(re.search(r"[.\-_](I|BI|It|BdIt|Ital)$", base))
        self.bold = bool(flags & 16) or bool(BOLD_RE.search(font)) or tex_bold
        self.heavy = self.bold or bool(HEAVY_RE.search(font))
        self.italic = bool(flags & 2) or bool(ITALIC_RE.search(font)) or tex_ital


class Line:
    __slots__ = ("pno", "bbox", "spans", "text", "font", "fam", "size", "heavy",
                 "base", "horizontal", "role", "owner", "first", "maxgap", "dir")

    def __init__(self, pno, bbox, spans, direction):
        self.pno, self.bbox, self.spans = pno, bbox, spans
        self.text = re.sub(r"\s+", " ", "".join(s.text for s in spans)).strip()
        weight = collections.Counter()
        for s in spans:
            weight[(s.font, round(s.size, 1))] += len(s.text.strip()) or 0.01
        self.font, self.size = max(weight, key=lambda k: (weight[k], k[1]))   # ties go to the larger size
        self.fam = family(self.font)
        n = sum(len(s.text.strip()) for s in spans) or 1
        self.heavy = sum(len(s.text.strip()) for s in spans if s.heavy) / n
        dom = [s for s in spans if abs(s.size - self.size) < 0.06 and s.text.strip()]
        self.base = (max(dom, key=lambda s: len(s.text)) if dom else spans[0]).oy
        self.horizontal = abs(direction[0] - 1) < 0.02 and abs(direction[1]) < 0.02
        self.dir = tuple(direction)
        vis = [s for s in spans if s.text.strip()]
        self.maxgap = max((b.x0 - a.x1 for a, b in zip(vis, vis[1:])), default=0.0)
        self.role = None    # None = reading flow
        self.owner = None   # the figure/table/box that swallowed this line
        self.first = False  # first line of its PDF text block

    def segments(self):
        """[text, formats] pieces with sup/sub detected from size and baseline."""
        out = []
        for s in self.spans:
            if not s.text:
                continue
            fmt = set()
            if s.text.strip():
                small = s.size < self.size * 0.86
                if (s.flags & 1) or (small and s.oy < self.base - 0.12 * self.size):
                    fmt.add("sup")
                elif small and s.oy > self.base + 0.08 * self.size:
                    fmt.add("sub")
                if s.bold:
                    fmt.add("b")
                if s.italic:
                    fmt.add("i")
            out.append([s.text, frozenset(fmt)])
        return out


def extract_lines(page, pno, textpage=None):
    flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_LIGATURES & ~pymupdf.TEXT_PRESERVE_IMAGES
    out = []
    d = page.get_text("dict", flags=flags, textpage=textpage) if textpage else page.get_text("dict", flags=flags)
    for b in d["blocks"]:
        if b.get("type") != 0:
            continue
        first = True
        for ln in b["lines"]:
            spans = [Span(s["text"].translate(_TRANS), s["font"], s["size"], s["flags"], s["origin"],
                          s["bbox"][0], s["bbox"][2]) for s in ln["spans"]]
            spans = [s for s in spans if s.text]
            spans = strip_line_number(spans)
            if spans and any(s.text.strip() for s in spans):
                bbox = Rect(ln["bbox"])
                bbox.x0 = max(bbox.x0, min(s.x0 for s in spans if s.text.strip()))
                line = Line(pno, bbox, spans, ln["dir"])
                line.first, first = first, False
                out.append(line)
    return join_row_pieces(absorb_scripts(merge_markers(out)))


def join_row_pieces(lines):
    """Inline math splits a printed line into pieces whose baselines differ ('{ξ' + 'i , ξ' +
    'i } and asks'). Pieces on one row that touch or overlap horizontally form one line."""
    order = sorted((l for l in lines if l.horizontal), key=lambda l: (l.bbox.x0))
    bins = collections.defaultdict(list)
    for l in order:
        bins[int((l.bbox.y0 + l.bbox.y1) / 8)].append(l)
    gone, dead = set(), []
    for a in order:
        if id(a) in gone:
            continue
        cur = a
        while True:
            nxt = None
            k = int((cur.bbox.y0 + cur.bbox.y1) / 8)
            for b in (x for j in range(k - 2, k + 3) for x in bins.get(j, ())):
                if b is cur or b is a or id(b) in gone or b.bbox.x0 < cur.bbox.x0:
                    continue
                g = b.bbox.x0 - cur.bbox.x1
                h = min(cur.bbox.height, b.bbox.height)
                ov = min(cur.bbox.y1, b.bbox.y1) - max(cur.bbox.y0, b.bbox.y0)
                if -0.6 * cur.size <= g <= 0.4 * cur.size and ov >= 0.6 * h and abs(b.size - cur.size) < 1.5 \
                        and b.bbox.x1 > cur.bbox.x1 and (nxt is None or b.bbox.x0 < nxt.bbox.x0):
                    nxt = b
            if nxt is None:
                break
            glue = [] if cur.text.endswith(" ") or nxt.text.startswith((" ", ",", ".", ")", "}", "]")) else \
                [Span(" ", cur.spans[-1].font, cur.spans[-1].size, 0, (cur.bbox.x1, cur.base))]
            main = cur if cur.bbox.width >= nxt.bbox.width else nxt
            merged = Line(cur.pno, Rect(cur.bbox.x0, main.bbox.y0, nxt.bbox.x1, main.bbox.y1),
                          cur.spans + glue + nxt.spans, (1, 0))
            merged.first = cur.first or nxt.first
            gone.update((id(cur), id(nxt)))
            dead += [cur, nxt]
            cur = merged
        if cur is not a:
            lines = [cur if x is a else x for x in lines]
    return [x for x in lines if id(x) not in gone]


def absorb_scripts(lines):
    """TeX often emits sub- and superscripts (a_µ, x², citation numbers) as separate tiny
    text lines. Put each back into the line it decorates so the sentence stays whole."""
    hosts = sorted((l for l in lines if l.horizontal), key=lambda l: l.bbox.y0)
    gone, dead = set(), []      # dead keeps replaced lines alive so their ids are never reused

    def big(l):
        return max((s.size for s in l.spans if s.text.strip()), default=l.size)

    for f in lines:
        if not f.horizontal or len(f.text) > 15 or id(f) in gone:
            continue
        best = None
        for h in hosts:
            if h.bbox.y0 > f.bbox.y1:
                break
            if h is f or id(h) in gone or big(h) * 0.86 < big(f) or h.bbox.width < 2 * f.bbox.width:
                continue
            ov = min(h.bbox.y1, f.bbox.y1) - max(h.bbox.y0, f.bbox.y0)
            if ov <= 0.3 * f.bbox.height or not (h.bbox.x0 - 2 <= f.bbox.x0 <= h.bbox.x1 + 0.5 * h.size):
                continue
            if best is None or ov > best[0]:
                best = (ov, h)
        if best is None:
            continue
        h = best[1]
        at = 0
        for i, s in enumerate(h.spans):
            if s.text.strip() and s.x0 <= f.bbox.x0:
                at = i + 1
        spans = h.spans[:at] + f.spans + h.spans[at:]
        merged = Line(h.pno, Rect(h.bbox.x0, h.bbox.y0, max(h.bbox.x1, f.bbox.x1), h.bbox.y1), spans, (1, 0))
        merged.first = h.first
        gone.update((id(f), id(h)))
        dead += [f, h]
        hosts[hosts.index(h)] = merged
        lines = [merged if x is h else x for x in lines]
    return [x for x in lines if id(x) not in gone]


def strip_line_number(spans):
    """Manuscript line numbers ('12') sometimes share a text line with the sentence."""
    vis = [s for s in spans if s.text.strip()]
    if len(vis) < 2:
        return spans
    big = max(s.size for s in vis)
    a, b = vis[0], vis[1]
    num_w = len(a.text.strip()) * 0.6 * a.size
    if re.fullmatch(r"\d{1,4}", a.text.strip()) and a.size < 0.8 * big and b.x0 - a.x0 > num_w + 0.5 * big:
        return [s for s in spans if s is not a and not (spans.index(s) < spans.index(b) and not s.text.strip())]
    a, b = vis[-1], vis[-2]
    if re.fullmatch(r"\d{1,4}", a.text.strip()) and a.size < 0.8 * big and a.x0 - b.x1 > 1.2 * big:
        return [s for s in spans if s is not a]
    return spans


MARKER_RE = re.compile(r"^(\[\d{1,4}\]|\d{1,4}[.)]|\d{1,2}(\.\d{1,2})*|[A-Z]\.|[ivx]{1,4}\.|[•●▪◦‣■□\u2022\u25aa\u25cf])$")


def merge_markers(lines):
    """PDFs often split one visual line into several text lines: a reference number
    or section number ('12.', '3.2') next to its text, or a bold run-in label next to
    the sentence it starts. Glue pieces that share a baseline back together."""
    used, out = set(), []
    for l in lines:
        if id(l) in used:
            continue
        cur = l
        while cur.horizontal:
            marker = MARKER_RE.match(cur.text) is not None
            best = None
            for m in lines:
                if m is cur or m is l or id(m) in used or not m.horizontal or abs(m.base - cur.base) >= 1.2:
                    continue
                g = m.bbox.x0 - cur.bbox.x1
                if marker:
                    ok = 0 <= g <= 25 and m.text[:1].isalpha() and not MARKER_RE.match(m.text) \
                        and abs(m.size - cur.size) < 1.0
                else:
                    limit = 0.7 * min(cur.size, m.size)
                    if cur.heavy >= 0.85 and m.heavy < 0.5 and len(cur.text) <= 40:
                        limit = 1.05 * cur.size      # bold run-in label: "Encoder: The encoder..."
                    ok = -1 <= g <= limit and abs(m.size - cur.size) < 2.5
                if ok and (best is None or m.bbox.x0 < best.bbox.x0):
                    best = m
            if best is None:
                break
            last = cur.spans[-1]
            glue = [Span(" ", last.font, last.size, 0, (0, last.oy))] if not cur.spans[-1].text.endswith(" ") else []
            merged = Line(cur.pno, union(cur.bbox, best.bbox), cur.spans + glue + best.spans, (1, 0))
            merged.first = cur.first or best.first
            used.add(id(best))
            cur = merged
        out.append(cur)
    return [x for x in out if id(x) not in used]


class Style:
    """Finds the body-text font and groups near-identical font sizes."""

    def __init__(self, lines):
        hist = collections.defaultdict(collections.Counter)
        total = 0
        for l in lines:
            if l.horizontal:
                hist[l.fam][round(l.size * 4) / 4] += len(l.text)
                total += len(l.text)
        self.clusters, cands = {}, []
        for fam, ctr in hist.items():
            groups = []
            for s in sorted(ctr):
                if groups and s - groups[-1][-1] <= 0.3 and s - groups[-1][0] <= 0.6:
                    groups[-1].append(s)
                else:
                    groups.append([s])
            self.clusters[fam] = []
            for g in groups:
                w = sum(ctr[s] for s in g)
                c = sum(s * ctr[s] for s in g) / w
                self.clusters[fam].append((g[0], g[-1], c))
                cands.append((w, c, fam))
        if not cands:
            self.fam, self.size = "", 10.0
            return
        big = [c for c in cands if c[0] >= 0.12 * total] or [max(cands)]
        _, self.size, self.fam = max(big, key=lambda c: c[1])

    def size_class(self, fam, size):
        q = round(size * 4) / 4
        for lo, hi, c in self.clusters.get(fam, ()):
            if lo - 0.15 <= q <= hi + 0.15:
                return round(c, 2)
        return round(size, 1)

    def is_body(self, l):
        return l.fam == self.fam and abs(l.size - self.size) <= 0.6

    def key(self, l):
        return (l.fam, self.size_class(l.fam, l.size), l.heavy >= 0.85)


class Dehyphenator:
    """Decides whether 'short-' + 'range' is 'short-range' or 'signal-' + 'ling' is 'signalling',
    using words seen elsewhere in the same paper (plus the system word list if present)."""

    def __init__(self, pages):
        self.words, self.compounds = collections.Counter(), collections.Counter()
        for lines in pages:
            prev_hy = False
            for l in lines:
                toks = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)*", l.text)
                ends_hy = l.text.endswith("-")
                if prev_hy and toks:
                    toks = toks[1:]
                if ends_hy and toks:
                    toks = toks[:-1]
                for t in toks:
                    t = t.lower()
                    if "-" in t:
                        self.compounds[t] += 1
                        for part in t.split("-"):
                            self.words[part] += 1
                    else:
                        self.words[t] += 1
                prev_hy = ends_hy
        self.dictionary = set()
        for path in ("/usr/share/dict/words", "/usr/dict/words"):
            try:
                with open(path, encoding="utf-8", errors="ignore") as fh:
                    self.dictionary = {w.strip().lower() for w in fh}
                break
            except OSError:
                pass

    LIG_GAP = re.compile(r"\b([A-Za-z]*(?:fi|fl|ff|ffi|ffl))\s([a-z]{2,})\b")

    def ligatures(self, text):
        """'specifi cally' -> 'specifically': some PDFs put a space after a ligature glyph."""
        def fix(m):
            w = (m.group(1) + m.group(2)).lower()
            if w in self.words or w in self.dictionary:
                return m.group(1) + m.group(2)
            return m.group(0)
        return self.LIG_GAP.sub(fix, text) if " " in text else text

    def keep_hyphen(self, left, right):
        if re.search(r"\d", left) or (len(left) > 1 and re.search(r"[A-Z]", left[1:])):
            return True                       # CB1-, mRNA-, 2-AG style
        l, r = left.lower(), right.lower()
        if f"{l}-{r}" in self.compounds:
            return True
        if l + r in self.words or l + r in self.dictionary:
            return False
        return len(l) >= 3 and len(r) >= 3 and self.words[l] > 0 and self.words[r] > 0

    def join(self, pieces):
        """Join plain strings that were separate lines."""
        out = ""
        for p in pieces:
            p = p.strip()
            if not p:
                continue
            if not out:
                out = p
                continue
            m = re.search(r"(-?)([A-Za-z0-9]+)-$", out)
            r = re.match(r"[a-z]+", p)
            if m and r:
                out = out if (m.group(1) or self.keep_hyphen(m.group(2), r.group(0))) else out[:-1]
                out += p
            elif out.endswith("/") or (out[-1] in "–—" and out[-2:-1] != " "):
                out += p
            else:
                out += " " + p
        return out


CAP_LABEL_RE = re.compile(r"^\s*((?:Supplementary|Extended\s+Data)\s+)?(Fig(?:ure)?s?\.?|FIG(?:URE)?\.?|Table|TABLE|"
                          r"Box|BOX|Scheme|Plate)\s*S?\d{1,3}[A-Za-z]?(\s*[.:|]|\s+(?=[A-Z(])|\s*$)\s*")
SENTENCE_END = re.compile(r"(?<!\bFig)(?<!\bFigs)(?<!\bal)(?<!\be\.g)(?<!\bi\.e)(?<!\bvs)(?<!\bca)(?<!\bEq)(?<!\bRef)"
                          r"(?<!\bRefs)(?<!\bNo)(?<!\bcf)(?<!\bSt)(?<!\bDr)(?<! [A-Z])\.\s+(?=[A-Z(\[])|\s\|\s")


def looks_like_names(text):
    """'TIAN LAN', 'Ana Souza1, Gabriel Example2* and J. R. Smith': author names, nothing else."""
    t = re.sub(r"[\d*†‡§¶#,]+", ",", text)
    parts = [p.strip() for p in re.split(r",|;|&|\band\b", t) if p.strip()]
    if not parts or len(text) > 400 or sum(len(p.split()) for p in parts) < 2:
        return False
    for p in parts:
        words = p.split()
        if not 1 <= len(words) <= 5:
            return False
        if not all(re.fullmatch(r"([A-ZÀ-Ý][\w'’\-]*\.?|[A-Z]\.(-?[A-Z]\.)*|van|von|de|da|del|der|di|le|la|dos|das)", w) for w in words):
            return False
    return True


def cut_segments(segs, n):
    """Split formatted segments at plain-text offset n."""
    head, tail = [], []
    for t, f in segs:
        if n <= 0:
            tail.append([t, f])
        elif len(t) <= n:
            head.append([t, f])
            n -= len(t)
        else:
            head.append([t[:n], f])
            tail.append([t[n:], f])
            n = 0
    return head, tail


def join_segments(lines, dehy):
    """Concatenate the formatted pieces of several lines into one paragraph."""
    out = []
    for ln in lines:
        segs = []
        for t, f in ln.segments():           # merge same-format pieces within the line
            if segs and segs[-1][1] == f:
                segs[-1][0] += t
            else:
                segs.append([t, f])
        while segs and not segs[0][0].strip():
            segs.pop(0)
        if not segs:
            continue
        if out:
            while out and not out[-1][0].strip():
                out.pop()
            if len(out) > 1 and out[-1][0].strip() == "-":   # lone hyphen in its own span
                out.pop()
                out[-1][0] = out[-1][0].rstrip() + "-"
            prev = out[-1][0].rstrip()
            nxt = segs[0][0].lstrip()
            m = re.search(r"(-?)([A-Za-z0-9]+)-$", prev)
            r = re.match(r"[a-z]+", nxt)
            if m and r:
                keep = bool(m.group(1)) or dehy.keep_hyphen(m.group(2), r.group(0))
                prev = prev if keep else prev[:-1]
                out[-1][0] = prev
            elif prev.endswith("/") or (prev[-1:] in ("–", "—") and prev[-2:-1] != " "):
                out[-1][0] = prev
            else:
                out[-1][0] = prev + " "
            segs[0][0] = nxt
        out.extend([list(s) for s in segs])
    merged = []
    for t, f in out:
        if not t.strip():
            f = frozenset()
        if merged and merged[-1][1] == f:
            merged[-1][0] += t
        else:
            merged.append([t, f])
    return merged


# ---------------------------------------------------------------------------
# Layout objects
# ---------------------------------------------------------------------------

class Float:
    """A figure, table, box or aside that sits outside the running text."""

    def __init__(self, kind, num, pno, regions):
        self.kind, self.num, self.pno = kind, num, pno
        self.regions = list(regions)
        self.anchor = None
        self.caption = []
        self.rows = None
        self.lines = []
        self.children = []
        self.blocks = []
        self.images = []
        self.cont = False
        self.title = None
        self.id = None
        self.order = 0

    @property
    def bbox(self):
        u = Rect(self.regions[0])
        for r in self.regions[1:]:
            u = union(u, r)
        return u

    @property
    def label(self):
        name = {"fig": "Figure", "table": "Table", "box": "Box"}.get(self.kind, "")
        return f"{name} {self.num}" if self.num else name


class Elem:
    __slots__ = ("kind", "obj", "bbox")

    def __init__(self, kind, obj, bbox):
        self.kind, self.obj, self.bbox = kind, obj, bbox


class Leaf:
    """One column-run of elements in reading order."""

    def __init__(self, elems):
        self.elems = sorted(elems, key=lambda e: (round(e.bbox.y0), e.bbox.x0))
        ls = [e.obj for e in self.elems if e.kind == "line"]
        self.x0 = min((l.bbox.x0 for l in ls), default=0.0)
        self.x1 = max((l.bbox.x1 for l in ls), default=0.0)
        longs = [l for l in ls if len(l.text) > 25]
        self.justified = bool(longs) and sum(l.bbox.x1 >= self.x1 - 2.5 for l in longs) >= 0.6 * len(longs)
        # typical baseline-to-baseline distance: handles single, 1.5 and double spacing
        steps = sorted(b.base - a.base for a, b in zip(ls, ls[1:])
                       if abs(a.size - b.size) < 0.6 and 0.5 * a.size < b.base - a.base < 3.2 * a.size)
        self.pitch = steps[len(steps) // 2] if len(steps) >= 3 else None
        starts = collections.Counter(round(l.bbox.x0) for l in longs)
        self.x0mode = starts.most_common(1)[0][0] if starts else self.x0


def find_gutter(elems):
    x0 = min(e.bbox.x0 for e in elems)
    x1 = max(e.bbox.x1 for e in elems)
    W = x1 - x0
    if W < 40:
        return None
    n = int(W) + 2
    diff = [0] * (n + 1)
    for e in elems:
        a = max(0, int(math.ceil(e.bbox.x0 - x0 + 1)))
        b = min(n - 1, int(math.floor(e.bbox.x1 - x0 - 1)))
        if b >= a:
            diff[a] += 1
            diff[b + 1] -= 1
    cov = list(accumulate(diff))[:n]
    lo, hi = int(0.12 * W), int(0.88 * W)
    if hi <= lo:
        return None
    m = min(cov[lo:hi + 1])
    best, start = None, None
    for i in range(lo, hi + 2):
        if i <= hi and cov[i] == m:
            start = i if start is None else start
        elif start is not None:
            if best is None or i - start > best[0]:
                best = (i - start, start, i - 1)
            start = None
    return x0 + (best[1] + best[2]) / 2


def xycut(elems, depth=0):
    """Recursive column split: columns left to right, full-width items cut bands."""
    if not elems:
        return []
    if len(elems) == 1 or depth > 14:
        return [Leaf(elems)]
    gx = find_gutter(elems)
    if gx is None:
        return [Leaf(elems)]
    cross = [e for e in elems if e.bbox.x0 < gx - 1 and e.bbox.x1 > gx + 1]
    cross_ids = {id(e) for e in cross}
    rest = [e for e in elems if id(e) not in cross_ids]
    left = [e for e in rest if (e.bbox.x0 + e.bbox.x1) / 2 < gx]
    right = [e for e in rest if (e.bbox.x0 + e.bbox.x1) / 2 >= gx]
    if not left or not right or len(cross) > 0.6 * len(elems):
        return [Leaf(elems)]
    if not cross:
        return xycut(left, depth + 1) + xycut(right, depth + 1)
    cross.sort(key=lambda e: e.bbox.y0)
    groups = []
    for e in cross:
        if groups and e.bbox.y0 <= groups[-1][1] + 3:
            groups[-1][1] = max(groups[-1][1], e.bbox.y1)
            groups[-1][2].append(e)
        else:
            groups.append([e.bbox.y0, e.bbox.y1, [e]])
    # Real columns: some stretch between the spanning items holds material on BOTH sides
    # of the gutter. In single-column text only short lines (paragraph ends) miss the gutter.
    def side_counts(lo, hi):
        inb = [e for e in rest if lo <= (e.bbox.y0 + e.bbox.y1) / 2 <= hi]
        return (sum(1 for e in inb if (e.bbox.x0 + e.bbox.x1) / 2 < gx),
                sum(1 for e in inb if (e.bbox.x0 + e.bbox.x1) / 2 >= gx))
    edges = [-1e9] + [y for g in groups for y in (g[0], g[1])] + [1e9]
    columns = any(min(side_counts(edges[i], edges[i + 1])) >= 2 for i in range(0, len(edges) - 1, 2))
    if not columns and len(cross) > max(1, 0.2 * len(elems)):
        return [Leaf(elems)]
    rows = [[] for _ in groups]                # things sitting beside a spanning item
    bands = [[] for _ in range(len(groups) + 1)]
    for e in rest:
        h = max(e.bbox.y1 - e.bbox.y0, 1.0)
        gi = next((k for k, g in enumerate(groups)
                   if min(e.bbox.y1, g[1]) - max(e.bbox.y0, g[0]) > 0.5 * h), None)
        if gi is not None:
            rows[gi].append(e)
            continue
        cy = (e.bbox.y0 + e.bbox.y1) / 2
        bands[sum(1 for g in groups if cy > (g[0] + g[1]) / 2)].append(e)
    out = []
    for i, band in enumerate(bands):
        out += xycut(band, depth + 1)
        if i < len(groups):
            row = groups[i][2] + rows[i]
            out += xycut(row, depth + 1) if rows[i] and len(row) < len(elems) else [Leaf(row)]
    return out


class Para:
    def __init__(self, line, leaf, key):
        self.lines, self.leaves, self.key = [line], [leaf], key
        self.bullet = bool(BULLET_RE.match(line.text))
        self.kind, self.level, self.id = "p", None, None
        self.label = None

    def add(self, line, leaf):
        self.lines.append(line)
        self.leaves.append(leaf)

    @property
    def text(self):
        return self.label or " ".join(l.text for l in self.lines)

    @property
    def size(self):
        return max((l.size for l in self.lines), default=0)

    @property
    def pno(self):
        return self.lines[0].pno if self.lines else 0


class RefList:
    def __init__(self, refs, numbered):
        self.refs, self.numbered = refs, numbered   # refs: list of (num, lines, note_lines)
        self.id = "references"


# ---------------------------------------------------------------------------
# The converter
# ---------------------------------------------------------------------------

class Paper:
    def __init__(self, path, dpi=288, placement="first-mention", log=print):
        self.path = Path(path)
        self.doc = pymupdf.open(str(path))
        self.zoom = dpi / 72.0
        self.placement = placement
        self.log = log
        self.warnings = []
        self.images = {}          # name -> (bytes, mime)
        self.float_index = {}
        self.meta = {"title": None, "authors": None, "doi": None, "year": None, "journal": None}

    # ----- page analysis ---------------------------------------------------
    def analyse(self):
        doc = self.doc
        self.pages = [extract_lines(p, i) for i, p in enumerate(doc)]
        nchars = sum(len(l.text) for ls in self.pages for l in ls)
        self.ocr = False
        if nchars < 150 * max(1, len(doc)):
            self.pages = self._ocr_pages()
            self.ocr = True
        self._mark_running()
        for pno in range(len(self.pages)):
            self.pages[pno] = self._merge_words(self.pages[pno], self.doc[pno].rect)
        self.style = Style([l for ls in self.pages for l in ls if l.role is None])
        self._repair_text()
        self._content_box()
        self._front_matter()
        self.dehy = Dehyphenator(self.pages)
        self.drawings = []
        for p in doc:
            try:
                self.drawings.append(p.get_drawings())
            except Exception:
                self.drawings.append([])
        self._decorations()
        self._caption_styles()
        self.page_floats = [[] for _ in doc]
        orphans, unmatched = [], []
        for pno, page in enumerate(doc):
            o, u = self._find_floats(page, pno)
            orphans += o
            unmatched += u
        self._match_across_pages(orphans, unmatched)
        stream = []
        for pno in range(len(doc)):
            for leaf in self._order_page(pno):
                stream += self._leaf_stream(leaf, pno)
        stream = self._assemble_captions(stream)
        self.blocks = self._build_blocks(stream)
        for f in self._all_floats():
            if f.kind in ("box", "aside"):
                self._build_inner(f)
        self._postprocess()

    def _ocr_pages(self):
        """Scanned paper: recognise the text with Tesseract (through PyMuPDF)."""
        pages = []
        try:
            for i, p in enumerate(self.doc):
                self.log(f"  reading scanned page {i + 1}/{len(self.doc)} (OCR)")
                tp = p.get_textpage_ocr(dpi=300, full=True)
                pages.append(extract_lines(p, i, tp))
        except Exception as e:
            raise RuntimeError("this PDF is a scan with no text layer, and OCR is not available here "
                               f"({type(e).__name__}). Install Tesseract (macOS: `brew install tesseract`) "
                               "or run `ocrmypdf in.pdf out.pdf` first.")
        if sum(len(l.text) for ls in pages for l in ls) < 100 * max(1, len(self.doc)):
            raise RuntimeError("this PDF is a scan and OCR found almost no text.")
        self.warnings.append("Scanned PDF: text was recognised by OCR, so expect occasional misread words")
        return pages

    def _mark_running(self):
        n = len(self.pages)

        def key(t):
            return re.sub(r"\d+", "#", t.lower()).strip()

        def in_margin(l):
            H = urect(self.doc[l.pno]).height
            cy = (l.bbox.y0 + l.bbox.y1) / 2
            return cy < 0.13 * H or cy > 0.9 * H

        occ = collections.defaultdict(set)
        for lines in self.pages:
            for l in lines:
                if in_margin(l):
                    occ[key(l.text)].add(l.pno)
        need = max(2, math.ceil(0.4 * n))
        rep = {k for k, v in occ.items() if len(v) >= need}
        counts = collections.Counter()
        for lines in self.pages:
            for l in lines:
                k = key(l.text)
                if in_margin(l) and (k in rep or re.fullmatch(r"(page\s*)?#(\s*(of|/)\s*#)?", k)):
                    l.role = "running"
                    counts[l.text] += 1
                elif k in ("check for updates",):
                    l.role = "drop"
        self.running_text = [t for t, _ in counts.most_common(6)]

    def _repair_text(self):
        # 1) drop caps: a giant first letter beside the opening lines of a paragraph
        for ls in self.pages:
            for l in ls:
                if l.role is None and len(l.text) == 1 and l.text.isalpha() and l.size >= 2.2 * self.style.size:
                    near = [m for m in ls if m is not l and m.role is None and m.horizontal
                            and -2 <= m.bbox.x0 - l.bbox.x1 <= 25 and l.bbox.y0 - 4 <= m.bbox.y0 <= l.bbox.y1]
                    if not near:
                        continue
                    near.sort(key=lambda m: m.bbox.y0)
                    sp = near[0].spans[0]
                    near[0].spans.insert(0, Span(l.text, sp.font, sp.size, sp.flags, (0, sp.oy)))
                    near[0].text = l.text + near[0].text
                    for m in near:
                        m.bbox = Rect(l.bbox.x0, m.bbox.y0, m.bbox.x1, m.bbox.y1)
                    l.role = "drop"
        # 2) some PDFs emit "signifi cance", "fl uorescence": a ligature followed by a space
        lig = re.compile(r"(?<=[A-Za-z])(ffi|ffl|fi|fl) (?=[a-z])|\b(ffi|ffl|fi|fl) (?=[a-z]{2})")
        hits = sum(len(lig.findall(l.text)) for ls in self.pages for l in ls)
        if hits >= 5:
            for ls in self.pages:
                for l in ls:
                    for i, s in enumerate(l.spans):
                        s.text = lig.sub(lambda m: m.group(1) or m.group(2), s.text)
                        nxt = l.spans[i + 1].text if i + 1 < len(l.spans) else ""
                        if re.search(r"(ffi|ffl|fi|fl) $", s.text) and nxt[:1].islower():
                            s.text = s.text[:-1]
                    l.text = re.sub(r"\s+", " ", "".join(s.text for s in l.spans)).strip()

    @staticmethod
    def _gutters(lines):
        """Vertical bands with (almost) no text across the page: column gutters."""
        hz = [l for l in lines if l.horizontal and l.role is None]
        if len(hz) < 8:
            return []
        x0 = int(min(l.bbox.x0 for l in hz))
        x1 = int(max(l.bbox.x1 for l in hz)) + 1
        n = x1 - x0 + 1
        diff = [0] * (n + 1)
        for l in hz:
            a, b = int(l.bbox.x0) - x0, int(l.bbox.x1) - x0
            diff[max(a, 0)] += 1
            diff[min(b + 1, n)] -= 1
        cover = list(accumulate(diff))[:n]
        inner = sorted(cover[int(0.1 * n):int(0.9 * n)])
        if not inner:
            return []
        med = inner[len(inner) // 2]
        limit = 0.35 * med
        bands, start = [], None
        for i in range(n + 1):
            low = i < n and cover[i] <= limit and 0.08 * n < i < 0.92 * n
            if low and start is None:
                start = i
            elif not low and start is not None:
                if i - start >= 4:
                    bands.append((x0 + start, x0 + i))
                start = None
        return bands

    def _merge_words(self, lines, page_rect):
        """Join fragments of one visual line (PDFs that place every word separately),
        never across a column gutter."""
        gutters = self._gutters(lines)
        hz = sorted([l for l in lines if l.horizontal and l.role is None], key=lambda l: (l.base, l.bbox.x0))
        rows, cur = [], []
        for l in hz:
            if cur and abs(l.base - cur[0].base) < 1.0:
                cur.append(l)
            else:
                if cur:
                    rows.append(cur)
                cur = [l]
        if cur:
            rows.append(cur)
        originals = {id(l) for l in lines}
        consumed, finals = set(), []
        for row in rows:
            row.sort(key=lambda l: l.bbox.x0)
            # word-per-fragment PDFs: most pieces of the row are single words
            singles = sum(1 for l in row if " " not in l.text.strip())
            wordy_row = singles >= 3 and singles >= 0.6 * len(row)
            acc, merged = row[0], False
            for nxt in row[1:]:
                g = nxt.bbox.x0 - acc.bbox.x1
                crosses = any(acc.bbox.x1 - 1 < b1 and nxt.bbox.x0 + 1 > b0 for b0, b1 in gutters)
                limit = (4 if wordy_row else 0.7) * min(acc.size, nxt.size)
                if -1 <= g <= limit and not crosses and abs(acc.size - nxt.size) < 2.5:
                    last = acc.spans[-1]
                    glue = [] if last.text.endswith(" ") or nxt.spans[0].text.startswith(" ") else \
                        [Span(" ", last.font, last.size, 0, (0, last.oy))]
                    m = Line(acc.pno, union(acc.bbox, nxt.bbox), acc.spans + glue + nxt.spans, (1, 0))
                    m.first = acc.first or nxt.first
                    for piece in (acc, nxt):
                        if id(piece) in originals:
                            consumed.add(id(piece))
                    acc, merged = m, True
                else:
                    if merged:
                        finals.append(acc)
                    acc, merged = nxt, False
            if merged:
                finals.append(acc)
        if not consumed:
            return lines
        return [l for l in lines if id(l) not in consumed] + finals

    def _content_box(self):
        body = [l for ls in self.pages for l in ls if l.role is None and self.style.is_body(l) and l.horizontal]
        if len(body) < 10:
            r = urect(self.doc[0])
            self.content = Rect(r.x0 + 36, r.y0 + 36, r.x1 - 36, r.y1 - 36)
        else:
            xs0 = sorted(l.bbox.x0 for l in body)
            xs1 = sorted(l.bbox.x1 for l in body)
            ys0 = sorted(l.bbox.y0 for l in body)
            ys1 = sorted(l.bbox.y1 for l in body)
            q = lambda a, f: a[min(len(a) - 1, int(f * len(a)))]
            self.content = Rect(q(xs0, 0.03), q(ys0, 0.01), q(xs1, 0.97), q(ys1, 0.99))
        body_l = [l for ls in self.pages for l in ls if l.role is None and self.style.is_body(l) and len(l.text) > 30]
        self.two_col = bool(body_l) and sum(l.bbox.width < 0.6 * self.content.width for l in body_l) > 0.5 * len(body_l)
        # preprint line numbers: bare numbers outside the text column
        for ls in self.pages:
            for l in ls:
                if l.role is None and re.fullmatch(r"\d{1,4}", l.text) and (
                        l.bbox.x1 < self.content.x0 - 1 or l.bbox.x0 > self.content.x1 + 1):
                    l.role = "running"

    def _front_matter(self):
        p0 = [l for l in self.pages[0] if l.role is None and l.horizontal]
        self.title_lines = []
        alltext = " ".join(l.text for ls in self.pages for l in ls)
        m = re.search(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", " ".join(l.text for l in self.pages[0])) or \
            re.search(r"\b(10\.\d{4,9}/[^\s\"<>,;]+)", alltext)
        if m:
            self.meta["doi"] = m.group(1).rstrip(".)")
        m = re.search(r"©\s*[^0-9]{0,80}?((?:19|20)\d{2})", alltext) or \
            re.search(r"(?:Published|Accepted|Received)[^0-9]{0,40}?\d{0,2}[^0-9]{0,15}((?:19|20)\d{2})", alltext)
        if m:
            self.meta["year"] = m.group(1)
        if not p0:
            return
        sizes = collections.defaultdict(str)
        for l in p0:
            sizes[round(l.size)] += l.text + " "
        good = [s for s, t in sizes.items() if len(t.strip()) >= 12 and len(t.split()) >= 2]
        top = max(good) if good else 0
        if top < 1.35 * self.style.size:
            self._title_from_metadata(p0)
            return
        tl = [l for l in p0 if abs(l.size - top) < 0.6]
        self.title_lines = tl
        self.meta["title"] = " ".join(l.text for l in sorted(tl, key=lambda l: (l.bbox.y0, l.bbox.x0)))
        ty0 = min(l.bbox.y0 for l in tl)
        if ty0 < 0.5 * urect(self.doc[0]).height:
            for l in p0:
                if l.bbox.y1 <= ty0 + 1 and l not in tl:
                    l.role = "drop"
        pieces = [x.strip() for t in self.running_text for x in re.split(r"\s[|·•]\s|\s{3,}", t)]   # "PLOS One | https://doi..."
        jt = [t for t in pieces if not re.search(r"\d|page|doi|http", t, re.I)
              and not re.fullmatch(r"(review|research|original|brief)?\s*(article|paper|report|communication)s?", t, re.I)
              and len(t) > 6]
        if jt:
            self.meta["journal"] = jt[0]

    def _title_from_metadata(self, p0):
        """Titles set at body size (AMS, some preprints): trust the PDF's own title field when
        the same words are printed near the top of page one."""
        meta = self.doc.metadata or {}
        t = re.sub(r"\s+", " ", meta.get("title") or "").strip()
        if len(t.split()) < 3 or len(t) > 300 or re.match(r"(microsoft word|untitled)|\S+\.(docx?|tex|pdf|indd)$", t, re.I):
            return
        norm = lambda x: re.sub(r"[^a-z0-9]+", "", x.lower())
        want = norm(t)
        half = urect(self.doc[0]).height * 0.5
        hits = [l for l in p0 if l.bbox.y0 < half and len(norm(l.text)) >= 4 and norm(l.text) in want]
        if hits and sum(len(norm(l.text)) for l in hits) >= 0.7 * len(want):
            self.title_lines = hits
        self.meta["title"] = t

    def _decorations(self):
        n = len(self.doc)
        cnt = collections.Counter()
        for pno, page in enumerate(self.doc):
            keys = {rkey(Rect(d["rect"])) for d in self.drawings[pno]}
            keys |= {rkey(Rect(i["bbox"])) for i in page.get_image_info()}
            cnt.update(keys)
        need = max(2, math.ceil(0.4 * n))
        self.deco = {k for k, v in cnt.items() if v >= need} if n > 1 else set()

    def _anchor(self, l):
        if l.role is not None or not l.horizontal:
            return None
        m = ANCHOR_RE.match(l.text)
        if not m:
            return None
        word = m.group(2).lower()
        kind = "fig" if word.startswith("fig") else "table" if word.startswith("tab") else "box"
        first = next((s for s in l.spans if s.text.strip()), l.spans[0])
        distinct = first.heavy or l.size < self.style.size - 0.35 or l.fam != self.style.fam
        sep = m.group(4)
        after = l.text[m.end():].lstrip()
        if sep == "(" and "contin" not in l.text.lower():
            return None
        if sep in ("|", ":"):
            ok = distinct or l.first
        elif sep in (".", "—", "–", "-"):
            ok = distinct or (l.first and after[:1].isupper())
        elif sep == "(":
            ok = True
        else:   # "FIGURE 1 Title" (bold label, no punctuation) or a bare "Table 1" line
            ok = first.heavy and (not after or after[:1].isupper())
        if not ok:
            return None
        num = m.group(3).upper()
        if m.group(1):
            num = ("ED" if m.group(1).lower().startswith("ext") else "S") + num.lstrip("S")
        return kind, num

    def _caption_styles(self):
        self.cap_styles = set()
        for ls in self.pages:
            for i, l in enumerate(ls):
                a = self._anchor(l)
                if a and a[0] in ("fig", "table"):
                    self.cap_styles.add((l.fam, self.style.size_class(l.fam, l.size)))

    def _protected(self, l):
        """Long lines of body or caption text are never swallowed into a figure."""
        if not l.horizontal:
            return False
        n = len(l.text)
        if n > 70:
            return True
        cap = (l.fam, self.style.size_class(l.fam, l.size)) in self.cap_styles
        return n > 28 and (self.style.is_body(l) or cap)

    def _find_floats(self, page, pno):
        lines = self.pages[pno]
        drawings = self.drawings[pno]
        st = self.style
        if self._rotated_page(page, pno, lines):
            return [], []
        anchors = [(l,) + a for l in lines for a in [self._anchor(l)] if a]
        anchor_lines = {id(a[0]) for a in anchors}
        floats, boxes = self.page_floats[pno], []

        # Boxes: a tinted or framed rectangle around a "Box N" label
        for l, kind, num in anchors:
            if kind != "box":
                continue
            c = center(l.bbox)
            cands = []
            for d in drawings:
                r = Rect(d["rect"])
                if area(r) < 15000 or r.height < 60 or not inside(r, c):
                    continue
                fill = d.get("fill")
                framed = d.get("color") is not None and d.get("type") in ("s", "fs")
                if (fill is not None and not is_white(fill)) or framed:
                    cands.append(r)
            if not cands:
                continue
            b = Float("box", num, pno, [min(cands, key=area)])
            b.anchor, b.cont = l, "contin" in l.text.lower()
            boxes.append(b)
            floats.append(b)
            for x in lines:
                if x.role is None and inside(b.bbox, center(x.bbox)):
                    x.role, x.owner = "in", b

        # Tables. Labelled tables: find the table's extent from its caption first,
        # then parse the cells inside that area. Unlabelled ruled tables only inside boxes.
        tab_anchors = [(l, n) for l, k, n in anchors if k == "table"]
        table_rects = []
        for l, n in list(tab_anchors):
            r = self._table_extent(l, lines, drawings) or self._table_extent_above(l, lines, drawings)
            if r is None:
                continue
            fl = Float("table", n, pno, [r])
            fl.anchor, fl.rows = l, self._parse_table(page, r)
            box = next((b for b in boxes if inter_area(r, b.bbox) > 0.8 * area(r)), None)
            (box.children if box else floats).append(fl)
            table_rects.append(r)
            for x in lines:
                if x.role in (None, "in") and inside(r, center(x.bbox)) and id(x) not in anchor_lines:
                    x.role, x.owner = "in", fl
            tab_anchors.remove((l, n))
        if tab_anchors or boxes:
            try:
                found = page.find_tables(strategy="lines").tables
            except Exception:
                found = []
            for t in found:
                r = Rect(t.bbox)
                if t.row_count < 2 or t.col_count < 2 or area(r) > 0.85 * area(urect(page)):
                    continue
                if any(inter_area(r, tr) > 0.3 * area(r) for tr in table_rects):
                    continue
                rows = [[(c or "") for c in row] for row in t.extract()]
                cells = [c for row in rows for c in row]
                if sum(1 for c in cells if c.strip()) < 0.5 * len(cells):
                    continue
                box = next((b for b in boxes if inter_area(r, b.bbox) > 0.8 * area(r)), None)
                lab = None
                for l, n in tab_anchors:      # caption below the table, or further away
                    horiz = min(r.x1, l.bbox.x1) - max(r.x0, l.bbox.x0) > 0
                    if horiz and (-4 <= r.y0 - l.bbox.y1 <= 90 or -4 <= l.bbox.y0 - r.y1 <= 40):
                        if lab is None or gap(r, l.bbox) < gap(r, lab[0].bbox):
                            lab = (l, n)
                if not box and not lab:
                    continue
                fl = Float("table", lab[1] if lab else None, pno, [r])
                fl.rows, fl.anchor = rows, (lab[0] if lab else None)
                (box.children if box else floats).append(fl)
                table_rects.append(r)
                for x in lines:
                    if x.role in (None, "in") and inside(r, center(x.bbox)) and id(x) not in anchor_lines:
                        x.role, x.owner = "in", fl
                if lab:
                    tab_anchors.remove(lab)

        # Figures: clusters of vector drawings and images
        W, H = urect(page).width, urect(page).height
        box_bg = [b.regions[0] for b in boxes]
        cw = self.content.width
        items = []
        for info in page.get_image_info():
            r = Rect(info["bbox"]) & urect(page)
            if r.is_empty or r.width < 1 or r.height < 1 or rkey(Rect(info["bbox"])) in self.deco:
                continue
            if r.width >= 0.9 * W and r.height >= 0.9 * H:
                continue                       # a page scan or background, not a figure
            items.append(("img", r))
        for d in drawings:
            r0 = Rect(d["rect"])
            if rkey(r0) in self.deco:
                continue
            r = Rect(r0)
            if r.width < 0.6:
                r.x0, r.x1 = r.x0 - 0.3, r.x1 + 0.3
            if r.height < 0.6:
                r.y0, r.y1 = r.y0 - 0.3, r.y1 + 0.3
            if r.width >= 0.95 * W and r.height >= 0.95 * H:
                continue
            if any(rkey(r0) == rkey(bg) for bg in box_bg):
                continue
            if any(inter_area(r, t) >= 0.8 * area(r) for t in table_rects):
                continue
            if (r.height <= 1.6 and r.width >= 0.5 * cw) or (r.width <= 1.6 and r.height >= 0.5 * self.content.height):
                continue
            items.append(("draw", r))
        clusters = self._cluster(items)
        cands = []
        for rect, members in clusters:
            if rect.width < 40 or rect.height < 30:
                continue
            if any(inter_area(rect, t) > 0.3 * area(rect) for t in table_rects):
                continue
            if any(inside(rect, center(l.bbox), -1) for l, k, _ in anchors if k in ("box", "table")):
                continue
            # drawing bounding boxes ignore clipping, so a shape can "reach" into the
            # caption or body text: cut those lines back out of the figure area
            rect = self._trim_prose(rect, lines)
            if rect is None:
                continue
            img_area = sum(inter_area(m[1], rect) for m in members if m[0] == "img")
            ndraw = sum(1 for m in members if m[0] == "draw" and touches(m[1], rect))
            if img_area < 2000 and ndraw < 4:
                continue
            cands.append([rect, None])

        for fc in cands:                       # pull in labels that belong to the drawing
            fc[0] = self._absorb(fc[0], lines, fc, anchor_lines)

        fig_anchors = [(l, n) for l, k, n in anchors if k == "fig"]
        assign = collections.defaultdict(list)
        orphans = []
        for fc in cands:
            if fig_anchors:
                l, n = min(fig_anchors, key=lambda a: gap(fc[0], a[0].bbox))
                assign[(id(l), n)].append(fc)
            else:
                orphans.append(fc)
        unmatched = []
        for l, n in fig_anchors:
            group = sorted(assign.get((id(l), n), []), key=lambda f: -area(f[0]))
            if not group:
                unmatched.append((pno, l, n))
                continue
            regions = [group[0][0]]
            for fc in group[1:]:
                u = union(regions[0], fc[0])
                if any(x.role is None and self._protected(x) and inside(u, center(x.bbox)) for x in lines):
                    regions.append(fc[0])
                else:
                    regions[0] = u
            fl = Float("fig", n, pno, regions)
            fl.anchor = l
            for fc in group:
                fc[1] = fl
            self._finish_regions(fl, lines, anchor_lines)
            self._place_in_box_or_page(fl, boxes, floats)
        for x in lines:
            if x.role == "fig" and isinstance(x.owner, list):
                x.owner = x.owner[1] if x.owner[1] is not None else x.owner
        self._find_equations(pno, lines, floats)
        return [(pno, fc, boxes) for fc in orphans], unmatched

    def _is_math_font(self, font):
        name = font.split("+", 1)[-1]
        if MATH_FONT.match(name) or family(name) == "TeXMath":
            return True
        return family(name) == "TeXText" and self.style.fam != "TeXText"

    def _find_equations(self, pno, lines, floats):
        """Display equations come out of PDFs as scrambled text; keep them as images."""
        cw = self.content.width
        mid = (self.content.x0 + self.content.x1) / 2
        flat = [l for l in lines if l.role is None and l.horizontal]
        # column geometry: equations live inside one column, prose fills it
        if self.two_col:
            colw = 0.48 * cw
            right_x0 = [l.bbox.x0 for l in flat if l.bbox.x0 > mid - 10]
            rx0 = sorted(right_x0)[len(right_x0) // 10] if right_x0 else mid
        else:
            colw, rx0 = cw, self.content.x0

        def col(l):
            return 0 if not self.two_col or center(l.bbox)[0] < mid else 1

        def colx0(l):
            return rx0 if col(l) else self.content.x0

        def nwords(l):
            return sum(w.lower() not in MATH_WORDS for w in re.findall(r"(?<![\\\w])[A-Za-z][a-z]{2,}\b", l.text))

        def wordy(l, k):
            return nwords(l) >= k

        def prose(l):
            """A body line with inline math is still prose: it fills the column with words."""
            if l.bbox.width >= 0.9 * cw or wordy(l, 5) or REFNUM_RE.match(l.text) and wordy(l, 2):
                return True
            if l.bbox.width >= 0.72 * colw and wordy(l, 4):
                return True
            return abs(l.bbox.x0 - colx0(l)) < 3 and l.bbox.width >= 0.45 * colw and wordy(l, 3)

        def mathy(l):
            if prose(l):
                return False
            n = sum(len(s.text.strip()) for s in l.spans) or 1
            m = sum(len(s.text.strip()) for s in l.spans if self._is_math_font(s.font))
            sym = len(re.findall(r"[=<>≤≥≈≡∝∑∏∫√±∓→←↔∂∇∞⊗⊕∈∉⊂⊆∀∃∧∨]", l.text))
            return m / n >= 0.3 or (m / n >= 0.1 and sym >= 1) or (sym >= 2 and len(l.text) < 80 and
                                                                     not re.search(r"[a-z]{4,} [a-z]{4,} [a-z]{4,}", l.text))

        def glued(r, c, skip=()):
            """Inline math sits on a prose row (fractions, sub/superscripts): not a display."""
            row = [l for l in flat if l not in skip and col(l) == c and
                   min(l.bbox.y1, r.y1) - max(l.bbox.y0, r.y0) > 0.3 * min(l.bbox.height, r.height)]
            if any(prose(l) for l in row):
                return True
            if not row:
                return False
            # a prose row broken into pieces by inline fractions: starts at the margin, fills the column
            x0 = min([l.bbox.x0 for l in row] + [r.x0])
            x1 = max([l.bbox.x1 for l in row] + [r.x1])
            return abs(x0 - colx0(row[0])) < 3 and x1 - x0 >= 0.85 * colw and sum(map(nwords, row)) >= 4

        seeds = [l for l in flat if mathy(l)]
        seeds = [l for l in seeds if not glued(l.bbox, col(l))]
        if not seeds:
            return
        groups = []
        gap = max(6, 0.9 * self.style.size)
        for l in sorted(seeds, key=lambda l: l.bbox.y0):
            for g in reversed(groups[-3:]):
                if col(g[-1]) == col(l) and l.bbox.y0 - max(x.bbox.y1 for x in g) <= gap:
                    g.append(l)
                    break
            else:
                groups.append([l])
        for g in groups:
            r = Rect(g[0].bbox)
            for l in g[1:]:
                r = union(r, l.bbox)
            if r.height > 0.5 * self.content.height:      # a page of symbols is a figure or table, not an equation
                continue
            c = col(g[0])
            if glued(r, c, g):
                continue
            members = list(g)
            for l in lines:
                if l.role is not None or l in members or col(l) != c:
                    continue
                if prose(l):
                    continue
                left_word = abs(l.bbox.x0 - colx0(l)) < 3 and wordy(l, 1) and not mathy(l)   # "reads", "algebra"
                near = touches(Rect(r.x0 - 8, r.y0 - 6, r.x1 + 8, r.y1 + 6), l.bbox)
                within = r.y0 - 2 <= l.bbox.y0 and l.bbox.y1 <= r.y1 + 2      # a piece of the same display
                eqno = re.fullmatch(r"\((\d{1,3}|[A-Z]?\d{1,3}\.?\d*)[a-z]?\)", l.text) and \
                    r.y0 - 6 <= (l.bbox.y0 + l.bbox.y1) / 2 <= r.y1 + 6 and l.bbox.x0 > r.x1 - 2
                if eqno or (not left_word and (within or (near and len(l.text) <= 10))):
                    members.append(l)
                    r = union(r, l.bbox)
            body_like = [l for l in members if len(l.text) > 50 and not mathy(l)]
            if body_like:
                continue
            f = Float("eq", None, pno, [r])
            for l in members:
                l.role, l.owner = "eq", f
            floats.append(f)

    def _rotated_page(self, page, pno, lines):
        rot = [l for l in lines if not l.horizontal and l.role is None and abs(abs(l.dir[1]) - 1) < 0.05]
        total = sum(len(l.text) for l in lines if l.role is None) or 1
        if sum(len(l.text) for l in rot) < max(200, 0.5 * total):
            return False
        rot_anchor = any(ANCHOR_RE.match(l.text) for l in rot)
        flat_anchor = any(self._anchor(l) for l in lines if l.horizontal)
        if flat_anchor and not rot_anchor:     # an upright figure whose labels are rotated
            return False
        r = Rect(rot[0].bbox)
        for l in rot[1:]:
            r = union(r, l.bbox)
        for d in self.drawings[pno]:
            dr = Rect(d["rect"])
            if rkey(dr) not in self.deco and touches(dr, r, 4):
                r = union(r, dr)
        kind, num, cap = "fig", None, []
        for l in rot:
            m = ANCHOR_RE.match(l.text)
            if m:
                word = m.group(2).lower()
                kind = "fig" if word.startswith("fig") else "table" if word.startswith("tab") else "box"
                num = m.group(3).upper()
                cap = [x for x in rot if x.fam == l.fam and abs(x.size - l.size) < 0.4 and x.dir == l.dir
                       and abs((x.bbox.x0 if l.dir[1] < 0 else -x.bbox.x1) - (l.bbox.x0 if l.dir[1] < 0 else -l.bbox.x1)) < 40]
                break
        body = [l for l in rot if l not in cap]
        if cap and body:                       # the caption is shown as text, not in the picture
            r = Rect(body[0].bbox)
            for l in body[1:]:
                r = union(r, l.bbox)
            for d in self.drawings[pno]:
                dr = Rect(d["rect"])
                if rkey(dr) not in self.deco and touches(dr, r, 4) and not any(
                        inter_area(dr, c.bbox) > 0.5 * area(c.bbox) for c in cap):
                    r = union(r, dr)
        f = Float(kind if kind != "box" else "fig", num, pno, [r])
        f.rotate = 90 if rot[0].dir[1] < 0 else -90
        cap.sort(key=lambda x: x.bbox.x0 if f.rotate == 90 else -x.bbox.x1)
        f.caption = cap
        f.anchor = cap[0] if cap else None
        for l in lines:
            if l.role is None and not l.horizontal:
                l.role, l.owner = ("in", f) if l in cap else ("fig", f)
        self.page_floats[pno].append(f)
        return True

    def _absorb(self, reg, lines, owner, anchor_lines):
        """Grow a figure area to take in its own labels (panel letters, axis text, legends)."""
        st = self.style
        for _ in range(8):
            grew = False
            for l in lines:
                ok_role = l.role is None or (l.role == "in" and isinstance(l.owner, Float)
                                             and l.owner.kind == "box")
                if l.role == "fig" and l.owner is not owner and isinstance(owner, Float):
                    ok_role = True             # claimed earlier by one of this figure's pieces
                if not ok_role or id(l) in anchor_lines or self._protected(l):
                    continue
                c = center(l.bbox)
                near = gap(reg, l.bbox) <= 12 and not st.is_body(l) and \
                    (l.fam, st.size_class(l.fam, l.size)) not in self.cap_styles and \
                    (len(l.text) <= 30 or not l.horizontal)
                if inside(reg, c, 1.5) or near:
                    if l.role == "fig" and l.owner is owner:
                        continue
                    l.role, l.owner = "fig", owner
                    reg = union(reg, l.bbox)
                    grew = True
            if not grew:
                break
        return reg

    def _rules(self, pno):
        ys = []
        for d in self.drawings[pno]:
            r = Rect(d["rect"])
            if rkey(r) not in self.deco and r.height <= 1.6 and r.width >= 0.9 * self.content.width:
                ys.append((r.y0 + r.y1) / 2)
        return ys

    def _clamp(self, r, pno, lines):
        c = self.content
        r = Rect(max(r.x0, c.x0 - 8), max(r.y0, c.y0 - 2), min(r.x1, c.x1 + 8), min(r.y1, c.y1 + 4))
        for y in self._rules(pno):              # a full-width rule is a hard border
            if r.y0 + 4 < y < r.y1 - 4:
                r = Rect(r.x0, r.y0, r.x1, y - 1) if y - r.y0 >= r.y1 - y else Rect(r.x0, y + 1, r.x1, r.y1)
        return self._trim_prose(r, lines) or r

    def _finish_regions(self, fl, lines, anchor_lines):
        regs = []
        for r in fl.regions:
            r = self._clamp(r, fl.pno, lines)
            regs.append(self._absorb(r, lines, fl, anchor_lines))
        fl.regions = regs

    def _trim_prose(self, rect, lines):
        def hits(l):
            ov_x = min(rect.x1, l.bbox.x1) - max(rect.x0, l.bbox.x0)
            ov_y = min(rect.y1, l.bbox.y1) - max(rect.y0, l.bbox.y0)
            return ov_x > 0 and ov_y >= 0.35 * l.bbox.height
        for _ in range(4):
            prose = [l for l in lines if l.role in (None, "in") and self._protected(l) and hits(l)]
            if not prose:
                return rect
            y0 = min(l.bbox.y0 for l in prose)
            y1 = max(l.bbox.y1 for l in prose)
            x0 = min(l.bbox.x0 for l in prose)
            x1 = max(l.bbox.x1 for l in prose)
            opts = [Rect(rect.x0, rect.y0, rect.x1, y0 - 1), Rect(rect.x0, y1 + 1, rect.x1, rect.y1),
                    Rect(rect.x0, rect.y0, x0 - 1, rect.y1), Rect(x1 + 1, rect.y0, rect.x1, rect.y1)]
            opts = [o for o in opts if o.width > 30 and o.height > 25]
            if not opts:
                return None
            rect = max(opts, key=area)
        return rect

    def _place_in_box_or_page(self, fl, boxes, floats):
        box = next((b for b in boxes if inter_area(fl.bbox, b.bbox) > 0.8 * area(fl.bbox)), None)
        (box.children if box else floats).append(fl)

    def _cluster(self, items, pad=7.0):
        groups = []
        for kind, r in sorted(items, key=lambda it: (it[1].y0, it[1].x0)):
            hits = [g for g in groups if touches(g[0], r, pad)]
            if not hits:
                groups.append([Rect(r), [(kind, r)]])
                continue
            g0 = hits[0]
            g0[0] = union(g0[0], r)
            g0[1].append((kind, r))
            for g in hits[1:]:
                g0[0] = union(g0[0], g[0])
                g0[1].extend(g[1])
                groups.remove(g)
        merged = True
        while merged:
            merged = False
            for i in range(len(groups)):
                for j in range(i + 1, len(groups)):
                    if touches(groups[i][0], groups[j][0], pad):
                        groups[i][0] = union(groups[i][0], groups[j][0])
                        groups[i][1].extend(groups[j][1])
                        del groups[j]
                        merged = True
                        break
                if merged:
                    break
        return groups

    def _table_extent(self, anchor, lines, drawings):
        """Area of a table whose caption sits above it: the run of non-body lines below
        the caption, widened to the table's own rules and cell shading."""
        st, c = self.style, self.content
        cw = c.width
        if anchor.bbox.width >= 0.55 * cw or not self.two_col:
            xr = (c.x0 - 2, c.x1 + 2)
        elif center(anchor.bbox)[0] < (c.x0 + c.x1) / 2:
            xr = (c.x0 - 2, (c.x0 + c.x1) / 2)
        else:
            xr = ((c.x0 + c.x1) / 2, c.x1 + 2)
        below = sorted([l for l in lines if l.role is None and l.horizontal and l is not anchor
                        and l.bbox.y0 >= anchor.bbox.y1 - 1 and l.bbox.x1 > xr[0] and l.bbox.x0 < xr[1]],
                       key=lambda l: l.bbox.y0)
        taken, last_y, title = [], anchor.bbox.y1, True
        for l in below:
            siblings = any(m is not l and abs(m.base - l.base) < 1 for m in below)
            if title and l.fam == anchor.fam and abs(l.size - anchor.size) < 0.3 and l.bbox.y0 - last_y < 6 \
                    and abs(l.bbox.x0 - anchor.bbox.x0) < 3 and not siblings and self._anchor(l) is None:
                last_y = l.bbox.y1          # second line of the caption title
                continue
            title = False
            limit = 32 if not taken else 18          # captions often sit a little apart
            if l.bbox.y0 - last_y > limit or self._prose(l) or self._anchor(l):
                break
            taken.append(l)
            last_y = max(last_y, l.bbox.y1)
        if len(taken) < 2:
            return None
        r = Rect(taken[0].bbox)
        for l in taken[1:]:
            r = union(r, l.bbox)
        for _ in range(2):                  # rules and shading that belong to the table
            for d in drawings:
                dr = Rect(d["rect"])
                if rkey(dr) in self.deco or dr.width > 0.98 * urect(self.doc[anchor.pno]).width:
                    continue
                if dr.y0 >= anchor.bbox.y1 - 2 and dr.y1 <= r.y1 + 4 and touches(dr, r, 3) and \
                        dr.x0 >= c.x0 - 12 and dr.x1 <= c.x1 + 12:
                    r = union(r, dr)
            for l in lines:
                if l.role is None and l is not anchor and l.horizontal and inside(r, center(l.bbox)) \
                        and not self._prose(l):
                    r = union(r, l.bbox)
        return r

    def _prose(self, l):
        """A running-text line (as opposed to a table row): body type, long, normal word spacing."""
        return self.style.is_body(l) and len(l.text) > 40 and l.maxgap < 1.6 * l.size

    def _table_extent_above(self, anchor, lines, drawings):
        """Table whose caption sits below it."""
        c = self.content
        if anchor.bbox.width >= 0.55 * c.width or not self.two_col:
            xr = (c.x0 - 2, c.x1 + 2)
        elif center(anchor.bbox)[0] < (c.x0 + c.x1) / 2:
            xr = (c.x0 - 2, (c.x0 + c.x1) / 2)
        else:
            xr = ((c.x0 + c.x1) / 2, c.x1 + 2)
        above = sorted([l for l in lines if l.role is None and l.horizontal and l is not anchor
                        and l.bbox.y1 <= anchor.bbox.y0 + 1 and l.bbox.x1 > xr[0] and l.bbox.x0 < xr[1]],
                       key=lambda l: -l.bbox.y1)
        taken, top = [], anchor.bbox.y0
        for l in above:
            if top - l.bbox.y1 > 18 or self._prose(l) or self._anchor(l):
                break
            taken.append(l)
            top = min(top, l.bbox.y0)
        if len(taken) < 2:
            return None
        r = Rect(taken[0].bbox)
        for l in taken[1:]:
            r = union(r, l.bbox)
        for d in drawings:                     # booktabs rules just above / below the rows
            dr = Rect(d["rect"])
            if rkey(dr) in self.deco:
                continue
            if dr.y1 <= anchor.bbox.y0 + 1 and touches(dr, r, 4) and dr.x0 >= c.x0 - 12 and dr.x1 <= c.x1 + 12:
                r = union(r, dr)
        return r

    def _parse_table(self, page, r):
        """Cell text for the table inside r. Several strategies are tried; a table that the
        parser returns in pieces (header block, body block) is stitched back together."""
        best = None
        for kw in ({"strategy": "lines"},
                   {"vertical_strategy": "text", "horizontal_strategy": "lines"},
                   {"strategy": "text"}):
            try:
                tabs = [t for t in page.find_tables(clip=r, **kw).tables if t.col_count >= 2]
            except Exception:
                continue
            if not tabs:
                continue
            ncol = collections.Counter(t.col_count for t in tabs).most_common(1)[0][0]
            ncol = max(ncol, max(t.col_count for t in tabs if t.row_count >= 3) if any(
                t.row_count >= 3 for t in tabs) else ncol)
            parts = sorted([t for t in tabs if t.col_count == ncol], key=lambda t: t.bbox[1])
            rows = []
            for t in parts:
                rows += [[(c or "").strip() for c in row] for row in t.extract()]
            if len(rows) < 2:
                continue
            filled = sum(1 for row in rows for c in row if c)
            if filled < 0.35 * sum(len(row) for row in rows):
                continue
            y0, y1 = min(t.bbox[1] for t in parts), max(t.bbox[3] for t in parts)
            cover = (y1 - y0) / max(1.0, r.height)
            score = filled * min(1.0, cover + 0.15)
            if best is None or score > best[0]:
                best = (score, rows, cover, Rect(r.x0, y0, r.x1, y1))
            if best[2] > 0.75:
                break
        if not best:
            return None
        # trust the parse only if it kept (nearly) every word printed in the table
        words = collections.Counter(re.findall(r"\w+", " ".join(
            l.text for l in self.pages[page.number] if l.horizontal and inside(best[3], center(l.bbox), 1)
            and self._anchor(l) is None)))
        got = collections.Counter(re.findall(r"\w+", " ".join(c for row in best[1] for c in row)))
        if words and sum((words & got).values()) < 0.9 * sum(words.values()):
            return None
        return best[1]

    def _match_across_pages(self, orphans, unmatched):
        used = set()
        for pno, l, n in unmatched:
            best = None
            for i, (opno, fc, boxes) in enumerate(orphans):
                if i in used or abs(opno - pno) > 1:
                    continue
                if best is None or area(fc[0]) > area(orphans[best][1][0]):
                    best = i
            if best is not None:
                used.add(best)
                opno, fc, boxes = orphans[best]
                fl = Float("fig", n, opno, [fc[0]])
                fl.anchor = l
                fc[1] = fl
                self._finish_regions(fl, self.pages[opno], self._anchor_ids(opno))
                self._place_in_box_or_page(fl, boxes, self.page_floats[opno])
            else:
                r = self._region_above(l)
                if r is not None:
                    fl = Float("fig", n, pno, [r])
                    fl.anchor = l
                    self.page_floats[pno].append(fl)
                else:
                    self.warnings.append(f"Figure {n}: caption found but no graphics; kept caption as text")
        for i, (opno, fc, boxes) in enumerate(orphans):
            if i in used:
                continue
            if area(fc[0]) >= 12000:
                fl = Float("fig", None, opno, [fc[0]])
                fc[1] = fl
                self._finish_regions(fl, self.pages[opno], self._anchor_ids(opno))
                self._place_in_box_or_page(fl, boxes, self.page_floats[opno])
            else:
                for x in self.pages[opno]:
                    if x.owner is fc:
                        x.role, x.owner = None, None
        for ls in self.pages:
            for x in ls:
                if x.role == "fig" and isinstance(x.owner, list):
                    x.owner = x.owner[1]

    def _anchor_ids(self, pno):
        return {id(l) for l in self.pages[pno] if self._anchor(l)}

    def _region_above(self, cap):
        """Fallback when a caption has no detectable graphics: crop the gap above it."""
        ls = [l for l in self.pages[cap.pno] if l.role is None and l.bbox.y1 <= cap.bbox.y0
              and min(l.bbox.x1, cap.bbox.x1) - max(l.bbox.x0, cap.bbox.x0) > 0]
        top = max((l.bbox.y1 for l in ls), default=self.content.y0)
        if cap.bbox.y0 - top < 50:
            return None
        x0 = min(cap.bbox.x0, self.content.x0) if cap.bbox.width > 0.55 * self.content.width else cap.bbox.x0
        return Rect(x0, top + 2, max(cap.bbox.x1, x0 + 50), cap.bbox.y0 - 2)

    def _all_floats(self, within=None):
        """Every float: from the page lists (during analysis) or from a block list (after)."""
        out = []
        if within is None:
            stack = [f for fl in self.page_floats for f in fl]
            while stack:
                f = stack.pop(0)
                out.append(f)
                stack = f.children + stack
            return out
        for b in within:
            if isinstance(b, Float):
                out.append(b)
                out += self._all_floats(b.blocks)
        return out

    # ----- reading order -----------------------------------------------------
    def _separators(self, pno, floats):
        ys = []
        for d in self.drawings[pno]:
            r = Rect(d["rect"])
            if rkey(r) in self.deco or r.height > 1.6 or r.width < 0.9 * self.content.width:
                continue
            c = center(r)
            if any(inside(f.bbox, c, -2) for f in floats):
                continue
            ys.append(c[1])
        return sorted(ys)

    def _order_page(self, pno):
        floats = self.page_floats[pno]
        for l in self.pages[pno]:
            if l.role is None and not l.horizontal:
                l.role = "drop"
        elems = [Elem("line", l, l.bbox) for l in self.pages[pno] if l.role is None and l.horizontal]
        elems += [Elem("float", f, f.bbox) for f in floats]
        ys = self._separators(pno, floats)
        bands = [[] for _ in range(len(ys) + 1)]
        for e in elems:
            bands[bisect.bisect(ys, (e.bbox.y0 + e.bbox.y1) / 2)].append(e)
        leaves = []
        for band in bands:
            if not band:
                continue
            aside = self._aside(band, pno) if ys else None
            if aside is not None:
                leaves.append(Leaf([Elem("float", aside, aside.bbox)]))
            else:
                leaves += xycut(band)
        return leaves

    def _leaf_stream(self, leaf, pno):
        """Elements of a column in order; small lines at the foot of the column are
        footnotes and travel as a note instead of interrupting the paragraph."""
        out = [(e.kind, e.obj, leaf) for e in leaf.elems]
        H = urect(self.doc[pno]).height
        k = len(out)
        while k > 0 and out[k - 1][0] == "line":
            l = out[k - 1][1]
            if l.size <= 0.9 * self.style.size and l.bbox.y0 > 0.6 * H and not self._anchor(l):
                k -= 1
            else:
                break
        tail = [o[1] for o in out[k:]]
        if tail and k > 0 and out[k - 1][0] == "line":
            above = out[k - 1][1]
            first = tail[0]
            starts_note = re.match(r"^\s*([*∗†‡§¶#]|\d{1,2}\b|[a-z]\b)", first.text) or \
                any(s.flags & 1 for s in first.spans[:2])
            if self.style.is_body(above) and (first.bbox.y0 - above.bbox.y1 > 3 or starts_note) and len(tail) <= 12:
                note = Float("note", None, pno, [union(first.bbox, tail[-1].bbox)])
                note.lines = tail
                for l in tail:
                    l.role, l.owner = "in", note
                self.page_floats[pno].append(note)
                return out[:k] + [("float", note, leaf)]
        return out

    def _aside(self, band, pno):
        top = min(band, key=lambda e: (e.bbox.y0, e.bbox.x0))
        if top.kind != "line" or top.obj.heavy < 0.85 or not ASIDE_RE.match(top.obj.text):
            return None
        a = Float("aside", None, pno, [e.bbox for e in band])
        a.title = top.obj.text
        for e in band:
            if e.kind == "line":
                e.obj.role, e.obj.owner = "in", a
            else:
                a.children.append(e.obj)
        self.page_floats[pno] = [f for f in self.page_floats[pno] if f not in a.children] + [a]
        return a

    def _assemble_captions(self, stream):
        out, i = [], 0
        while i < len(stream):
            kind, obj, leaf = stream[i]
            if kind == "line":
                a = self._anchor(obj)
                fl = self.float_index_get(a) if a and a[0] in ("fig", "table") else None
                if fl is not None and not fl.caption and fl.anchor is obj:
                    caps, j = [obj], i + 1
                    ref = obj
                    if len(obj.text) <= 14 and j < len(stream) and stream[j][0] == "line" \
                            and not self._anchor(stream[j][1]) and stream[j][1].bbox.y0 - obj.bbox.y1 < 2 * obj.size:
                        ref = stream[j][1]       # label on its own line: follow the text style
                    body_like = self.style.is_body(ref)
                    while j < len(stream):
                        k2, o2, lf2 = stream[j]
                        if k2 != "line" or self._anchor(o2):
                            break
                        if (o2.fam != ref.fam and not self._compatible_font(ref, o2)) or abs(o2.size - ref.size) > 0.45:
                            break
                        if body_like and (lf2 is not leaf or o2.bbox.y0 - caps[-1].bbox.y1 > 0.6 * o2.bbox.height):
                            break
                        caps.append(o2)
                        j += 1
                    fl.caption = caps
                    i = j
                    continue
            out.append(stream[i])
            i += 1
        return out

    def float_index_get(self, a):
        if not self.float_index:
            for f in self._all_floats():
                if f.num and (f.kind, f.num) not in self.float_index:
                    self.float_index[(f.kind, f.num)] = f
        return self.float_index.get(a)

    # ----- paragraphs ----------------------------------------------------------
    @staticmethod
    def _compatible_font(a, b):
        fams = {family(s.font) for s in a.spans if s.text.strip()}
        share = sum(len(s.text) for s in b.spans if family(s.font) in fams) / max(1, len(b.text))
        return share >= 0.25

    def _compatible(self, P, l):
        fam, sc, heavy = P.key
        if (l.heavy >= 0.85) != heavy:
            return False
        if abs(self.style.size_class(l.fam, l.size) - sc) > 0.35 and abs(l.size - P.lines[-1].size) > 0.35:
            return False
        if l.fam == fam:
            return True
        share = sum(len(s.text) for s in l.spans if family(s.font) == fam) / max(1, len(l.text))
        return share >= 0.25

    def _starts_new(self, P, l, leaf):
        if P is None or not self._compatible(P, l) or BULLET_RE.match(l.text):
            return True
        pl, pleaf = P.lines[-1], P.leaves[-1]
        if REFS_H.match(bare(l.text)) or REFS_H.match(bare(pl.text)) or \
                (TOP_H.match(bare(l.text)) and (l.heavy >= 0.85 or l.text.isupper())):
            return True
        if P.bullet and l.bbox.x0 > P.lines[0].bbox.x0 + 2:
            return False
        lh = max(l.bbox.height, l.size)
        indent = l.bbox.x0 - (pl.bbox.x0 if leaf is pleaf else leaf.x0mode)
        if 0.6 * l.size <= indent <= 6 * l.size:
            return True
        short_prev = pleaf.justified and pl.bbox.x1 < pleaf.x1 - max(10, 0.1 * (pleaf.x1 - pleaf.x0)) \
            and TERMINAL_RE.search(pl.text)
        if leaf is pleaf:
            if leaf.pitch:
                wide = l.base - pl.base > 1.4 * leaf.pitch
            else:
                wide = l.bbox.y0 - pl.bbox.y1 > 0.6 * lh
            return bool(wide or short_prev)
        if short_prev:
            return True
        if not pleaf.justified and TERMINAL_RE.search(pl.text) and l.text[:1].isupper():
            return True
        return False

    def _build_blocks(self, stream):
        blocks, pending, P = [], [], None
        for kind, obj, leaf in stream:
            if kind == "float" and obj.kind == "eq":
                if P is not None:
                    blocks.append(P)
                blocks += pending + [obj]
                pending, P = [], None
                continue
            if kind == "float":
                (pending if P is not None else blocks).append(obj)
                continue
            if not self._starts_new(P, obj, leaf):
                P.add(obj, leaf)
                continue
            if P is not None:
                blocks.append(P)
            blocks += pending
            pending = []
            P = Para(obj, leaf, self.style.key(obj))
        if P is not None:
            blocks.append(P)
        blocks += pending
        for b in blocks:
            if isinstance(b, Para):
                b.kind = self._classify(b)
        return blocks

    def _classify(self, P):
        txt, st = P.text, self.style
        heavy = all(l.heavy >= 0.85 for l in P.lines)
        size = P.size
        bare = re.sub(r"^[\dIVX.\s]+(?=[A-Za-z])", "", txt).strip()
        if (TOP_H.match(bare) or REFS_H.match(bare)) and len(P.lines) <= 2 and \
                (heavy or txt.isupper() or size >= 0.9 * st.size):
            return "h"
        if len(re.findall(r"[A-Za-z]", txt)) < 3:
            return "small" if size < 0.86 * st.size else "p"
        if len(txt) <= 220 and len(P.lines) <= 4 and not (txt.endswith(".") and len(txt) > 90):
            if (heavy and size >= 0.92 * st.size) or (size >= 1.3 * st.size and len(txt) <= 160):
                return "h"
        if size < 0.86 * st.size:
            return "small"
        return "p"

    def _build_inner(self, f):
        lines = [l for ls in self.pages for l in ls if l.owner is f and l.horizontal]
        elems = [Elem("line", l, l.bbox) for l in lines] + [Elem("float", c, c.bbox) for c in f.children]
        stream = []
        for leaf in xycut(elems):
            stream += [(e.kind, e.obj, leaf) for e in leaf.elems]
        stream = self._assemble_captions(stream)
        f.blocks = self._build_blocks(stream)
        if f.blocks and isinstance(f.blocks[0], Para) and f.blocks[0].kind in ("h", "p"):
            f.title = f.blocks.pop(0)
        self._terms(f.blocks)

    # ----- clean-up ------------------------------------------------------------
    def _postprocess(self):
        B, st = self.blocks, self.style
        # merge "Box N (continued)" into the first part
        seen = {}
        for f in self._all_floats(B):
            if f.kind == "box" and f.num:
                if f.num in seen and f.cont:
                    seen[f.num].blocks += f.blocks
                    f.kind = "gone"
                else:
                    seen.setdefault(f.num, f)
        B = [b for b in B if not (isinstance(b, Float) and b.kind == "gone")]

        # tables continued over several pages
        first_tab = {}
        for f in [b for b in B if isinstance(b, Float) and b.kind == "table" and b.num]:
            if f.num in first_tab:
                t0 = first_tab[f.num]
                t0.parts = getattr(t0, "parts", None) or [(t0.pno, r) for r in t0.regions]
                t0.parts += [(f.pno, r) for r in f.regions]
                if t0.rows and f.rows:
                    extra = f.rows[1:] if [c.strip() for c in f.rows[0]] == [c.strip() for c in t0.rows[0]] else f.rows
                    t0.rows = t0.rows + extra
                else:
                    t0.rows = None          # one part unreadable: show the page images only
                f.kind = "gone"
            else:
                first_tab[f.num] = f
        B = [b for b in B if not (isinstance(b, Float) and b.kind == "gone")]

        # title and byline
        p0h = [b for b in B if isinstance(b, Para) and b.kind == "h" and b.pno == 0]
        if self.title_lines:
            for b in p0h:
                if any(l in self.title_lines for l in b.lines):
                    b.kind = "title"
                    break
            else:      # a body-size title (from the PDF's metadata) that became an ordinary paragraph
                for b in B:
                    if isinstance(b, Para) and b.pno == 0 and b.lines and all(l in self.title_lines for l in b.lines):
                        b.kind = "title"
                        break
        for b in B:
            if isinstance(b, Para) and b.kind == "title" and b.text.isupper() and \
                    re.sub(r"\W+", "", b.text.lower()) == re.sub(r"\W+", "", (self.meta["title"] or "").lower()):
                b.label = self.meta["title"]      # set in capitals: show the PDF's own title casing
        # page-1 sidebars that precede the title (citation, editor, dates, funding...)
        # are moved to the end as "Article information"
        ti = next((i for i, b in enumerate(B) if isinstance(b, Para) and b.kind == "title"), 0)
        pre = [b for b in B[:ti] if isinstance(b, Para) and b.pno == 0]
        self.article_info = pre if sum(len(b.text) for b in pre) > 80 else []
        # affiliations, correspondence and dates printed in the first page's text flow
        hits = [b for b in B[ti + 1:] if isinstance(b, Para) and b.pno == 0 and b.kind in ("p", "small", "term")
                and len(b.text) < 1200 and AFFIL_RE.match(b.text)]
        # a narrow page-1 sidebar (PLOS, Frontiers) holding several of these: take the whole column,
        # including statements that continue in a second paragraph ("...Award (to V.P.C.). The funders...")
        side = collections.Counter()
        for b in hits + [b for b in self.article_info if AFFIL_RE.match(b.text)]:
            for lf in {(round(lf.x0), round(lf.x1)) for lf in b.leaves or []}:
                if lf[1] - lf[0] < 0.42 * self.content.width:
                    side[lf] += 1
        spans = [k for k, n in side.items() if n >= 2]

        def in_side(b, tol=14):
            return bool(b.leaves) and all(any(abs(lf.x0 - a) < 6 and abs(lf.x1 - c) < tol for a, c in spans)
                                          for lf in b.leaves)
        if spans:
            hits += [b for b in B[ti + 1:] if isinstance(b, Para) and b.pno == 0 and b not in hits and b.kind != "title"
                     and in_side(b) and not TOP_H.match(bare(b.text))]
            # the same sidebar column often runs over onto page 2 in small type
            hits += [b for b in B[ti + 1:] if isinstance(b, Para) and b.pno == 1 and b not in hits and b.kind == "small"
                     and in_side(b)]
        self.article_info += hits
        # affiliation markers left alone on a line under the authors ("1,2*")
        for b in B[ti + 1:]:
            if isinstance(b, Para) and b.pno == 0 and re.fullmatch(r"[\d,\s*†‡§¶#]{1,12}", b.text) and b not in self.article_info:
                for l in b.lines:
                    l.role = "drop"
        B = [b for b in B if not (isinstance(b, Para) and b.lines and all(l.role == "drop" for l in b.lines))]
        if self.article_info:
            B = [b for b in B if b not in self.article_info]
            # a sentence that was interrupted by the sidebar continues in the next paragraph
            joined = []
            for b in B:
                prev = joined[-1] if joined else None
                if isinstance(b, Para) and isinstance(prev, Para) and b.kind == prev.kind == "p" and b.lines and prev.lines \
                        and not TERMINAL_RE.search(prev.text.rstrip()) and re.match(r"[a-z]", b.text):
                    prev.lines += b.lines
                    prev.leaves += b.leaves
                    continue
                joined.append(b)
            B = joined
        heads = [b for b in B if isinstance(b, Para) and b.kind == "h"]

        # drop table-of-contents sidebars (duplicates of real headings)
        norm = lambda t: re.sub(r"[^a-z0-9]+", "", t.lower())
        best = {}
        for h in heads:
            k = norm(h.text)
            if k not in best or h.size >= best[k].size - 0.05:
                best[k] = h
        drop = {id(h) for h in heads if best[norm(h.text)] is not h}
        for b in B:
            if id(b) in drop:
                for l in b.lines:
                    l.role = "drop"
        B = [b for b in B if id(b) not in drop]
        for i, b in enumerate(B):
            if isinstance(b, Para) and b.kind == "h" and TOC_H.match(b.text.strip()):
                nxt = B[i + 1] if i + 1 < len(B) else None
                if nxt is None or (isinstance(nxt, Para) and nxt.kind in ("h", "title")) or b.pno == 0:
                    drop.add(id(b))
                    for l in b.lines:
                        l.role = "drop"
        B = [b for b in B if id(b) not in drop]
        heads = [b for b in B if isinstance(b, Para) and b.kind == "h"]

        # pull quotes repeat a sentence of the body in large type: drop them
        bodies = [(id(b), re.sub(r"[^a-z]+", "", b.text.lower())) for b in B
                  if isinstance(b, Para) and b.kind == "p" and len(b.text) > 200 and b.size < 1.1 * st.size]

        def pull_quote(b):
            if not (isinstance(b, Para) and b.kind in ("h", "p") and 40 <= len(b.text) <= 300
                    and len(b.text.split()) >= 7 and b.size >= 1.15 * st.size):
                return False
            k = re.sub(r"[^a-z]+", "", b.text.lower())
            return any(k in t for i, t in bodies if i != id(b))
        B = [b for b in B if not pull_quote(b)]
        heads = [b for b in B if isinstance(b, Para) and b.kind == "h"]

        # short bold lines between the title and the first section heading are bylines
        try:
            t = next(i for i, b in enumerate(B) if isinstance(b, Para) and b.kind == "title")
        except StopIteration:
            t = -1
        if t >= 0:
            biggest = max((h.size for h in heads if h.pno > 0), default=0)
            for b in B[t + 1:]:
                if isinstance(b, Para) and b.kind == "h":
                    if TOP_H.match(bare(b.text)) or b.pno > 0 or b.size >= biggest - 0.3:
                        break
                    b.kind = "byline"
            # a plain line of names right under the title ("TIAN LAN", "A. Smith, B. Jones")
            nxt = B[t + 1] if t + 1 < len(B) else None
            if isinstance(nxt, Para) and nxt.kind in ("p", "term", "small") and nxt.pno == 0 and looks_like_names(nxt.text):
                nxt.kind = "byline"
        heads = [b for b in B if isinstance(b, Para) and b.kind == "h"]

        # heading levels: canonical section names are level 2, others ranked by size
        sizes = sorted({round(h.size, 1) for h in heads if not TOP_H.match(bare(h.text))}, reverse=True)
        ranks = []
        for s in sizes:
            if not ranks or ranks[-1] - s > 0.3:
                ranks.append(s)
        for h in heads:
            if TOP_H.match(bare(h.text)):
                h.level = 2
            else:
                h.level = 2 + min(2, sum(1 for r in ranks if r - h.size > 0.3))
        by = next((b for b in B if isinstance(b, Para) and b.kind == "byline"), None)
        if by:
            first = re.split(r",|;|&|\band\b|[∗*†‡§¶#]|\d", by.text)[0].strip()
            if first and len(first.split()) <= 5:
                last = first.split()[-1]
                self.meta["first_author"] = last.title() if last.isupper() and len(last) > 2 else last
            names = re.sub(r"[\d∗*†‡§¶#]+", " ", by.text)
            names = re.sub(r"\s*,(\s*,)+", ",", re.sub(r"\s+", " ", names))
            self.meta["authors"] = re.sub(r"\s+,", ",", names).strip(" ,")
        if not self.meta.get("authors"):
            a = re.sub(r"\s+", " ", (self.doc.metadata or {}).get("author") or "").strip()
            if re.search(r"[A-Za-z]{2,}\s+[A-Za-z]", a) and not re.search(r"@|\.com|^(author|user|admin)", a, re.I):
                self.meta["authors"] = re.sub(r"\s*;\s*", ", ", a)
                first = re.split(r",|;|\band\b", a)[0].split()
                if first and not self.meta.get("first_author"):
                    self.meta["first_author"] = first[-1]
        if not self.meta["title"]:
            self.meta["title"] = self.path.stem

        # references: a "References" heading; a run-in "REFERENCES: ..." paragraph; or,
        # with no heading at all (revtex), the run of entries numbered [1], [2], [3]...
        B = self._locate_refs(B)
        idx = next((i for i, b in enumerate(B) if isinstance(b, Para) and b.kind == "h"
                    and REFS_H.match(bare(b.text))), None)
        self.refs = None
        if idx is not None:
            j = idx + 1
            while j < len(B) and not (isinstance(B[j], Para) and B[j].kind in ("h", "title")):
                j += 1
            seg = B[idx + 1:j]
            ref_lines = [l for b in seg if isinstance(b, Para) for l in b.lines]
            floats_in = [b for b in seg if isinstance(b, Float)]
            self.refs = self._parse_refs(ref_lines, [b for b in seg if isinstance(b, Para)])
            B = B[:idx + 1] + [self.refs] + floats_in + B[j:]
        else:
            self.warnings.append("No reference list found; citations are not linked")

        # glossary-type asides go just before the references
        gl = [b for b in B if isinstance(b, Float) and b.kind == "aside" and isinstance(b.title, Para)
              and re.match(r"glossary|abbreviations", b.title.text, re.I)]
        if gl:
            B = [b for b in B if b not in gl]
            at = next((i for i, b in enumerate(B) if isinstance(b, Para) and b.kind == "h"
                       and REFS_H.match(bare(b.text))), len(B))
            B = B[:at] + gl + B[at:]

        if self.article_info:
            info_h = Para(self.article_info[0].lines[0], self.article_info[0].leaves[0], None)
            info_h.kind, info_h.level, info_h.lines = "h", 2, []
            info_h.label = "Article information"
            for b in self.article_info:
                if b.kind in ("h", "title", "byline"):
                    b.kind = "p"
            B = B + [info_h] + self.article_info

        self._terms(B)
        if self.placement == "first-mention":
            B = self._place_after_mentions(B)
        self.blocks = B
        self._assign_ids()

    def _heading(self, text, like):
        h = Para(like.lines[0], like.leaves[0], None)
        h.lines, h.leaves, h.kind, h.level, h.label = [], [], "h", 2, text
        return h

    def _locate_refs(self, B):
        if any(isinstance(b, Para) and b.kind == "h" and REFS_H.match(bare(b.text)) for b in B):
            return B
        half = len(self.doc) * 0.4
        # run-in heading: "REFERENCES: Adams, F. C. 2010, ..."
        for i, b in enumerate(B):
            if isinstance(b, Para) and b.kind in ("p", "small") and b.pno >= half:
                m = re.match(r"^\s*(references|bibliography|literature cited)\s*[:.]?\s+(?=\S)", b.text, re.I)
                if m:
                    first = b.lines[0]
                    drop = len(m.group(0))
                    for s in first.spans:          # remove the label from the first line
                        if drop <= 0:
                            break
                        k = min(len(s.text), drop)
                        s.text, drop = s.text[k:], drop - k
                    first.text = re.sub(r"\s+", " ", "".join(s.text for s in first.spans)).strip()
                    return B[:i] + [self._heading("References", b)] + B[i:]
        # no heading: the longest run of [1], [2], ... entries after the first 40% of the paper
        rx = re.compile(r"^\s*\[(\d{1,4})\]\s+\S")
        for i, b in enumerate(B):
            if not (isinstance(b, Para) and b.pno >= half):
                continue
            for k, l in enumerate(b.lines):
                m = rx.match(l.text)
                if m and m.group(1) == "1":
                    nums, n = [], 1
                    for bb in B[i:]:
                        if isinstance(bb, Para):
                            for ll in bb.lines:
                                mm = rx.match(ll.text)
                                if mm and int(mm.group(1)) == n + 1:
                                    n += 1
                    if n >= 5:
                        if k > 0:                  # entry 1 sits inside a paragraph: split it
                            tail = Para(b.lines[k], b.leaves[k], b.key)
                            tail.lines, tail.leaves, tail.kind = b.lines[k:], b.leaves[k:], b.kind
                            b.lines, b.leaves = b.lines[:k], b.leaves[:k]
                            B = B[:i + 1] + [tail] + B[i + 1:]
                            i += 1
                        return B[:i] + [self._heading("References", b)] + B[i:]
        return B

    def _terms(self, B):
        for i, b in enumerate(B[:-1]):
            n = B[i + 1]
            if isinstance(b, Para) and b.kind in ("p", "small") and isinstance(n, Para) \
                    and n.kind in ("p", "small") and len(b.lines) <= 2 and len(b.text) <= 80 \
                    and not TERMINAL_RE.search(b.text) and n.size <= b.size - 0.6 and not self.style.is_body(b.lines[0]):
                b.kind = "term"

    def _parse_refs(self, lines, paras):
        """Numbered lists: every line that opens with an unused plausible number starts an
        entry (tolerates entries met slightly out of order); otherwise author-year."""
        entries, used = [], set()
        for l in lines:
            m = REFNUM_RE.match(l.text)
            if m:
                n, rest = int(m.group(1)), l.text[m.end():]
                plausible = n not in used and 1 <= n <= len(used) + 25 and (rest[:1].isalpha() or rest[:1] in "‘“'\"")
                if plausible:
                    entries.append([n, [l], []])
                    used.add(n)
                    continue
            if not entries:
                continue
            if l.heavy >= 0.85 and l.size <= self.style.size:
                entries[-1][2].append(l)
            else:
                entries[-1][1].append(l)
        if len(entries) >= 3 and len(used) >= 0.8 * max(used):
            entries.sort(key=lambda e: e[0])
            return RefList(entries, True)
        return RefList(self._split_unnumbered(paras), False)

    def _split_unnumbered(self, paras):
        """Author-year reference lists: one entry per hanging indent, per gap, or per
        'Surname, A.' line that follows a finished entry."""
        pairs = [(l, lf) for p in paras for l, lf in zip(p.lines, p.leaves)]
        if not pairs:
            return []
        rel = [l.bbox.x0 - lf.x0 for l, lf in pairs]
        hanging = sum(1 for r in rel if 3 < r < 40) >= 0.2 * len(pairs) and sum(1 for r in rel if r < 1.5) >= 3
        start_rx = re.compile(r"^(?:(?:van|von|de|da|del|der|le|la|di|du|dos)\s+)*[A-ZÀ-Ý][\w'’\-]+"
                              r"(?:[\s\-][A-ZÀ-Ý][\w'’\-]+)?"
                              r"(?:,|\s+[A-Z]{1,3}[,.\s]|\s+(?:[A-Z]\.\s?-?){1,3})")
        end_rx = re.compile(r"([.)\]]|\b(19|20)\d{2}[a-z]?\.?|\S/\S+|\d)\s*$")
        refs = []
        for i, (l, lf) in enumerate(pairs):
            if i == 0:
                new = True
            elif hanging:
                pl = pairs[i - 1][0]
                new = rel[i] < 1.5 and bool(start_rx.match(l.text) or
                                            (pl.text.rstrip().endswith(".") and l.text[:1].isupper()))
            else:
                pl, plf = pairs[i - 1]
                gap = lf is plf and lf.pitch and l.base - pl.base > 1.35 * lf.pitch
                new = bool(gap or (start_rx.match(l.text) and end_rx.search(pl.text)))
            if not new and refs and l.heavy >= 0.85 and l.size <= self.style.size:
                refs[-1][2].append(l)
                continue
            if new or not refs:
                refs.append([len(refs) + 1, [l], []])
            else:
                refs[-1][1].append(l)
        return refs

    def ref_index(self):
        """(surname, year) -> reference number, for linking author-year citations."""
        idx = {}
        if not self.refs or self.refs.numbered:
            return idx
        for n, lines, _ in self.refs.refs:
            t = " ".join(l.text for l in lines)
            m = re.match(r"\s*((?:(?:van|von|de|da|del|der|le|la|di)\s+)*[^\s,.(]+)", t)
            y = re.search(r"\b((?:19|20)\d{2})([a-z]?)\b", t)
            if m and y:
                name = m.group(1).lower()
                idx.setdefault((name, y.group(1) + y.group(2)), n)
                idx.setdefault((name, y.group(1)), n)
        return idx

    def _place_after_mentions(self, B):
        movable = [b for b in B if isinstance(b, Float) and b.kind in ("fig", "table", "box") and b.num]
        for i, b in enumerate(B):
            if isinstance(b, Float):
                b.order = i
        start = next((i for i, b in enumerate(B) if isinstance(b, Para) and b.kind == "h" and b.level == 2
                      and not re.match(r"abstract|summary", bare(b.text), re.I)), 0)
        end = next((i for i, b in enumerate(B) if isinstance(b, RefList)), len(B))
        first = {}
        for i in range(start, end):
            b = B[i]
            if isinstance(b, Para) and b.kind in ("p", "small"):
                for m in MENTION_LIST.finditer(b.text):          # "Figs 2 and 3", "Figs 2-4", "Fig 2A"
                    w = m.group(1).lower()
                    kind = "fig" if w.startswith("fig") else "table" if w.startswith("tab") else "box"
                    nums = re.findall(r"(S?)(\d{1,2})[A-Za-z]?", m.group(2), re.I)
                    seps = re.findall(r"(?:,|and|&|–|-|to)", m.group(2))
                    found = [(pre.upper(), int(n)) for pre, n in nums]
                    for k, (pre, n) in enumerate(found):
                        first.setdefault((kind, f"{pre}{n}"), i)
                        if k and k - 1 < len(seps) and seps[k - 1] in ("–", "-", "to") and found[k - 1][0] == pre:
                            for x in range(found[k - 1][1] + 1, n):
                                first.setdefault((kind, f"{pre}{x}"), i)
        def numkey(f):
            m = re.match(r"(S?)(\d+)([A-Za-z]?)", f.num or "", re.I)
            return (bool(m and m.group(1)), int(m.group(2)) if m else 0, (m.group(3) or "").lower() if m else "")

        after = collections.defaultdict(list)
        movers = set()
        for kind in ("fig", "table", "box"):
            # numbering follows first citation, so a passing early mention ("see Fig 3" in the
            # Methods) must not pull Fig 3 ahead of Fig 2: each figure goes no earlier than the one before
            floor = -1
            for f in sorted((f for f in movable if f.kind == kind), key=numkey):
                i = first.get((f.kind, f.num), f.order)     # never mentioned: keep its page position
                i = max(i, floor)
                floor = i
                after[i].append(f)
                movers.add(id(f))
        out = []
        for i, b in enumerate(B):
            if id(b) not in movers:
                out.append(b)
            out += sorted(after.get(i, []), key=lambda f: (f.kind != "fig", numkey(f)))
        return out

    def _assign_ids(self):
        used = set()
        unnamed = 0
        for f in self._all_floats(self.blocks):
            if f.kind in ("eq", "note"):
                unnamed += 1
                f.id = f"{f.kind}-{unnamed}"
            elif f.kind in ("fig", "table", "box"):
                if f.num and f"{f.kind}-{f.num}" not in used:
                    f.id = f"{f.kind}-{f.num}"
                else:
                    unnamed += 1
                    f.id = f"{f.kind}-x{unnamed}"
            else:
                unnamed += 1
                f.id = f"aside-{unnamed}"
            used.add(f.id)
        for b in self._walk(self.blocks):
            if isinstance(b, Para) and b.kind in ("h", "title"):
                b.id = slug(b.text, used)

    def _walk(self, blocks):
        for b in blocks:
            yield b
            if isinstance(b, Float):
                if b.title is not None and isinstance(b.title, Para):
                    yield b.title
                yield from self._walk(b.blocks)

    # ----- images ----------------------------------------------------------------
    def render_images(self):
        for f in self._all_floats(self.blocks):
            big = f.kind == "table" and (not f.rows or max(len(r) for r in f.rows) >= 5 or len(f.rows) > 15)
            if f.kind in ("fig", "eq") or big:
                f.images = []
                parts = getattr(f, "parts", None) or [(f.pno, r) for r in f.regions]
                for k, (pno, r) in enumerate(parts):
                    name = f"{f.id}{'' if k == 0 else chr(97 + k)}"
                    f.images.append(self._crop(pno, r, name, getattr(f, "rotate", 0)))
        self.images["cover.jpg"] = (self.make_cover(), "image/jpeg")

    def make_cover(self):
        """A library cover: the paper's lead figure on white above the title, so papers are
        recognisable at a glance in Apple Books instead of a row of identical first pages."""
        W, H, M = 400, 600, 30
        doc = pymupdf.open()
        page = doc.new_page(width=W, height=H)
        ink, paper = (0.15, 0.137, 0.37), (0.992, 0.992, 0.988)
        figs = [f for f in self._all_floats(self.blocks) if f.kind == "fig" and getattr(f, "images", None)]
        split = 0.0
        if figs:
            lead = figs[0]
            data = self.images[lead.images[0]][0]
            pix = pymupdf.Pixmap(data)
            split = H * 0.54
            page.draw_rect(pymupdf.Rect(0, 0, W, split), color=None, fill=paper)
            box = pymupdf.Rect(M, M, W - M, split - M * 0.8)
            k = min(box.width / pix.width, box.height / pix.height)
            w, h = pix.width * k, pix.height * k
            r = pymupdf.Rect(box.x0 + (box.width - w) / 2, box.y0 + (box.height - h) / 2, 0, 0)
            r.x1, r.y1 = r.x0 + w, r.y0 + h
            page.insert_image(r, stream=data)
        page.draw_rect(pymupdf.Rect(0, split, W, H), color=None, fill=ink)
        m = self.meta
        title = m.get("title") or self.path.stem
        names = [n.strip() for n in re.split(r",|;|\band\b|&", m.get("authors") or "") if n.strip()]
        authors = ", ".join(names[:3]) + (" et al." if len(names) > 3 else "")
        venue = ", ".join(x for x in (m.get("journal"), m.get("year")) if x)
        size = 30 if len(title) <= 50 else 26 if len(title) <= 90 else 22 if len(title) <= 140 else 18
        top = split + 30 if figs else 120
        css = (f"* {{margin:0; padding:0}} .v {{font-family: sans-serif; font-size: 10.5px; color: #B9B6F0; margin-bottom: 12px}}"
               f" h1 {{font-family: serif; font-weight: bold; font-size: {size}px; line-height: 1.12; color: #FFFFFF}}"
               f" .a {{font-family: sans-serif; font-size: 11px; color: #DAD8F7; margin-top: 14px}}")
        body = (f'<p class="v">{esc(venue)}</p>' if venue else "") + f"<h1>{esc(title)}</h1>" + \
               (f'<p class="a">{esc(authors)}</p>' if authors else "")
        try:
            page.insert_htmlbox(pymupdf.Rect(M, top, W - M, H - M), body, css=css, scale_low=0)
        except Exception:      # very old PyMuPDF: plain text
            page.insert_textbox(pymupdf.Rect(M, top, W - M, H - M), title, fontsize=size, color=(1, 1, 1))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(3, 3), alpha=False)
        return pix.tobytes("jpeg", jpg_quality=86)

    def _crop(self, pno, r, name, rotate=0):
        page = self.doc[pno]
        clip = Rect(r.x0 - 2.5, r.y0 - 2.5, r.x1 + 2.5, r.y1 + 2.5) & urect(page)
        c = self.content
        if not rotate:
            clip = Rect(max(clip.x0, c.x0 - 10), max(clip.y0, c.y0 - 2), min(clip.x1, c.x1 + 10), min(clip.y1, c.y1 + 6))
        for y in self._rules(pno):             # keep separator rules out of the picture
            if r.y1 - 1 <= y <= clip.y1:
                clip.y1 = y - 0.8
            elif clip.y0 <= y <= r.y0 + 1:
                clip.y0 = y + 0.8
        for l in self.pages[pno]:
            if l.role is not None or not touches(l.bbox, clip) or inside(r, center(l.bbox)):
                continue
            if l.bbox.y0 >= r.y1 - 1:
                clip.y1 = min(clip.y1, max(r.y1, l.bbox.y0 + 0.5))
            elif l.bbox.y1 <= r.y0 + 1:
                clip.y0 = max(clip.y0, min(r.y0, l.bbox.y1 - 0.5))
            elif l.bbox.x0 >= r.x1 - 1:
                clip.x1 = min(clip.x1, max(r.x1, l.bbox.x0))
            elif l.bbox.x1 <= r.x0 + 1:
                clip.x0 = max(clip.x0, min(r.x0, l.bbox.x1))
        zoom = self.zoom
        longest = max(clip.width, clip.height, 1)
        if longest * zoom > 3600:               # cap the long side; plenty for pinch-zoom
            zoom = 3600 / longest
        if clip.width < 2 or clip.height < 2:
            clip = Rect(clip.x0, clip.y0, clip.x0 + max(clip.width, 2), clip.y0 + max(clip.height, 2))
        mat = pymupdf.Matrix(zoom, zoom)
        if page.rotation:              # geometry is unrotated; the renderer wants display space
            clip = Rect(clip) * page.rotation_matrix
            clip.normalize()
        elif rotate:
            mat = mat.prerotate(rotate)
        pix = page.get_pixmap(matrix=mat, clip=clip, alpha=False)
        data, ext, mime = pix.tobytes("png"), "png", "image/png"
        if len(data) > 300_000:          # photographs and micrographs: JPEG is far smaller and looks the same
            jpg = pix.tobytes("jpeg", jpg_quality=88)
            if len(jpg) < 0.6 * len(data) or len(data) > 1_800_000:
                data, ext, mime = jpg, "jpg", "image/jpeg"
        fname = f"{name}.{ext}"
        self.images[fname] = (data, mime)
        return fname

    def summary(self):
        floats = self._all_floats(self.blocks)
        figs = [f.num for f in floats if f.kind == "fig"]
        tabs = [f.num for f in floats if f.kind == "table"]
        boxes = [f.num for f in floats if f.kind == "box"]
        nums = sorted(int(n) for n in figs if n and n.isdigit())
        if nums:
            missing = sorted(set(range(1, nums[-1] + 1)) - set(nums))
            if missing:
                self.warnings.append("Figure numbers not found: " + ", ".join(map(str, missing)))
        nonum = sum(1 for n in figs if not n)
        for f in floats:
            if f.kind == "fig" and f.num and not f.caption:
                self.warnings.append(f"Figure {f.num}: image kept but caption not found")
        parts = [f"figures {', '.join(n for n in figs if n) or 'none'}" + (f" (+{nonum} unlabelled)" if nonum else ""),
                 f"tables {', '.join(n or '?' for n in tabs) or 'none'}",
                 f"boxes {', '.join(n or '?' for n in boxes) or 'none'}",
                 f"references {len(self.refs.refs) if self.refs else 0}"]
        return "; ".join(parts)


# ---------------------------------------------------------------------------
# HTML / EPUB rendering
# ---------------------------------------------------------------------------

CSS_EPUB = """
body { line-height: 1.55; margin: 0 4%; widows: 2; orphans: 2; }
p { margin: 0 0 .8em; text-indent: 0; }
a { text-decoration: none; }
sup, sub { font-size: .66em; line-height: 0; }
h1, h2, h3, h4, h5, h6 { font-family: -apple-system, "Helvetica Neue", sans-serif; page-break-after: avoid; }
h2 { font-size: 1.15em; line-height: 1.3; margin: 2em 0 .6em; }
h3 { font-size: 1em; line-height: 1.35; margin: 1.6em 0 .5em; }
h4, h5, h6 { font-size: .92em; margin: 1.3em 0 .4em; }
.mast { margin: 0 0 2em; padding-bottom: 1.2em; border-bottom: 1px solid rgba(128,128,128,.35); }
.venue { font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .75em; opacity: .7; margin: 0 0 1.2em; }
h1.title { font-size: 1.7em; line-height: 1.15; margin: 0 0 .6em; font-weight: bold; }
.authors { font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .82em; margin: 0 0 .4em; }
.authors p.byline { margin: 0; }
.doi { font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .75em; opacity: .7; margin: 0; }
p.small { font-size: .9em; }
p.note { font-size: .8em; opacity: .8; border-top: 1px solid rgba(128,128,128,.3); padding-top: .4em; }
p.term { font-weight: bold; margin: 1em 0 .15em; }
figure { margin: 1.6em 0; padding: 0; }
figure.fig img, figure.orig img, .tbl > figure img { display: block; width: 100%; height: auto; margin: 0 0 .4em;
  background-color: #fff; page-break-inside: avoid; }
.cap { font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .8em; line-height: 1.45; margin: .5em 0 0; }
.cap .flabel { font-weight: bold; }
.cap .ftitle { font-weight: 600; }
.tbl { margin: 1.6em 0; }
.tbl > .cap { margin: 0 0 .7em; }
.tscroll { max-width: 100%; overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .78em; line-height: 1.35; }
th, td { text-align: left; vertical-align: top; padding: .4em .5em .4em 0; border-bottom: 1px solid rgba(128,128,128,.35); }
th { font-weight: bold; border-bottom: 1.5px solid rgba(128,128,128,.7); }
.card { border-top: 1px solid rgba(128,128,128,.4); padding: .6em 0 .3em; font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .9em; }
.card h4 { margin: 0 0 .35em; }
dl { margin: 0 0 .4em; }
dl.kv div { display: inline-block; vertical-align: top; min-width: 7em; margin: 0 1.2em .4em 0; }
dt { font-size: .8em; opacity: .65; }
dd { margin: 0; }
p.tgroup { font-weight: bold; margin: 1.2em 0 .2em; font-family: -apple-system, "Helvetica Neue", sans-serif; }
figure.orig figcaption { font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .75em; opacity: .65; }
.box { margin: 1.6em 0; padding: .8em 1em .3em; background-color: rgba(120,120,140,.12); border-radius: 10px; }
.box > h3 { margin-top: .1em; }
.box .flabel { opacity: .7; }
.glossary p.term { margin-top: .9em; }
.eq { text-align: center; margin: .7em 0 1em; }
.eq img { height: auto; max-width: 100%; }
ol.refs, ul.refs { font-size: .86em; line-height: 1.45; }
ol.refs { padding-left: 2.4em; }
ul.refs.ay { list-style: none; padding-left: 1.4em; text-indent: -1.4em; }
.refs li { margin-bottom: .65em; }
.annot { display: block; font-style: italic; margin-top: .2em; }
.rl { font-family: -apple-system, "Helvetica Neue", sans-serif; font-size: .82em; white-space: nowrap; }
.rl a { margin-right: .6em; }
"""

# <<READER
READER_CSS = r''':root{
  --paper:#FDFDFC;--ink:#1B1D23;--ink-2:#575D6B;--ink-3:#8B909B;--rule:#E6E7EB;
  --raised:#FFFFFF;--glass:rgba(253,253,252,.9);--sunken:#F2F3F5;--fill:rgba(118,120,134,.11);--plate:#FFFFFF;
  --accent:#4B45B8;--accent-soft:rgba(75,69,184,.10);--hl:rgba(214,51,108,.17);
  --shadow:0 12px 40px rgba(22,24,44,.16),0 1px 3px rgba(22,24,44,.08);
  --fs:18px;--lh:1.62;
  --serif:ui-serif,"New York",Charter,"Iowan Old Style","Palatino Linotype",Georgia,serif;
  --sans:ui-sans-serif,-apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --text:var(--serif);
  --ease:cubic-bezier(.32,.72,0,1);
  color-scheme:light;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]):not([data-theme="sepia"]){
    --paper:#0F1117;--ink:#E7E8EC;--ink-2:#A6ABB8;--ink-3:#737987;--rule:#262A34;
    --raised:#181B23;--glass:rgba(24,27,35,.9);--sunken:#1A1D25;--fill:rgba(160,166,190,.14);--plate:#F0F0F0;
    --accent:#A3ACFF;--accent-soft:rgba(163,172,255,.13);--hl:rgba(255,122,168,.22);
    --shadow:0 12px 40px rgba(0,0,0,.5),0 1px 3px rgba(0,0,0,.4);color-scheme:dark}
}
:root[data-theme="dark"]{
  --paper:#0F1117;--ink:#E7E8EC;--ink-2:#A6ABB8;--ink-3:#737987;--rule:#262A34;
  --raised:#181B23;--glass:rgba(24,27,35,.9);--sunken:#1A1D25;--fill:rgba(160,166,190,.14);--plate:#F0F0F0;
  --accent:#A3ACFF;--accent-soft:rgba(163,172,255,.13);--hl:rgba(255,122,168,.22);
  --shadow:0 12px 40px rgba(0,0,0,.5),0 1px 3px rgba(0,0,0,.4);color-scheme:dark}
:root[data-theme="sepia"]{
  --paper:#F5EFE3;--ink:#3A3027;--ink-2:#6D6051;--ink-3:#9A8C79;--rule:#E3D9C6;
  --raised:#FBF7EF;--glass:rgba(250,245,236,.9);--sunken:#ECE4D3;--fill:rgba(120,96,60,.10);--plate:#FFFDF8;
  --accent:#5B46A6;--accent-soft:rgba(91,70,166,.11);--hl:rgba(214,51,108,.16)}
:root[data-font="sans"]{--text:var(--sans);--lh:1.56}
*,*::before,*::after{box-sizing:border-box}
html{-webkit-text-size-adjust:100%;text-size-adjust:100%;background:var(--paper)}
body{margin:0;background:var(--paper);color:var(--ink);font:400 var(--fs)/var(--lh) var(--text);
  -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility;
  -webkit-tap-highlight-color:transparent}
html.locked,html.locked body{overflow:hidden}
button{font:inherit;color:inherit;-webkit-tap-highlight-color:transparent}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px;border-radius:6px}
::selection{background:var(--accent-soft)}
.paper{max-width:35em;margin:0 auto;
  padding:calc(env(safe-area-inset-top) + 34px) max(20px,env(safe-area-inset-right)) calc(132px + env(safe-area-inset-bottom)) max(20px,env(safe-area-inset-left))}
.paper{overflow-wrap:break-word}
.paper p{margin:0 0 .95em;hyphens:auto;-webkit-hyphens:auto}
.paper a{color:var(--accent);text-decoration:none}
sup,sub{font-size:.64em;line-height:0}
.paper h2,.paper h3,.paper h4,.paper h5,.paper h6{font-family:var(--sans);color:var(--ink);text-wrap:balance;scroll-margin-top:28px}
.paper h2{font-size:1.13em;font-weight:650;line-height:1.3;letter-spacing:-.012em;margin:2.4em 0 .75em}
.paper h3{font-size:1em;font-weight:620;line-height:1.35;letter-spacing:-.006em;margin:2em 0 .6em}
.paper h4,.paper h5,.paper h6{font-size:.9em;font-weight:600;line-height:1.4;margin:1.6em 0 .45em;color:var(--ink-2)}
.mast{margin:0 0 2.4em;padding-bottom:1.7em;border-bottom:1px solid var(--rule)}
.venue{font:500 13px/1.4 var(--sans);color:var(--ink-2);margin:0 0 18px!important}
h1.title{font-family:var(--serif);font-size:1.8em;line-height:1.12;font-weight:600;letter-spacing:-.02em;margin:0 0 .65em;text-wrap:balance;overflow-wrap:anywhere;hyphens:auto;-webkit-hyphens:auto}
:root[data-font="sans"] h1.title{font-family:var(--sans);font-weight:700;letter-spacing:-.028em}
.authors{font:400 14.5px/1.5 var(--sans);color:var(--ink-2);margin:0 0 6px}
.authors p.byline{margin:0!important;hyphens:none;-webkit-hyphens:none}
.authors.clamp{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.doi{font:400 13px/1.4 var(--sans);margin:2px 0 18px!important}
.paper .doi a{color:var(--ink-3)}
.facts{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.chip{display:inline-flex;align-items:center;gap:6px;height:34px;padding:0 13px;border:0;border-radius:17px;
  background:var(--fill);color:var(--ink);font:500 13.5px/1 var(--sans);cursor:pointer;transition:transform .2s var(--ease),background .2s}
.chip:active{transform:scale(.96)}
.chip svg{width:15px;height:15px;stroke:var(--ink-2);fill:none;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round}
.chip.static{background:none;padding:0 6px 0 2px;color:var(--ink-2);cursor:default}
.more{display:inline-block;border:0;background:none;padding:6px 0 2px;color:var(--accent);font:500 13.5px var(--sans);cursor:pointer}
.paper figure{margin:2em 0 2.2em}
.shot{display:block;width:100%;margin:0;padding:10px;border:1px solid var(--rule);border-radius:14px;background:var(--plate);
  cursor:zoom-in;transition:transform .25s var(--ease)}
.shot:active{transform:scale(.985)}
.shot img{display:block;width:100%;height:auto;border-radius:3px}
.shot img+img{margin-top:10px}
.cap{font:400 14px/1.5 var(--sans);color:var(--ink-2);margin:12px 2px 0!important;hyphens:auto;-webkit-hyphens:auto}
.cap .flabel{font-weight:650;color:var(--ink);margin-right:.25em}
.cap .ftitle{font-weight:550;color:var(--ink)}
.cap.clamp{display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.cap+.more{margin-left:2px}
.tbl{margin:2em 0 2.2em}
.tbl>.cap{margin:0 2px 12px!important}
.tscroll{overflow-x:auto;-webkit-overflow-scrolling:touch;scrollbar-width:none;margin:0 -20px;padding:0 20px}
.tscroll::-webkit-scrollbar{display:none}
.tscroll.scrolls{-webkit-mask-image:linear-gradient(90deg,#000 calc(100% - 36px),transparent);mask-image:linear-gradient(90deg,#000 calc(100% - 36px),transparent)}
.tscroll.scrolls.end{-webkit-mask-image:none;mask-image:none}
.paper table{border-collapse:separate;border-spacing:0;width:100%;font:400 13.5px/1.42 var(--sans);font-variant-numeric:tabular-nums}
.tscroll.wide table{width:max-content;min-width:100%}
.paper th,.paper td{text-align:left;vertical-align:top;padding:9px 14px 9px 0;border-bottom:1px solid var(--rule);max-width:15em;overflow-wrap:break-word}
.paper th{font-weight:600;color:var(--ink);border-bottom:1.5px solid var(--ink-3)}
.paper td b{font-weight:600}
.tscroll.wide th:first-child,.tscroll.wide td:first-child{position:sticky;left:0;z-index:1;background:var(--paper)}
.tscroll.wide.moved th:first-child,.tscroll.wide.moved td:first-child{box-shadow:6px 0 8px -6px rgba(0,0,0,.18)}
.card{border-top:1px solid var(--rule);padding:14px 0 6px;font-family:var(--sans)}
.paper .card h4{margin:0 0 10px;font-size:15px;color:var(--ink);font-weight:600}
.paper dl{margin:0}
dl.kv{display:grid;grid-template-columns:repeat(auto-fill,minmax(7.4em,1fr));gap:10px 14px;margin:0 0 10px!important}
dl.kv dt,dl.long dt{font-size:12px;color:var(--ink-3);line-height:1.3}
dl.kv dd{margin:2px 0 0;font-size:14.5px;line-height:1.35;font-variant-numeric:tabular-nums}
dl.long{margin:0 0 10px!important}
dl.long dd{margin:2px 0 0;font-size:14.5px;line-height:1.45}
p.tgroup{font:650 14px/1.4 var(--sans);margin:22px 0 2px!important;color:var(--ink)}
.orig{display:flex;align-items:center;gap:12px;width:100%;margin:14px 0 0;padding:8px 14px 8px 8px;border:1px solid var(--rule);
  border-radius:14px;background:none;color:var(--accent);font:500 14px var(--sans);text-align:left;cursor:pointer}
.orig img{flex:none;width:46px;height:46px;object-fit:cover;object-position:left top;border-radius:8px;background:#fff;border:1px solid var(--rule)}
.orig img+img{display:none}
.box{margin:2.2em 0;padding:18px 18px 4px;background:var(--sunken);border-radius:18px}
.paper .box>h3,.paper .box>h4{margin:0 0 12px;font-size:15px;line-height:1.4}
.box>h3 .flabel{color:var(--accent);margin-right:.2em}
.box p{font-size:.94em}
.glossary p.term{font:600 15px/1.4 var(--sans);margin:1.2em 0 .2em!important}
.eq{margin:1.1em 0 1.3em;overflow-x:auto;text-align:center;scrollbar-width:none}
.eq img{max-width:100%;height:auto}
:root[data-theme="dark"] .eq img{filter:invert(1) hue-rotate(180deg) brightness(.9);mix-blend-mode:screen}
@media (prefers-color-scheme:dark){:root:not([data-theme]) .eq img{filter:invert(1) hue-rotate(180deg) brightness(.9);mix-blend-mode:screen}
  :root:not([data-theme]) .shot img{filter:brightness(.94)}}
:root[data-theme="dark"] .shot img{filter:brightness(.94)}
:root[data-theme="sepia"] .eq img{mix-blend-mode:multiply}
.paper p.small{font-size:.9em;color:var(--ink-2)}
.paper p.note{font:400 13px/1.5 var(--sans);color:var(--ink-3);border-top:1px solid var(--rule);padding-top:10px}
a.cite{position:relative;font-family:var(--sans);font-weight:500}
a.cite::after{content:"";position:absolute;inset:-12px -3px}
a.cite.ay{font-family:inherit;font-weight:inherit}
a.cite.ay::after{inset:-6px 0}
a.xref{font-weight:500}
sup.cg{padding-left:.06em}
ol.refs,ul.refs{list-style:none;margin:0;padding:0}
.paper ol.refs,.paper ul.refs{font-size:.85em;line-height:1.5}
.refs li{position:relative;padding:12px 0 12px 2.5em;border-bottom:1px solid var(--rule);scroll-margin-top:30px}
.refs.ay li{padding-left:0}
.refs .rn{position:absolute;left:0;top:14px;width:1.9em;text-align:right;font:500 12px/1.4 var(--sans);color:var(--ink-3);font-variant-numeric:tabular-nums}
.refs .rt{overflow-wrap:anywhere}
.rl{display:inline;margin-left:.35em;white-space:nowrap}
.rl a{font:500 12.5px var(--sans);margin-right:.7em}
.sheet .rl{display:flex;gap:8px;margin:10px 0 0}
.sheet .rl a{display:inline-flex;align-items:center;height:32px;padding:0 13px;margin:0;border-radius:16px;background:var(--accent-soft);color:var(--accent);text-decoration:none;font-size:13.5px}
.sheet a{color:var(--accent);text-decoration:none}
.annot{display:block;font-style:italic;color:var(--ink-2);margin-top:4px}
.flash{animation:flash 2.4s var(--ease)}
@keyframes flash{0%,35%{background:var(--hl);box-shadow:0 0 0 8px var(--hl)}100%{background:transparent;box-shadow:0 0 0 8px transparent}}
mark{background:var(--hl);color:inherit;border-radius:3px;padding:0 1px}
.dock{position:fixed;left:50%;bottom:calc(14px + env(safe-area-inset-bottom));z-index:20;transform:translateX(-50%);
  width:min(calc(100% - 28px),31em);height:56px;display:flex;align-items:center;gap:2px;padding:0 6px 0 8px;
  border-radius:28px;background:var(--glass);-webkit-backdrop-filter:saturate(1.8) blur(24px);backdrop-filter:saturate(1.8) blur(24px);
  box-shadow:var(--shadow),inset 0 0 0 .5px var(--rule);font-family:var(--sans);
  transition:transform .5s var(--ease),opacity .35s var(--ease)}
.dock.hide{transform:translate(-50%,calc(100% + 34px));opacity:0}
.where{flex:1;min-width:0;display:flex;align-items:center;gap:11px;height:46px;padding:0 8px 0 6px;border:0;border-radius:23px;background:none;text-align:left;cursor:pointer}
.where:active{background:var(--fill)}
.where span{min-width:0;display:block}
.where b{display:block;font-size:15px;font-weight:600;letter-spacing:-.01em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;color:var(--ink)}
.where small{display:block;font-size:12px;color:var(--ink-2);font-variant-numeric:tabular-nums;margin-top:1px}
.ring{flex:none;width:28px;height:28px}
.ring circle{fill:none;stroke-width:2.6}
.ring .track{stroke:var(--rule)}
.ring .val{stroke:var(--accent);stroke-linecap:round;transform:rotate(-90deg);transform-origin:50% 50%}
.ibtn{flex:none;width:44px;height:44px;display:grid;place-items:center;border:0;border-radius:22px;background:none;color:var(--ink);cursor:pointer}
.ibtn:active{background:var(--fill)}
.ibtn svg{width:21px;height:21px;fill:none;stroke:currentColor;stroke-width:1.8;stroke-linecap:round;stroke-linejoin:round}
.ibtn.aa{font:600 17px/1 var(--serif);letter-spacing:-.02em}
.scrim{position:fixed;inset:0;z-index:30;background:rgba(8,10,18,.34);opacity:0;pointer-events:none;transition:opacity .4s var(--ease)}
.scrim.on{opacity:1;pointer-events:auto}
.sheet{position:fixed;left:0;right:0;bottom:0;z-index:31;margin:0 auto;max-width:640px;max-height:calc(100% - 44px - env(safe-area-inset-top));
  display:flex;flex-direction:column;background:var(--raised);border-radius:22px 22px 0 0;box-shadow:0 -12px 48px rgba(0,0,0,.2);
  font-family:var(--sans);color:var(--ink);padding-bottom:env(safe-area-inset-bottom);
  transform:translateY(104%);visibility:hidden;transition:transform .5s var(--ease),visibility 0s .5s}
.sheet.on{transform:none;visibility:visible;transition:transform .5s var(--ease),visibility 0s}
.sheet.drag{transition:none}
.grab{flex:none;height:24px;display:grid;place-items:center;cursor:grab;touch-action:none}
.grab::before{content:"";width:38px;height:5px;border-radius:3px;background:var(--rule)}
.sheet header{flex:none;display:flex;align-items:center;gap:8px;padding:0 10px 8px 20px;touch-action:none}
.sheet header h2{flex:1;margin:0;font-size:18px;font-weight:650;letter-spacing:-.015em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.close{width:32px;height:32px;display:grid;place-items:center;border:0;border-radius:16px;background:var(--fill);color:var(--ink-2);cursor:pointer}
.close svg{width:14px;height:14px;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;fill:none}
.sheet .body{overflow-y:auto;overscroll-behavior:contain;-webkit-overflow-scrolling:touch;padding:4px 20px 26px}
.search{display:flex;align-items:center;gap:8px;height:42px;padding:0 12px;margin:0 0 18px;background:var(--fill);border-radius:12px;color:var(--ink-3)}
.search svg{flex:none;width:17px;height:17px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round}
.search input{flex:1;min-width:0;height:100%;border:0;background:none;outline:none;font:400 16px var(--sans);color:var(--ink);-webkit-appearance:none;appearance:none}
.search input::placeholder{color:var(--ink-3)}
.search input::-webkit-search-cancel-button{display:none}
.lbl{font-size:13px;font-weight:600;color:var(--ink-2);margin:0 0 10px}
.shelf{display:flex;gap:12px;overflow-x:auto;margin:0 -20px 22px;padding:2px 20px 4px;scroll-snap-type:x proximity;scroll-padding-inline:20px;scrollbar-width:none}
.shelf::after{content:"";flex:none;width:8px}
.shelf::-webkit-scrollbar{display:none}
.thumb{flex:none;width:112px;padding:0;border:0;background:none;text-align:left;cursor:pointer;scroll-snap-align:start}
.thumb i{display:block;height:84px;border-radius:12px;border:1px solid var(--rule);background:#fff center/contain no-repeat;background-origin:content-box;padding:6px;transition:transform .25s var(--ease)}
.thumb:active i{transform:scale(.96)}
.thumb span{display:block;margin-top:7px;font-size:12.5px;font-weight:550;color:var(--ink-2)}
.toc{list-style:none;margin:0;padding:0}
.toc a{display:flex;align-items:center;min-height:44px;padding:9px 12px;margin:0 -12px;border-radius:12px;color:var(--ink);text-decoration:none;font-size:15.5px;line-height:1.3}
.toc a:active{background:var(--fill)}
.toc .l3 a{padding-left:28px;font-size:14.5px;color:var(--ink-2)}
.toc a.cur{background:var(--accent-soft);color:var(--accent);font-weight:600}
.results .lbl{margin:18px 0 4px}
.results .lbl:first-child{margin-top:0}
.hit{display:block;width:100%;padding:10px 12px;margin:0 -12px;border:0;border-radius:12px;background:none;text-align:left;cursor:pointer;
  font:400 14px/1.45 var(--sans);color:var(--ink-2)}
.hit:active{background:var(--fill)}
.hit b{display:block;font-weight:600;color:var(--ink)}
.none{padding:24px 0;color:var(--ink-3);font-size:15px}
.themes{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:2px 0 18px}
.themes button{padding:0;border:0;background:none;font-size:12.5px;font-weight:500;color:var(--ink-2);cursor:pointer}
.themes i{display:grid;place-items:center;height:60px;margin-bottom:7px;border-radius:15px;font:600 20px/1 var(--serif);font-style:normal;box-shadow:inset 0 0 0 1px rgba(128,128,140,.28)}
.themes [aria-pressed="true"]{color:var(--ink)}
.themes [aria-pressed="true"] i{box-shadow:inset 0 0 0 1px rgba(128,128,140,.28),0 0 0 2px var(--raised),0 0 0 4px var(--accent)}
.themes [data-v="auto"] i{background:linear-gradient(135deg,#FDFDFC 50%,#0F1117 50%);color:#8B909B}
.themes [data-v="light"] i{background:#FDFDFC;color:#1B1D23}
.themes [data-v="sepia"] i{background:#F5EFE3;color:#3A3027}
.themes [data-v="dark"] i{background:#0F1117;color:#E7E8EC}
.setrow{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:56px;border-top:1px solid var(--rule);font-size:15.5px}
.seg,.stepper{display:inline-flex;align-items:center;padding:2px;border-radius:11px;background:var(--fill)}
.seg button{height:34px;padding:0 15px;border:0;border-radius:9px;background:none;color:var(--ink-2);font-size:14px;font-weight:500;cursor:pointer}
.seg button[aria-pressed="true"]{background:var(--raised);color:var(--ink);box-shadow:0 1px 3px rgba(0,0,0,.14)}
.seg [data-v="serif"]{font-family:var(--serif)}
.stepper button{width:46px;height:34px;border:0;border-radius:9px;background:none;color:var(--ink);font-family:var(--serif);cursor:pointer}
.stepper button:active{background:var(--raised)}
.stepper button:first-child{font-size:14px}
.stepper button:last-child{font-size:21px}
.stepper output{min-width:30px;text-align:center;font-size:14px;color:var(--ink-2);font-variant-numeric:tabular-nums}
.sheet .refs{font-family:var(--serif);font-size:16px}
.sheet .refs li:last-child{border-bottom:0}
.sheet .refs li:first-child{padding-top:4px}
.sheet .refs li:first-child .rn{top:7px}
.sheet .refs .rn{font-size:12px}
.sheet .tbl{margin:0}
.sheet .tscroll{margin:0 -20px}
.sheet .paper-like{font-family:var(--serif)}
.textbtn{display:flex;align-items:center;justify-content:center;width:100%;height:48px;margin-top:14px;border:0;border-radius:14px;background:var(--fill);color:var(--accent);font:600 15px var(--sans);cursor:pointer}
.textbtn:active{filter:brightness(.96)}
.foot{margin:22px 0 0;font-size:12.5px;color:var(--ink-3);line-height:1.5}
.back{position:fixed;top:calc(10px + env(safe-area-inset-top));left:50%;z-index:25;transform:translate(-50%,-180%);visibility:hidden;
  display:flex;align-items:center;gap:7px;height:38px;padding:0 16px 0 12px;border:0;border-radius:19px;background:var(--ink);color:var(--paper);
  font:600 13.5px var(--sans);box-shadow:var(--shadow);cursor:pointer;transition:transform .5s var(--ease),visibility 0s .5s}
.back.on{transform:translate(-50%,0);visibility:visible;transition:transform .5s var(--ease),visibility 0s}
.back svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.toast{position:fixed;left:50%;bottom:calc(84px + env(safe-area-inset-bottom));z-index:24;transform:translate(-50%,16px);opacity:0;pointer-events:none;visibility:hidden;
  width:min(calc(100% - 28px),31em);display:flex;align-items:center;gap:10px;padding:12px 8px 12px 16px;border-radius:18px;
  background:var(--ink);color:var(--paper);font:400 14px/1.35 var(--sans);box-shadow:var(--shadow);transition:transform .45s var(--ease),opacity .35s}
.toast.on{transform:translate(-50%,0);opacity:1;pointer-events:auto;visibility:visible}
.toast span{flex:1;min-width:0}
.toast b{display:block;font-weight:600}
.toast small{display:block;opacity:.7;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.toast .go{flex:none;height:36px;padding:0 14px;border:0;border-radius:11px;background:var(--paper);color:var(--ink);font:600 14px var(--sans);cursor:pointer}
.toast .x{flex:none;width:36px;height:36px;display:grid;place-items:center;border:0;background:none;color:var(--paper);opacity:.6;cursor:pointer}
.toast .x svg{width:13px;height:13px;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;fill:none}
.viewer{position:fixed;inset:0;z-index:40;display:none;background:#000;color:#fff;font-family:var(--sans);touch-action:none;
  -webkit-user-select:none;user-select:none;opacity:0;transition:opacity .25s ease}
.viewer.on{display:block}
.viewer.in{opacity:1}
.stage{position:absolute;inset:0;overflow:hidden}
.stage img{position:absolute;left:0;top:0;transform-origin:0 0;background:#fff;-webkit-user-drag:none;will-change:transform}
.stage img.anim{transition:transform .32s var(--ease)}
.vtop{position:absolute;left:0;right:0;top:0;display:flex;align-items:center;gap:10px;padding:calc(env(safe-area-inset-top) + 8px) 12px 18px;
  background:linear-gradient(rgba(0,0,0,.72),rgba(0,0,0,0));transition:opacity .25s}
.vtop .t{flex:1;min-width:0;text-align:center}
.vtop b{display:block;font-size:15px;font-weight:600}
.vtop small{display:block;font-size:12px;opacity:.6;font-variant-numeric:tabular-nums}
.vbtn{flex:none;width:40px;height:40px;display:grid;place-items:center;border:0;border-radius:20px;background:rgba(255,255,255,.14);color:#fff;cursor:pointer;
  -webkit-backdrop-filter:blur(12px);backdrop-filter:blur(12px)}
.vbtn svg{width:15px;height:15px;fill:none;stroke:currentColor;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round}
.vbtn.ghost{visibility:hidden}
.vcap{position:absolute;left:0;right:0;bottom:0;max-height:46%;overflow-y:auto;overscroll-behavior:contain;
  padding:34px 20px calc(18px + env(safe-area-inset-bottom));background:linear-gradient(rgba(0,0,0,0),rgba(0,0,0,.82) 34px);
  font-size:14px;line-height:1.5;color:rgba(255,255,255,.82);transition:opacity .25s}
.vcap .in{max-width:640px;margin:0 auto}
.vcap .flabel,.vcap .ftitle{color:#fff;font-weight:600}
.vcap.clamp .in{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.vcap a{color:#C7CCFF}
.vcap .more{color:#C7CCFF}
.viewer.bare .vtop,.viewer.bare .vcap{opacity:0;pointer-events:none}
.vhint{position:absolute;left:50%;top:50%;transform:translate(-50%,-50%);white-space:nowrap;padding:10px 16px;border-radius:14px;background:rgba(40,40,46,.86);font-size:13.5px;
  opacity:0;pointer-events:none;transition:opacity .4s}
.vhint.on{opacity:1}
@media (min-width:720px){
  :root{--fs:19px}
  h1.title{font-size:2.1em}
  .shot{padding:14px}
}
@media (prefers-reduced-motion:reduce){
  *,*::before,*::after{transition-duration:.01ms!important;animation-duration:.01ms!important;scroll-behavior:auto!important}
}
@media print{
  .dock,.sheet,.scrim,.viewer,.back,.toast,.more{display:none!important}
  .cap.clamp,.authors.clamp{display:block;-webkit-line-clamp:unset}
  .paper{padding:0;max-width:none}
}
.vcap{touch-action:pan-y}
.viewer.on~.scrim{z-index:41}
.viewer.on~.sheet{z-index:42}
html:not(.js) .dock,html:not(.js) .back,html:not(.js) .toast,html:not(.js) .viewer,html:not(.js) .sheet,html:not(.js) .scrim{display:none}
html:not(.js) .shot{cursor:default}'''
READER_JS = r'''(function () {
  'use strict';
  var D = document, R = D.documentElement, W = window;
  function $(s, c) { return (c || D).querySelector(s); }
  function $$(s, c) { return Array.prototype.slice.call((c || D).querySelectorAll(s)); }
  var reduce = W.matchMedia && W.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var paper = $('#paper');
  var KEY = 'p2p:pos:' + (paper.getAttribute('data-key') || location.pathname);
  var TOTAL = parseInt(paper.getAttribute('data-minutes') || '0', 10);
  function load(k, d) { try { var v = localStorage.getItem(k); return v ? JSON.parse(v) : d; } catch (e) { return d; } }
  function save(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) {} }
  function esc(s) { return s.replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function scrollTo(y) { W.scrollTo({ top: Math.max(0, y), behavior: reduce ? 'auto' : 'smooth' }); }
  function maxScroll() { return Math.max(1, R.scrollHeight - W.innerHeight); }

  /* ---------- preferences ---------- */
  var prefs = load('p2p:prefs', {}) || {};
  function fsNow() { return parseFloat(getComputedStyle(R).getPropertyValue('--fs')) || 18; }
  function anchor() {                       // keep the reader's place while the layout changes
    var el = D.elementFromPoint(W.innerWidth / 2, Math.min(140, W.innerHeight / 3));
    el = el && el.closest('p, h1, h2, h3, figure, li, .tbl, .box');
    if (!el || !paper.contains(el)) return function () {};
    var top = el.getBoundingClientRect().top;
    return function () { W.scrollTo(0, W.scrollY + el.getBoundingClientRect().top - top); };
  }
  function applyPrefs(keep) {
    var restore = keep ? anchor() : null;
    if (prefs.theme && prefs.theme !== 'auto') R.setAttribute('data-theme', prefs.theme); else R.removeAttribute('data-theme');
    if (prefs.font === 'sans') R.setAttribute('data-font', 'sans'); else R.removeAttribute('data-font');
    if (prefs.fs) R.style.setProperty('--fs', prefs.fs + 'px'); else R.style.removeProperty('--fs');
    var meta = $('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', getComputedStyle(D.body).backgroundColor);
    $$('.themes button').forEach(function (b) { b.setAttribute('aria-pressed', String((prefs.theme || 'auto') === b.dataset.v)); });
    $$('#set-font button').forEach(function (b) { b.setAttribute('aria-pressed', String((prefs.font || 'serif') === b.dataset.v)); });
    $('#fs-out').textContent = Math.round(fsNow());
    if (restore) { restore(); clampAll(); tables(D); }
  }
  $$('.themes button').forEach(function (b) {
    b.addEventListener('click', function () { prefs.theme = b.dataset.v; save('p2p:prefs', prefs); applyPrefs(true); });
  });
  $$('#set-font button').forEach(function (b) {
    b.addEventListener('click', function () { prefs.font = b.dataset.v; save('p2p:prefs', prefs); applyPrefs(true); });
  });
  $$('.stepper button').forEach(function (b) {
    b.addEventListener('click', function () {
      prefs.fs = Math.max(14, Math.min(28, Math.round(fsNow()) + parseInt(b.dataset.step, 10)));
      save('p2p:prefs', prefs); applyPrefs(true);
    });
  });

  /* ---------- sheets ---------- */
  var scrim = $('#scrim'), viewer = $('#viewer'), openSheet = null;
  function lock() { R.classList.toggle('locked', !!openSheet || viewer.classList.contains('on')); }
  function showSheet(id) {
    var s = D.getElementById(id);
    if (openSheet && openSheet !== s) hideSheet();
    openSheet = s;
    s.style.transform = '';
    s.classList.add('on');
    s.setAttribute('aria-hidden', 'false');
    scrim.classList.add('on');
    lock();
    dock.classList.add('hide');
  }
  function hideSheet() {
    if (!openSheet) return;
    var s = openSheet;
    openSheet = null;
    if (s.contains(D.activeElement)) D.activeElement.blur();
    s.classList.remove('on', 'drag');
    s.style.transform = '';
    s.setAttribute('aria-hidden', 'true');
    scrim.classList.remove('on');
    lock();
    dock.classList.remove('hide');
  }
  scrim.addEventListener('click', hideSheet);
  $$('.sheet .close').forEach(function (b) { b.addEventListener('click', hideSheet); });
  $$('.sheet').forEach(function (s) {             // drag the handle or header down to dismiss
    var y0 = null, t0 = 0, dy = 0;
    $$('.grab, header', s).forEach(function (h) {
      h.addEventListener('touchstart', function (e) {
        if (e.target.closest('button, input')) return;
        y0 = e.touches[0].clientY; t0 = Date.now(); dy = 0; s.classList.add('drag');
      }, { passive: true });
      h.addEventListener('touchmove', function (e) {
        if (y0 === null) return;
        dy = Math.max(0, e.touches[0].clientY - y0);
        s.style.transform = 'translateY(' + dy + 'px)';
      }, { passive: true });
      h.addEventListener('touchend', function () {
        if (y0 === null) return;
        s.classList.remove('drag');
        if (dy > 110 || dy / Math.max(1, Date.now() - t0) > 0.6) hideSheet(); else s.style.transform = '';
        y0 = null;
      });
    });
  });

  /* ---------- jumping around without losing your place ---------- */
  var back = $('#back'), backY = null, backTimer = null;
  function flash(el) {
    el.classList.remove('flash'); void el.offsetWidth; el.classList.add('flash');
    setTimeout(function () { el.classList.remove('flash'); }, 2500);
  }
  function jump(el, quiet) {
    var y0 = W.scrollY;
    var top = el.getBoundingClientRect().top + W.scrollY - Math.min(84, W.innerHeight * 0.12);
    scrollTo(top);
    flash(el);
    if (!quiet && Math.abs(top - y0) > W.innerHeight * 0.8) {
      backY = y0;
      back.classList.add('on');
      clearTimeout(backTimer);
      backTimer = setTimeout(function () { back.classList.remove('on'); }, 10000);
    }
  }
  back.addEventListener('click', function () {
    if (backY !== null) scrollTo(backY);
    backY = null;
    back.classList.remove('on');
  });

  /* ---------- figures in the viewer ---------- */
  var items = [];
  $$('.paper .fig, .paper .tbl').forEach(function (el) {
    var shot = $('.shot, .orig', el);
    if (!shot) return;
    var cap = $('.cap', el), lab = cap && $('.flabel', cap);
    var label = lab ? lab.textContent : (el.classList.contains('tbl') ? 'Table' : 'Figure');
    var imgs = $$('img', shot);
    imgs.forEach(function (im, k) {
      items.push({ el: el, img: im, label: label, part: imgs.length > 1 ? (k + 1) + '/' + imgs.length : '', cap: cap ? cap.innerHTML : '' });
    });
    shot.addEventListener('click', function () { openViewer(indexOf(el)); });
  });
  function indexOf(el) { for (var i = 0; i < items.length; i++) if (items[i].el === el) return i; return -1; }

  var stage = $('.stage', viewer), vimg = $('img', stage), vtitle = $('.vtop b', viewer), vcount = $('.vtop small', viewer);
  var vcap = $('.vcap', viewer), vcapIn = $('.vcap .in', viewer), vhint = $('.vhint', viewer);
  var vi = -1, s = 1, tx = 0, ty = 0, bw = 0, bh = 0, ox = 0, oy = 0;
  function openViewer(i) {
    if (i < 0 || !items.length) return;
    if (openSheet) hideSheet();
    viewer.classList.add('on');
    viewer.setAttribute('aria-hidden', 'false');
    lock();
    requestAnimationFrame(function () { viewer.classList.add('in'); });
    show(i);
    if (!load('p2p:hint', false)) {
      save('p2p:hint', true);
      vhint.classList.add('on');
      setTimeout(function () { vhint.classList.remove('on'); }, 2200);
    }
  }
  function closeViewer() {
    viewer.classList.remove('in');
    viewer.setAttribute('aria-hidden', 'true');
    setTimeout(function () {
      viewer.classList.remove('on', 'bare');
      viewer.style.background = '';
      vimg.removeAttribute('src');
      lock();
    }, reduce ? 0 : 250);
  }
  function show(i) {
    vi = (i + items.length) % items.length;
    var it = items[vi];
    s = 1; tx = 0; ty = 0;
    viewer.style.background = '';
    vtitle.textContent = it.label + (it.part ? ' (' + it.part + ')' : '');
    vcount.textContent = items.length > 1 ? (vi + 1) + ' of ' + items.length : '';
    vcapIn.innerHTML = it.cap;
    vcap.hidden = !it.cap;
    vcap.classList.add('clamp');
    vcap.scrollTop = 0;
    vimg.onload = layout;
    vimg.src = it.img.src;
    vimg.alt = it.img.alt;
    if (vimg.complete) layout();
  }
  function layout() {
    var Wd = stage.clientWidth, Ht = stage.clientHeight;
    var nw = vimg.naturalWidth || 1, nh = vimg.naturalHeight || 1;
    var top = 64, bottom = vcap.hidden ? 24 : Math.min(150, Ht * 0.24);
    var k = Math.min((Wd - 16) / nw, (Ht - top - bottom) / nh, 3);
    bw = nw * k; bh = nh * k;
    vimg.style.width = bw + 'px';
    vimg.style.height = bh + 'px';
    ox = (Wd - bw) / 2;
    oy = top + (Ht - top - bottom - bh) / 2;
    apply(false);
  }
  function apply(anim) {
    vimg.classList.toggle('anim', !!anim);
    vimg.style.transform = 'translate(' + (ox + tx) + 'px,' + (oy + ty) + 'px) scale(' + s + ')';
  }
  function bound() {
    var Wd = stage.clientWidth, Ht = stage.clientHeight, w = bw * s, h = bh * s;
    if (w <= Wd) tx = (Wd - w) / 2 - ox; else tx = Math.min(0, Math.max(Wd - w, ox + tx)) - ox;
    if (s <= 1.001) ty = 0;
    else if (h <= Ht) ty = (Ht - h) / 2 - oy;
    else ty = Math.min(0, Math.max(Ht - h, oy + ty)) - oy;
  }
  function zoomAt(px, py, ns, anim) {
    ns = Math.max(1, Math.min(8, ns));
    var u = (px - ox - tx) / s, v = (py - oy - ty) / s;
    s = ns; tx = px - ox - u * s; ty = py - oy - v * s;
    if (s <= 1.001) { s = 1; tx = 0; ty = 0; }
    bound(); apply(anim);
  }
  function reset(anim) { s = 1; tx = 0; ty = 0; apply(anim); }
  function step(dir) {
    if (items.length < 2) { reset(true); return; }
    vimg.style.opacity = '0';
    setTimeout(function () { show(vi + dir); vimg.style.opacity = ''; }, reduce ? 0 : 120);
  }
  function dist(a, b) { return Math.hypot(a.clientX - b.clientX, a.clientY - b.clientY); }
  var pinch = null, pan = null, swipe = null, moved = false, lastTap = 0, lastX = 0, lastY = 0, tapTimer = null;
  stage.addEventListener('touchstart', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    e.preventDefault();
    var t = e.touches;
    if (t.length === 2) {
      pan = null; swipe = null; moved = true;
      pinch = { d: dist(t[0], t[1]) || 1, s: s, mx: (t[0].clientX + t[1].clientX) / 2, my: (t[0].clientY + t[1].clientY) / 2, tx: tx, ty: ty };
    } else if (t.length === 1 && !pinch) {
      moved = false;
      pan = { x: t[0].clientX, y: t[0].clientY, tx: tx, ty: ty, t: Date.now() };
    }
  }, { passive: false });
  stage.addEventListener('touchmove', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    e.preventDefault();
    var t = e.touches;
    if (pinch && t.length === 2) {
      var mx = (t[0].clientX + t[1].clientX) / 2, my = (t[0].clientY + t[1].clientY) / 2;
      var ns = Math.max(0.7, Math.min(8, pinch.s * dist(t[0], t[1]) / pinch.d));
      var u = (pinch.mx - ox - pinch.tx) / pinch.s, v = (pinch.my - oy - pinch.ty) / pinch.s;
      s = ns; tx = mx - ox - u * s; ty = my - oy - v * s;
      apply(false);
      return;
    }
    if (!pan || t.length !== 1) return;
    var dx = t[0].clientX - pan.x, dy = t[0].clientY - pan.y;
    if (!moved && Math.abs(dx) + Math.abs(dy) > 8) moved = true;
    if (!moved) return;
    if (s > 1.001) { tx = pan.tx + dx; ty = pan.ty + dy; apply(false); return; }
    if (!swipe) swipe = Math.abs(dy) > Math.abs(dx) ? 'v' : 'h';
    if (swipe === 'v') {
      ty = dy;
      viewer.style.background = 'rgba(0,0,0,' + Math.max(0.25, 1 - Math.abs(dy) / 420) + ')';
    } else { tx = dx * (items.length > 1 ? 1 : 0.3); }
    apply(false);
  }, { passive: false });
  stage.addEventListener('touchend', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    e.preventDefault();
    if (pinch) {
      if (e.touches.length < 2) {
        pinch = null;
        if (s < 1.02) reset(true); else { bound(); apply(true); }
        pan = null;
      }
      return;
    }
    if (!pan) return;
    var c = e.changedTouches[0], dx = c.clientX - pan.x, dy = c.clientY - pan.y, dt = Math.max(1, Date.now() - pan.t);
    pan = null;
    if (!moved) { tap(c.clientX, c.clientY); return; }
    if (s > 1.001) { bound(); apply(true); }
    else if (swipe === 'v') {
      if (Math.abs(dy) > 110 || Math.abs(dy) / dt > 0.65) closeViewer();
      else { viewer.style.background = ''; reset(true); }
    } else if (swipe === 'h') {
      if (items.length > 1 && (Math.abs(dx) > 70 || Math.abs(dx) / dt > 0.5)) step(dx < 0 ? 1 : -1); else reset(true);
    }
    swipe = null;
  }, { passive: false });
  function tap(x, y) {
    var now = Date.now();
    if (now - lastTap < 300 && Math.hypot(x - lastX, y - lastY) < 30) {
      clearTimeout(tapTimer); lastTap = 0;
      if (s > 1.05) { s = 1; bound(); reset(true); } else zoomAt(x, y, 2.6, true);
      return;
    }
    lastTap = now; lastX = x; lastY = y;
    tapTimer = setTimeout(function () { viewer.classList.toggle('bare'); }, 300);
  }
  stage.addEventListener('wheel', function (e) {
    e.preventDefault();
    zoomAt(e.clientX, e.clientY, s * Math.exp(-e.deltaY * (e.ctrlKey ? 0.012 : 0.0025)), false);
  }, { passive: false });
  var drag = null;
  stage.addEventListener('mousedown', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    drag = { x: e.clientX, y: e.clientY, tx: tx, ty: ty, m: false };
  });
  W.addEventListener('mousemove', function (e) {
    if (!drag) return;
    var dx = e.clientX - drag.x, dy = e.clientY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 4) drag.m = true;
    if (s > 1.001) { tx = drag.tx + dx; ty = drag.ty + dy; apply(false); }
  });
  W.addEventListener('mouseup', function () { if (drag) { if (s > 1.001) { bound(); apply(true); } drag = null; } });
  stage.addEventListener('dblclick', function (e) {
    if (e.target.closest('.vcap, .vtop')) return;
    if (s > 1.05) reset(true); else zoomAt(e.clientX, e.clientY, 2.6, true);
  });
  $('.vclose', viewer).addEventListener('click', closeViewer);
  vcap.addEventListener('click', function (e) {
    if (e.target.closest('a')) return;
    vcap.classList.toggle('clamp');
  });
  W.addEventListener('resize', function () { if (viewer.classList.contains('on')) { reset(false); layout(); } });

  /* ---------- citations, figure and table mentions ---------- */
  var peekList = $('#peek-list'), peekTbl = $('#peek-table'), peekGo = $('#peek-go'), peekTitle = $('#peek-title');
  var numbered = !!$('.paper ol.refs');
  function rangeTitle(ns) {
    if (!numbered) return ns.length > 1 ? 'References' : 'Reference';
    if (ns.length === 1) return 'Reference ' + ns[0];
    var v = ns.map(Number), run = v.every(function (n, i) { return !i || n === v[i - 1] + 1; });
    return 'References ' + (run ? v[0] + '–' + v[v.length - 1] : v.join(', '));
  }
  function peekRefs(ns, focus) {
    ns = ns.filter(function (n) { return D.getElementById('ref-' + n); });
    if (!ns.length) return;
    peekList.innerHTML = '';
    peekList.className = numbered ? 'refs' : 'refs ay';
    ns.forEach(function (n) {
      var li = D.getElementById('ref-' + n).cloneNode(true);
      li.removeAttribute('id');
      peekList.appendChild(li);
    });
    peekList.hidden = false; peekTbl.hidden = true; peekTbl.innerHTML = '';
    peekTitle.textContent = rangeTitle(ns);
    peekGo.textContent = 'Show in the reference list';
    peekGo.onclick = function () { hideSheet(); jump(D.getElementById('ref-' + (focus || ns[0]))); };
    showSheet('peek');
    var body = $('#peek .body'); body.scrollTop = 0;
    var i = ns.indexOf(String(focus));
    if (i > 0) body.scrollTop = peekList.children[i].offsetTop - 8;
  }
  function peekTable(el) {
    var c = el.cloneNode(true);
    c.removeAttribute('id');
    $$('[id]', c).forEach(function (x) { x.removeAttribute('id'); });
    $$('.more', c).forEach(function (x) { x.remove(); });
    $$('.cap', c).forEach(function (x) { x.classList.remove('clamp'); });
    peekTbl.innerHTML = '';
    peekTbl.appendChild(c);
    peekTbl.hidden = false; peekList.hidden = true;
    var lab = $('.flabel', el);
    peekTitle.textContent = lab ? lab.textContent : 'Table';
    peekGo.textContent = 'Go to the table';
    peekGo.onclick = function () { hideSheet(); jump(el); };
    $$('.orig, .shot', c).forEach(function (b) { b.addEventListener('click', function () { openViewer(indexOf(el)); }); });
    showSheet('peek');
    $('#peek .body').scrollTop = 0;
    tables(peekTbl);
  }
  D.addEventListener('click', function (e) {
    var a = e.target.closest && e.target.closest('a');
    if (!a) return;
    if (a.classList.contains('cite')) {
      e.preventDefault();
      var g = a.closest('.cg');
      var ns = g && g.dataset.refs ? g.dataset.refs.split(' ') : [a.dataset.ref];
      if (ns.indexOf(a.dataset.ref) < 0) ns = [a.dataset.ref];
      peekRefs(ns, a.dataset.ref);
      return;
    }
    if (a.classList.contains('xref')) {
      e.preventDefault();
      var t = D.getElementById(a.dataset.x);
      if (!t) return;
      if (t.classList.contains('fig') && indexOf(t) >= 0) openViewer(indexOf(t));
      else if (t.classList.contains('tbl')) peekTable(t);
      else { if (viewer.classList.contains('on')) closeViewer(); hideSheet(); jump(t); }
      return;
    }
    var href = a.getAttribute('href') || '';
    if (href.charAt(0) === '#' && href.length > 1) {
      var target = D.getElementById(decodeURIComponent(href.slice(1)));
      if (target) { e.preventDefault(); hideSheet(); jump(target); }
    }
  });

  /* ---------- contents, search and the figure shelf ---------- */
  var heads = $$('.paper h2[id], .paper h3[id]').filter(function (h) { return !h.closest('.box, .mast'); });
  var toc = $('#toc'), shelf = $('#shelf'), results = $('#results'), q = $('#q'), built = false;
  function buildContents() {
    if (built) return;
    built = true;
    heads.forEach(function (h) {
      var li = D.createElement('li'), a = D.createElement('a');
      li.className = h.tagName === 'H2' ? 'l2' : 'l3';
      a.href = '#' + h.id;
      a.textContent = h.textContent;
      li.appendChild(a);
      toc.appendChild(li);
    });
    var seen = [];
    items.forEach(function (it, i) {
      if (seen.indexOf(it.el) >= 0) return;
      seen.push(it.el);
      var b = D.createElement('button'), im = D.createElement('i'), sp = D.createElement('span');
      b.type = 'button'; b.className = 'thumb';
      im.style.backgroundImage = 'url("' + it.img.src + '")';
      sp.textContent = it.label;
      b.appendChild(im); b.appendChild(sp);
      b.addEventListener('click', function () { openViewer(i); });
      shelf.appendChild(b);
    });
    if (!seen.length) { shelf.hidden = true; $('#shelf-lbl').hidden = true; }
    if (!heads.length) $('#toc-lbl').hidden = true;
  }
  function openContents(searching) {
    buildContents();
    var cur = current();
    $$('a', toc).forEach(function (a) { a.classList.toggle('cur', !!cur && a.getAttribute('href') === '#' + cur.id); });
    showSheet('contents');
    var body = $('#contents .body');
    var on = $('a.cur', toc);
    body.scrollTop = 0;
    if (on && !searching) {
      var y = on.getBoundingClientRect().top - body.getBoundingClientRect().top;
      if (y > body.clientHeight * 0.75) body.scrollTop = y - body.clientHeight / 2;
    }
    if (searching) q.focus();
  }
  var index = null;
  function textOf(el) {                     // superscript citations get a space so words stay apart
    var out = '', w = D.createTreeWalker(el, NodeFilter.SHOW_TEXT), n;
    while ((n = w.nextNode())) { if (n.parentNode.closest('sup')) out += ' '; out += n.nodeValue; }
    return out;
  }
  function buildIndex() {
    index = [];
    var section = '';
    $$('.paper h2[id], .paper h3[id], .paper p, .paper figcaption, .paper .refs li').forEach(function (el) {
      if (el.closest('.mast')) return;
      var t = textOf(el).replace(/\s+/g, ' ').trim();
      if (!t) return;
      var kind = 'p', label = section, target = el;
      if (/^H[23]$/.test(el.tagName)) { kind = 's'; section = t; label = t; }
      else if (el.classList.contains('cap')) { kind = 'f'; target = el.closest('.fig, .tbl') || el; var l = $('.flabel', el); label = l ? l.textContent : 'Caption'; }
      else if (el.tagName === 'LI') { kind = 'r'; var n = $('.rn', el); label = n ? 'Reference ' + n.textContent : 'Reference'; }
      else if (el.closest('.tbl')) return;
      index.push({ el: target, kind: kind, label: label, t: t, l: t.toLowerCase() });
    });
  }
  function snippet(it, terms) {
    var i = Math.max(0, it.l.indexOf(terms[0])), a = Math.max(0, i - 48), b = Math.min(it.t.length, i + 120);
    var out = esc((a > 0 ? '…' : '') + it.t.slice(a, b) + (b < it.t.length ? '…' : ''));
    terms.forEach(function (t) {
      out = out.replace(new RegExp('(' + esc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig'), '<mark>$1</mark>');
    });
    return out;
  }
  var searchT = null;
  q.addEventListener('input', function () {
    clearTimeout(searchT);
    searchT = setTimeout(runSearch, 110);
  });
  q.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { var f = $('.hit', results); if (f) f.click(); }
  });
  function runSearch() {
    var v = q.value.trim().toLowerCase();
    var terms = v.split(/\s+/).filter(function (t) { return t.length > 0; });
    var on = terms.length && v.length >= 2;
    results.hidden = !on;
    $('#contents-main').hidden = !!on;
    if (!on) return;
    if (!index) buildIndex();
    var groups = { s: [], f: [], p: [], r: [] };
    index.forEach(function (it) { if (terms.every(function (t) { return it.l.indexOf(t) >= 0; })) groups[it.kind].push(it); });
    var names = { s: 'Sections', f: 'Figures and tables', p: 'Text', r: 'References' }, caps = { s: 12, f: 12, p: 40, r: 25 };
    results.innerHTML = '';
    var total = 0;
    ['s', 'f', 'p', 'r'].forEach(function (k) {
      var g = groups[k];
      if (!g.length) return;
      total += g.length;
      var h = D.createElement('p');
      h.className = 'lbl';
      h.textContent = names[k] + (g.length > caps[k] ? ' (' + caps[k] + ' of ' + g.length + ')' : ' (' + g.length + ')');
      results.appendChild(h);
      g.slice(0, caps[k]).forEach(function (it) {
        var b = D.createElement('button');
        b.type = 'button'; b.className = 'hit';
        b.innerHTML = (it.label ? '<b>' + esc(it.label) + '</b>' : '') + (k === 's' ? '' : snippet(it, terms));
        b.addEventListener('click', function () { hideSheet(); jump(it.el); });
        results.appendChild(b);
      });
    });
    if (!total) results.innerHTML = '<p class="none">Nothing in this paper matches “' + esc(q.value.trim()) + '”.</p>';
  }

  /* ---------- dock, progress and resume ---------- */
  var dock = $('#dock'), where = $('#where b'), sub = $('#where small'), ring = $('#ring .val');
  var C = 2 * Math.PI * 11, lastY = W.scrollY, ticking = false, saveT = null, curName = null;
  ring.style.strokeDasharray = C;
  var shortTitle = (function () { var h = $('h1.title'); var t = h ? h.textContent.trim() : D.title; return t; })();
  function current() {
    var cur = null;
    for (var i = 0; i < heads.length; i++) { if (heads[i].getBoundingClientRect().top < 150) cur = heads[i]; else break; }
    return cur;
  }
  function tick() {
    ticking = false;
    var y = W.scrollY, max = maxScroll(), p = Math.min(1, Math.max(0, y / max));
    ring.style.strokeDashoffset = C * (1 - p);
    var cur = current(), name = cur ? cur.textContent : shortTitle;
    if (name !== curName) { where.textContent = name; curName = name; }
    var left = Math.ceil(TOTAL * (1 - p));
    sub.textContent = p < 0.015 ? (TOTAL ? TOTAL + ' min read' : 'Start') : p > 0.985 ? 'Finished' : (TOTAL ? left + ' min left' : Math.round(p * 100) + '%');
    if (!openSheet && !viewer.classList.contains('on')) {
      if (y > lastY + 8 && y > 260) dock.classList.add('hide');
      else if (y < lastY - 8 || y >= max - 80) dock.classList.remove('hide');
    }
    lastY = y;
    clearTimeout(saveT);
    saveT = setTimeout(function () { save(KEY, { p: p, h: cur ? cur.textContent : '', t: Date.now() }); }, 700);
  }
  W.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(tick); } }, { passive: true });
  $('#where').addEventListener('click', function () { openContents(false); });
  $('#find').addEventListener('click', function () { openContents(true); });
  $('#aa').addEventListener('click', function () { showSheet('settings'); });

  var toast = $('#toast'), last = load(KEY, null);
  if (last && last.p > 0.03 && last.p < 0.97) {
    $('small', toast).textContent = (last.h ? last.h + ', ' : '') + Math.round(last.p * 100) + '% read';
    setTimeout(function () { toast.classList.add('on'); }, 500);
    var hideToast = setTimeout(function () { toast.classList.remove('on'); }, 9000);
    $('.go', toast).addEventListener('click', function () {
      clearTimeout(hideToast); toast.classList.remove('on');
      scrollTo(last.p * maxScroll());
    });
    $('.x', toast).addEventListener('click', function () { clearTimeout(hideToast); toast.classList.remove('on'); });
  }

  /* ---------- captions, authors and tables that need a second look ---------- */
  function clampOne(el, label) {
    if (el.dataset.open) return;
    el.classList.add('clamp');
    var b = el.nextElementSibling, has = b && b.classList.contains('more');
    var over = el.scrollHeight > el.clientHeight + 2;
    if (over && !has) {
      b = D.createElement('button');
      b.type = 'button'; b.className = 'more'; b.textContent = label;
      b.addEventListener('click', function () { el.classList.remove('clamp'); el.dataset.open = '1'; b.remove(); });
      el.parentNode.insertBefore(b, el.nextSibling);
    } else if (!over) {
      el.classList.remove('clamp');
      if (has) b.remove();
    }
  }
  function clampAll() {
    $$('.paper .fig > .cap, .paper .tbl > .cap').forEach(function (c) { clampOne(c, 'Show full caption'); });
    var au = $('.paper .authors');
    if (au) clampOne(au, 'Show all authors');
  }
  function tables(root) {
    $$('.tscroll', root).forEach(function (t) {
      function upd() {
        t.classList.toggle('scrolls', t.scrollWidth > t.clientWidth + 2);
        t.classList.toggle('end', t.scrollLeft + t.clientWidth >= t.scrollWidth - 2);
        t.classList.toggle('moved', t.scrollLeft > 2);
      }
      upd();
      if (!t.dataset.bound) { t.dataset.bound = '1'; t.addEventListener('scroll', upd, { passive: true }); }
    });
  }

  /* ---------- the facts row ---------- */
  $$('[data-act]').forEach(function (b) {
    b.addEventListener('click', function () {
      var act = b.dataset.act;
      if (act === 'figures') { for (var i = 0; i < items.length; i++) if (items[i].el.classList.contains('fig')) return openViewer(i); }
      if (act === 'tables') { var t = $('.paper .tbl'); if (t) jump(t); }
      if (act === 'refs') { var r = $('.paper .refs'); var h = r && r.closest('section'); h = h && h.previousElementSibling; jump(h && /^H/.test(h.tagName) ? h : r); }
    });
  });

  /* ---------- keyboard ---------- */
  D.addEventListener('keydown', function (e) {
    if (viewer.classList.contains('on')) {
      if (e.key === 'Escape') closeViewer();
      else if (e.key === 'ArrowRight') step(1);
      else if (e.key === 'ArrowLeft') step(-1);
      return;
    }
    if (e.key === 'Escape' && openSheet) { hideSheet(); return; }
    var typing = /INPUT|TEXTAREA/.test((D.activeElement || {}).tagName || '');
    if ((e.key === 'k' && (e.metaKey || e.ctrlKey)) || (e.key === '/' && !typing)) { e.preventDefault(); openContents(true); }
  });

  /* ---------- inside the Paper to Phone shelf ---------- */
  if (W.P2P_SHELF && W.parent !== W) {
    var lib = D.createElement('button');
    lib.type = 'button'; lib.className = 'ibtn'; lib.id = 'lib';
    lib.setAttribute('aria-label', 'Back to your papers');
    lib.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.5 5.5L8 12l6.5 6.5"/></svg>';
    dock.insertBefore(lib, dock.firstChild);
    lib.addEventListener('click', function () { W.parent.postMessage({ type: 'p2p', close: true }, '*'); });
    W.parent.postMessage({ type: 'p2p', ready: true }, '*');
  }

  applyPrefs(false);
  clampAll();
  tables(D);
  tick();
  W.addEventListener('resize', function () { clampAll(); tables(D); });
})();'''
# READER>>


class Renderer:
    def __init__(self, paper, mode):
        self.p, self.mode = paper, mode
        self.loc, self.current = {}, None
        self.ref_ids = {str(r[0]) for r in paper.refs.refs} if paper.refs and paper.refs.numbered else set()
        self.ay = paper.ref_index()
        if self.ay:   # unnumbered lists still get ids, so author-year links can land on them
            pass
        self.ids = {f.id: f for f in paper._all_floats(paper.blocks)}

    def href(self, target):
        if self.mode == "html":
            return "#" + target
        f = self.loc.get(target)
        if f is None:
            return None
        return ("" if f == self.current else f) + "#" + target

    def img_src(self, name):
        if self.mode == "html":
            data, mime = self.p.images[name]
            return f"data:{mime};base64," + base64.b64encode(data).decode()
        return "../images/" + name

    # inline -------------------------------------------------------------------
    def inline(self, segs, cites=True, mentions=True, strip_bold=False):
        parts = []
        for t, f in segs:
            t = self.p.dehy.ligatures(re.sub(r"\s{2,}", " ", t))
            e = esc(t)
            group = None
            if "sup" in f and cites and self.ref_ids and CITE_RE.fullmatch(t.strip()):
                e = re.sub(r"\d+", self._cite, e)
                group = self._group(t)
            elif mentions and not (f & {"sup", "sub"}):
                e = MENTION_RE.sub(self._mention, e)
                if cites and self.ref_ids:
                    e = BRACKET_CITE.sub(self._bracket, e)
                if cites and self.ay:
                    e = AY_CITE.sub(self._ay, e)
            if strip_bold:
                f = f - {"b"}
            for tag in ("sub", "sup", "i", "b"):
                if tag in f:
                    attr = f' class="cg" data-refs="{group}"' if tag == "sup" and group and self.mode == "html" else ""
                    e = f"<{tag}{attr}>{e}</{tag}>"
            parts.append(e)
        return "".join(parts).strip()

    def _group(self, text):
        """'3–5, 9' -> '3 4 5 9': every reference a citation group points to."""
        out = []
        for a, b in re.findall(r"(\d+)(?:\s*[–\-−‒]\s*(\d+))?", text):
            lo, hi = int(a), int(b or a)
            out += [str(n) for n in range(lo, hi + 1)] if 0 <= hi - lo <= 40 else [a, b]
        return " ".join(n for n in out if n in self.ref_ids)

    def _bracket(self, m):
        inner = re.sub(r"\d+", self._cite, m.group(1))
        group = self._group(m.group(1))
        if self.mode == "html" and group:
            return f'<span class="cg" data-refs="{group}">[{inner}]</span>'
        return f"[{inner}]"

    def _cite(self, m):
        n = m.group(0)
        h = self.href("ref-" + n) if n in self.ref_ids else None
        if not h:
            return n
        if self.mode == "html":
            return f'<a href="{h}" class="cite" data-ref="{n}" role="doc-noteref">{n}</a>'
        return f'<a href="{h}" epub:type="noteref" role="doc-noteref">{n}</a>'

    def _ay(self, m):
        name, year = m.group(1).lower(), m.group(2)
        n = self.ay.get((name, year)) or self.ay.get((name, year[:4]))
        h = self.href(f"ref-{n}") if n else None
        if not h:
            return m.group(0)
        if self.mode == "html":
            return f'<a href="{h}" class="cite ay" data-ref="{n}" role="doc-noteref">{m.group(0)}</a>'
        return f'<a href="{h}" epub:type="noteref" role="doc-noteref" class="ay">{m.group(0)}</a>'

    def _mention(self, m):
        w = m.group(1).lower()
        kind = "fig" if w.startswith("fig") else "table" if w.startswith("tab") else "box"
        target = f"{kind}-{m.group(2).upper()}"
        h = self.href(target) if target in self.ids else None
        if h and self.mode == "html":
            return f'<a href="{h}" class="xref" data-x="{target}">{m.group(0)}</a>'
        return f'<a href="{h}">{m.group(0)}</a>' if h else m.group(0)

    def byline(self, P):
        """Author lines: affiliation superscripts stay plain, never citation links."""
        return self.inline(join_segments(P.lines, self.p.dehy), cites=False, mentions=False, strip_bold=True)

    def para(self, P):
        if P.label:
            return esc(P.label)
        return self.inline(join_segments(P.lines, self.p.dehy), strip_bold=P.kind in ("h", "title", "byline", "term"))

    # blocks -------------------------------------------------------------------
    def blocks(self, blocks, depth=0):
        return "\n".join(self.block(b, depth) for b in blocks)

    def block(self, b, depth=0):
        if isinstance(b, RefList):
            return self.refs(b)
        if isinstance(b, Float):
            return getattr(self, "f_" + b.kind)(b, depth)
        h = self.para(b)
        if not h:
            return ""
        if b.kind == "title":
            return f'<h1 class="title" id="{b.id}">{h}</h1>'
        if b.kind == "h":
            lvl = min(6, (b.level or 3) + depth)
            return f'<h{lvl} id="{b.id}">{h}</h{lvl}>'
        cls = {"byline": "byline", "small": "small", "term": "term"}.get(b.kind)
        return f'<p class="{cls}">{h}</p>' if cls else f"<p>{h}</p>"

    def masthead(self, title_html, bylines, facts=""):
        EXT = ' target="_blank" rel="noopener"'
        """Venue, title, authors and DOI as one block at the top of the paper."""
        m = self.p.meta
        venue = ", ".join(esc(x) for x in (m.get("journal"), m.get("year")) if x)
        out = ['<header class="mast">']
        if venue:
            out.append(f'<p class="venue">{venue}</p>')
        out.append(title_html)
        if bylines:
            out.append('<div class="authors">' + "".join(f'<p class="byline">{b}</p>' for b in bylines) + "</div>")
        if m.get("doi"):
            d = esc(m["doi"])
            out.append(f'<p class="doi"><a href="https://doi.org/{d}"{EXT if self.mode == "html" else ""}>doi.org/{d}</a></p>')
        out.append(facts)
        out.append("</header>")
        return "\n".join(x for x in out if x)

    def caption_parts(self, f):
        """(label, title, rest) of a caption: 'Fig. 2 |', its first sentence, everything after."""
        if not f.caption:
            return "", "", ""
        segs = [list(s) for s in join_segments(f.caption, self.p.dehy)]
        plain = "".join(t for t, _ in segs)
        m = CAP_LABEL_RE.match(plain)
        if not m:
            return "", "", self.inline(segs, mentions=False)
        label, rest = cut_segments(segs, m.end())
        body = plain[m.end():]
        e = SENTENCE_END.search(body[:260])
        title, rest = cut_segments(rest, e.end()) if e else ([], rest)
        lab = re.sub(r"[\s.:|]+$", "", "".join(t for t, _ in label))
        return esc(lab), self.inline(title, mentions=False), self.inline(rest, mentions=False)

    def caption(self, f, tag="figcaption"):
        label, title, rest = self.caption_parts(f)
        if not (label or title or rest):
            return ""
        bits = []
        if label:
            bits.append(f'<span class="flabel">{label}</span>')
        if title:
            bits.append(f'<span class="ftitle">{title}</span>')
        if rest:
            bits.append(f'<span class="frest">{rest}</span>')
        return f'<{tag} class="cap">{" ".join(bits)}</{tag}>'

    def f_fig(self, f, depth):
        alt = esc(f.label or "Figure")
        imgs = "".join(f'<img src="{self.img_src(n)}" alt="{alt}"/>' for n in f.images)
        if self.mode == "html":
            imgs = f'<button class="shot" type="button" aria-label="Open {alt}">{imgs}</button>'
        return f'<figure class="fig" id="{f.id}">{imgs}{self.caption(f)}</figure>'

    def f_table(self, f, depth):
        out = [f'<section class="tbl" id="{f.id}">', self.caption(f, "p")]
        rows = [[self.p.dehy.join(c.replace("\xad\n", "").translate(_TRANS).split("\n")) for c in r]
                for r in (f.rows or [])]
        if rows:
            out.append(self.table_body(rows))
        imgs = getattr(f, "images", None) or []
        if imgs:
            alt = esc(f.label or "Table")
            tags = "".join(f'<img src="{self.img_src(n)}" alt="{alt}"/>' for n in imgs)
            if self.mode == "html" and rows:
                out.append(f'<button class="orig" type="button">{tags}<span>View the original table</span></button>')
            elif self.mode == "html":
                out.append(f'<button class="shot" type="button" aria-label="Open {alt}">{tags}</button>')
            else:
                label = "Original layout" if rows else ""
                out.append(f'<figure class="orig">{tags}' + (f'<figcaption>{label}</figcaption>' if label else "") + "</figure>")
        out.append("</section>")
        return "\n".join(x for x in out if x)

    @staticmethod
    def _numeric(cells):
        cells = [c for c in cells if c.strip()]
        return bool(cells) and sum(bool(re.fullmatch(r"[<>≤≥~±+\-–−.,·×\d\s%()Ee/]*", c)) for c in cells) >= 0.6 * len(cells)

    def table_body(self, rows):
        ncol = max(len(r) for r in rows)
        rows = [r + [""] * (ncol - len(r)) for r in rows if any(c.strip() for c in r)]
        head, body = rows[0], rows[1:]
        # multi-row headers ("MIC (µg/ml)" spanning "CBD | CBDV | AmpB")
        while len(body) > 1 and not body[0][0].strip() and any(c.strip() for c in body[0][1:]) \
                and all(len(c) < 25 for c in body[0]) and not self._numeric(body[0]):
            top, last = [], ""
            for c in head:
                last = c if c.strip() else last
                top.append(last)
            head = [" ".join(x for x in (top[i] if body[0][i] else head[i], body[0][i]) if x).strip()
                    for i in range(ncol)]
            body = body[1:]
        header_ok = sum(1 for c in head if c.strip()) >= max(2, ncol - 1) and all(len(c) < 70 for c in head)
        wordy = [len(c) for r in body for c in r[1:] if c.strip()]
        refs_col = [i for i, c in enumerate(head) if re.match(r"refs?\.?$|references?$", c.strip(), re.I)]

        def cell(t, i):
            e = esc(t)
            if i in refs_col and self.ref_ids and CITE_RE.fullmatch(t.strip()):
                e = re.sub(r"\d+", self._cite, e)
            return e

        def is_group(r):
            return r[0].strip() and not any(c.strip() for c in r[1:])

        def longest_word(i):
            return max((len(w) for r in rows for w in (r[i] if i < len(r) else "").split()), default=1)

        est = sum(longest_word(i) * 0.52 + 0.8 for i in range(ncol))   # min grid width in em
        cells = [c.strip() for r in body for c in r[1:] if c.strip()]
        compact = bool(cells) and sum(len(c) <= 12 for c in cells) >= 0.85 * len(cells)   # a data grid: keep it a grid
        if header_ok and body and ncol <= 10 and ((wordy and sum(wordy) / len(wordy) > 28) or (est > 21 and not compact)):
            cards = []
            for r in body:
                if is_group(r):
                    cards.append(f'<p class="tgroup">{esc(r[0])}</p>')
                    continue
                parts, short = [], []
                for i in range(1, ncol):
                    v = r[i].strip()
                    if not v:
                        continue
                    if len(v) <= 28:      # short values sit side by side in a small grid
                        short.append(f'<div><dt>{esc(head[i])}</dt><dd>{cell(v, i)}</dd></div>')
                        continue
                    if short:
                        parts.append(f'<dl class="kv">{"".join(short)}</dl>')
                        short = []
                    parts.append(f'<dl class="long"><dt>{esc(head[i])}</dt><dd>{cell(v, i)}</dd></dl>')
                if short:
                    parts.append(f'<dl class="kv">{"".join(short)}</dl>')
                title = esc(r[0]) if r[0].strip() else "&#8203;"
                cards.append(f'<div class="card"><h4>{title}</h4>{"".join(parts)}</div>')
            return "\n".join(cards)
        th = "".join(f"<th>{esc(c)}</th>" for c in head) if header_ok else ""
        trs = [] if header_ok else [head]
        trs += body
        tb = "".join(
            f'<tr><td colspan="{ncol}"><b>{esc(r[0])}</b></td></tr>' if is_group(r) else
            "<tr>" + "".join(f"<td>{cell(c, i)}</td>" for i, c in enumerate(r)) + "</tr>" for r in trs)
        wide = " wide" if est > 21 else ""
        return f'<div class="tscroll{wide}"><table>{f"<thead><tr>{th}</tr></thead>" if th else ""}<tbody>{tb}</tbody></table></div>'

    def box_title(self, f):
        if not isinstance(f.title, Para):
            return esc(f.label or "")
        if f.title.label:
            return esc(f.title.label)
        segs = [list(x) for x in join_segments(f.title.lines, self.p.dehy)]
        plain = "".join(t for t, _ in segs)
        m = CAP_LABEL_RE.match(plain)
        if not m:
            return self.inline(segs, mentions=False, strip_bold=True)
        label, rest = cut_segments(segs, m.end())
        lab = re.sub(r"[\s.:|]+$", "", "".join(t for t, _ in label))
        return f'<span class="flabel">{esc(lab)}</span> {self.inline(rest, mentions=False, strip_bold=True)}'

    def f_box(self, f, depth):
        return f'<div class="box" id="{f.id}"><h3>{self.box_title(f)}</h3>\n{self.blocks(f.blocks, depth + 1)}</div>'

    def f_aside(self, f, depth):
        title = self.para(f.title) if isinstance(f.title, Para) else esc(f.title or "")
        if f.title and isinstance(f.title, Para) and re.match(r"glossary|abbreviations", f.title.text, re.I):
            return f'<section class="glossary" id="{f.id}"><h2 id="{f.title.id or f.id + "-h"}">{title}</h2>\n' \
                   f'{self.blocks(f.blocks, 0)}</section>'
        return f'<div class="box" id="{f.id}"><h3>{self.box_title(f)}</h3>\n{self.blocks(f.blocks, depth + 1)}</div>'

    def f_eq(self, f, depth):
        em = round(f.regions[0].width / max(self.p.style.size, 1), 1)   # keep math at text size
        return f'<div class="eq"><img src="{self.img_src(f.images[0])}" alt="equation" style="width:{em}em"/></div>'

    def f_note(self, f, depth):
        return f'<p class="note">{self.inline(join_segments(f.lines, self.p.dehy), mentions=False)}</p>'

    def f_gone(self, f, depth):
        return ""

    def refs(self, R):
        items = []
        dehy = self.p.dehy
        for n, lines, note in R.refs:
            segs = join_segments(lines, dehy)
            plain = "".join(t for t, _ in segs)
            m = REFNUM_RE.match(plain) if R.numbered else None
            if m:
                drop = len(m.group(0))
                while drop and segs:
                    if len(segs[0][0]) <= drop:
                        drop -= len(segs[0][0])
                        segs.pop(0)
                    else:
                        segs[0][0] = segs[0][0][drop:]
                        drop = 0
                plain = plain[len(m.group(0)):]
            body = self.inline(segs, cites=False, mentions=False)
            links = []
            EXT = ' target="_blank" rel="noopener"' if self.mode == "html" else ""
            dm = re.search(r"\b(10\.\d{4,9}/[^\s<>\"]+)", plain)
            if dm:
                links.append(f'<a href="https://doi.org/{esc(dm.group(1).rstrip(".)"))}"{EXT}>DOI</a>')
            q = urllib.parse.quote(re.sub(r"\s+", " ", plain)[:280])
            links.append(f'<a href="https://pubmed.ncbi.nlm.nih.gov/?term={q}"{EXT}>PubMed</a>')
            note_html = ""
            if note:
                note_html = f'<span class="annot">{self.inline(join_segments(note, dehy), cites=False, mentions=False, strip_bold=True)}</span>'
            val = f' value="{n}"' if R.numbered else ""
            if self.mode == "html":
                num = f'<span class="rn">{n}</span>' if R.numbered else ""
                items.append(f'<li id="ref-{n}"{val}>{num}<div class="rt">{body}{note_html}</div>'
                             f'<span class="rl">{"".join(links)}</span></li>')
            else:
                items.append(f'<li id="ref-{n}"{val} epub:type="endnote">{body} <span class="rl">{" ".join(links)}</span>{note_html}</li>')
        ep = ' epub:type="endnotes"' if self.mode == "epub" else ""
        tag = "ol" if R.numbered else "ul"
        cls = "refs" if R.numbered else "refs ay"
        return f'<section{ep} role="doc-endnotes"><{tag} class="{cls}">\n' + "\n".join(items) + f"\n</{tag}></section>"


def toc_entries(paper):
    out = []
    for b in paper.blocks:
        if isinstance(b, Para) and b.kind == "h" and b.level in (2, 3):
            out.append((b.level, b.id, b.text))
        elif isinstance(b, Float) and b.kind == "aside" and isinstance(b.title, Para) and \
                re.match(r"glossary|abbreviations", b.title.text, re.I):
            out.append((2, b.id, b.title.text))
    return out


def reading_minutes(words, figures, tables):
    """Rough reading time for a research paper. Dense scientific prose reads slower than
    general text (about 200 words a minute), and each figure or table takes a moment to study."""
    return max(1, round(words / 200 + 0.5 * figures + 0.4 * tables))


ICONS = {
    "list": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6.5h11M9 12h11M9 17.5h11M4.5 6.5h.01M4.5 12h.01M4.5 17.5h.01"/></svg>',
    "search": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="6.5"/><path d="M20 20l-4.2-4.2"/></svg>',
    "close": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"/></svg>',
    "back": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 14L4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/></svg>',
    "clock": '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/></svg>',
    "figure": '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="M20.5 16l-5-5-8.5 8.5"/></svg>',
    "table": '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3.5" y="4.5" width="17" height="15" rx="2.5"/><path d="M3.5 10h17M9.5 10v9.5"/></svg>',
    "refs": '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 4.5h9.5a3 3 0 0 1 3 3v12H9a3 3 0 0 1-3-3z"/><path d="M6 16.5a3 3 0 0 1 3-3h9.5"/></svg>',
}

HEAD_JS = ("try{var r=document.documentElement,p=JSON.parse(localStorage.getItem('p2p:prefs')||'{}');"
           "if(p.theme&&p.theme!=='auto')r.setAttribute('data-theme',p.theme);if(p.font==='sans')r.setAttribute('data-font','sans');"
           "if(p.fs)r.style.setProperty('--fs',p.fs+'px')}catch(e){}document.documentElement.classList.add('js');")


def paper_stats(paper):
    floats = paper._all_floats(paper.blocks)
    words = sum(len(b.text.split()) for b in paper._walk(paper.blocks) if isinstance(b, Para) and b.kind in ("p", "small"))
    figs = sum(1 for f in floats if f.kind == "fig" and getattr(f, "images", None))
    tabs = sum(1 for f in floats if f.kind == "table")
    refs = len(paper.refs.refs) if paper.refs else 0
    return words, figs, tabs, refs


def write_html(paper, path):
    r = Renderer(paper, "html")
    words, figs, tabs, nrefs = paper_stats(paper)
    minutes = reading_minutes(words, figs, tabs)
    blocks = list(paper.blocks)
    title_b = next((b for b in blocks if isinstance(b, Para) and b.kind == "title"), None)
    bylines = [b for b in blocks if isinstance(b, Para) and b.kind == "byline"]
    blocks = [b for b in blocks if b is not title_b and b not in bylines]
    title_text = paper.meta["title"] or paper.path.stem
    title_html = r.block(title_b) if title_b else f'<h1 class="title">{esc(title_text)}</h1>'

    def plural(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"
    chips = [f'<span class="chip static">{ICONS["clock"]}{minutes} min</span>']
    if figs:
        chips.append(f'<button class="chip" type="button" data-act="figures">{ICONS["figure"]}{plural(figs, "figure")}</button>')
    if tabs:
        chips.append(f'<button class="chip" type="button" data-act="tables">{ICONS["table"]}{plural(tabs, "table")}</button>')
    if nrefs:
        chips.append(f'<button class="chip" type="button" data-act="refs">{ICONS["refs"]}{plural(nrefs, "reference")}</button>')
    facts = f'<div class="facts">{"".join(chips)}</div>'
    mast = r.masthead(title_html, [r.byline(b) for b in bylines], facts)
    body = r.blocks(blocks)
    key = re.sub(r"[^a-z0-9]+", "-", (paper.meta.get("doi") or title_text).lower())[:80]
    made = datetime.date.today().strftime("%-d %B %Y") if os.name != "nt" else datetime.date.today().isoformat()
    themes = "".join(f'<button type="button" data-v="{v}" aria-pressed="false"><i>Aa</i>{n}</button>'
                     for v, n in (("auto", "Auto"), ("light", "Light"), ("sepia", "Sepia"), ("dark", "Dark")))
    close = f'<button class="close" type="button" aria-label="Close">{ICONS["close"]}</button>'
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<meta name="theme-color" content="#FDFDFC"/>
<meta name="color-scheme" content="light dark"/>
<title>{esc(title_text)}</title>
<script>{HEAD_JS}</script>
<style>{READER_CSS}</style></head>
<body>
<main class="paper" id="paper" data-key="{esc(key)}" data-minutes="{minutes}">
{mast}
{body}
</main>
<nav class="dock" id="dock" aria-label="Reading controls">
<button class="where" id="where" type="button" aria-label="Contents and progress"><svg class="ring" id="ring" viewBox="0 0 28 28" aria-hidden="true"><circle class="track" cx="14" cy="14" r="11"/><circle class="val" cx="14" cy="14" r="11"/></svg><span><b></b><small></small></span></button>
<button class="ibtn" id="find" type="button" aria-label="Search this paper">{ICONS["search"]}</button>
<button class="ibtn aa" id="aa" type="button" aria-label="Reading settings">Aa</button>
</nav>
<button class="back" id="back" type="button">{ICONS["back"]}Back to where you were</button>
<div class="toast" id="toast" role="status"><span><b>Pick up where you left off</b><small></small></span><button class="go" type="button">Continue</button><button class="x" type="button" aria-label="Dismiss">{ICONS["close"]}</button></div>
<div class="viewer" id="viewer" role="dialog" aria-modal="true" aria-label="Figure viewer" aria-hidden="true">
<div class="stage"><img alt=""/></div>
<div class="vtop"><button class="vbtn vclose" type="button" aria-label="Close viewer">{ICONS["close"]}</button><div class="t"><b></b><small></small></div><span class="vbtn ghost"></span></div>
<div class="vcap clamp"><div class="in"></div></div>
<div class="vhint">Pinch or double-tap to zoom</div>
</div>
<div class="scrim" id="scrim"></div>
<section class="sheet" id="contents" role="dialog" aria-modal="true" aria-label="Contents" aria-hidden="true">
<div class="grab"></div><header><h2>Contents</h2>{close}</header>
<div class="body">
<label class="search">{ICONS["search"]}<input id="q" type="search" placeholder="Search this paper" autocomplete="off" autocorrect="off" enterkeyhint="search" aria-label="Search this paper"/></label>
<div class="results" id="results" hidden></div>
<div id="contents-main"><p class="lbl" id="shelf-lbl">Figures and tables</p><div class="shelf" id="shelf"></div>
<p class="lbl" id="toc-lbl">Sections</p><ol class="toc" id="toc"></ol></div>
</div></section>
<section class="sheet" id="settings" role="dialog" aria-modal="true" aria-label="Reading settings" aria-hidden="true">
<div class="grab"></div><header><h2>Reading settings</h2>{close}</header>
<div class="body">
<div class="themes">{themes}</div>
<div class="setrow"><span>Text size</span><div class="stepper"><button type="button" data-step="-1" aria-label="Smaller text">A</button><output id="fs-out">18</output><button type="button" data-step="1" aria-label="Larger text">A</button></div></div>
<div class="setrow"><span>Typeface</span><div class="seg" id="set-font"><button type="button" data-v="serif">Serif</button><button type="button" data-v="sans">Sans</button></div></div>
<p class="foot">Converted from {esc(paper.path.name)} on {made}. Figures are crops of the original pages, so nothing is redrawn.</p>
</div></section>
<section class="sheet" id="peek" role="dialog" aria-modal="true" aria-label="Details" aria-hidden="true">
<div class="grab"></div><header><h2 id="peek-title">Reference</h2>{close}</header>
<div class="body"><ol class="refs" id="peek-list"></ol><div id="peek-table" hidden></div><button class="textbtn" id="peek-go" type="button">Show in the reference list</button></div>
</section>
<script>{READER_JS}</script>
</body></html>"""
    Path(path).write_text(doc, encoding="utf-8")


def write_epub(paper, path):
    r = Renderer(paper, "epub")
    # split into chapters at level-2 headings (title and abstract stay together)
    chapters, cur = [], []
    for b in paper.blocks:
        is_h2 = isinstance(b, Para) and b.kind == "h" and b.level == 2 and \
            not re.match(r"abstract|summary", bare(b.text), re.I)
        is_gloss = isinstance(b, Float) and b.kind == "aside" and isinstance(b.title, Para) and \
            re.match(r"glossary|abbreviations", b.title.text, re.I)
        if (is_h2 or is_gloss) and cur:
            chapters.append(cur)
            cur = []
        cur.append(b)
    if cur:
        chapters.append(cur)
    files = [f"c{i:02d}.xhtml" for i in range(len(chapters))]
    for fname, ch in zip(files, chapters):
        for b in paper._walk(ch):
            if getattr(b, "id", None):
                r.loc[b.id] = fname
            if isinstance(b, RefList):
                for n, _, _ in b.refs:
                    r.loc[f"ref-{n}"] = fname
    title = paper.meta["title"] or paper.path.stem

    def chapter_title(ch, i):
        for b in ch:
            if isinstance(b, Para) and b.kind in ("h", "title"):
                return b.text
            if isinstance(b, Float) and b.kind == "aside" and isinstance(b.title, Para):
                return b.title.text
        return f"Part {i + 1}"

    xhtml = []
    for i, (fname, ch) in enumerate(zip(files, chapters)):
        r.current = fname
        title_b = next((b for b in ch if isinstance(b, Para) and b.kind == "title"), None)
        bylines = [b for b in ch if isinstance(b, Para) and b.kind == "byline"]
        if i == 0:
            rest = [b for b in ch if b is not title_b and b not in bylines]
            head = r.block(title_b) if title_b else f'<h1 class="title">{esc(title)}</h1>'
            body = r.masthead(head, [r.byline(b) for b in bylines]) + "\n" + r.blocks(rest)
        else:
            body = r.blocks(ch)
        xhtml.append((fname, f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
                             f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
                             f'lang="en" xml:lang="en"><head><meta charset="utf-8"/><title>{esc(chapter_title(ch, i))}</title>'
                             f'<link rel="stylesheet" type="text/css" href="../style.css"/></head><body>\n{body}\n</body></html>'))
    # navigation
    nav_items, open_sub = [], False
    for lvl, i, t in toc_entries(paper):
        link = f'text/{r.loc.get(i, files[0])}#{i}'
        if lvl == 2:
            if open_sub:
                nav_items.append("</ol></li>")
                open_sub = False
            elif nav_items:
                nav_items.append("</li>")
            nav_items.append(f'<li><a href="{link}">{esc(t)}</a>')
        else:
            if not nav_items:
                nav_items.append(f'<li><a href="text/{files[0]}">{esc(title)}</a>')
            if not open_sub:
                nav_items.append("<ol>")
                open_sub = True
            nav_items.append(f'<li><a href="{link}">{esc(t)}</a></li>')
    if nav_items:
        nav_items.append("</ol></li>" if open_sub else "</li>")
    nav_list = "".join(nav_items) or f'<li><a href="text/{files[0]}">{esc(title)}</a></li>'
    first = f'<li><a href="text/{files[0]}">{esc(title[:60])}</a></li>'
    nav = (f'<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
           f'xmlns:epub="http://www.idpf.org/2007/ops" lang="en" xml:lang="en"><head><meta charset="utf-8"/>'
           f'<title>Contents</title></head><body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{first}{nav_list}</ol></nav>'
           f'</body></html>')
    ident = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, paper.meta.get("doi") or str(paper.path.resolve())))
    modified = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
                '<item id="css" href="style.css" media-type="text/css"/>']
    for k, (fname, _) in enumerate(xhtml):
        manifest.append(f'<item id="c{k}" href="text/{fname}" media-type="application/xhtml+xml"/>')
    for k, (name, (_, mime)) in enumerate(paper.images.items()):
        props = ' properties="cover-image"' if name == "cover.jpg" else ""
        manifest.append(f'<item id="img{k}" href="images/{name}" media-type="{mime}"{props}/>')
    spine = "".join(f'<itemref idref="c{k}"/>' for k in range(len(xhtml)))
    creator = f"<dc:creator>{esc(paper.meta['authors'])}</dc:creator>" if paper.meta.get("authors") else ""
    opf = (f'<?xml version="1.0" encoding="utf-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           f'unique-identifier="uid" xml:lang="en"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           f'<dc:identifier id="uid">{ident}</dc:identifier><dc:title>{esc(title)}</dc:title>{creator}'
           f'<dc:language>en</dc:language><meta property="dcterms:modified">{modified}</meta>'
           f'<meta name="cover" content="img{list(paper.images).index("cover.jpg")}"/></metadata>'
           f'<manifest>{"".join(manifest)}</manifest><spine>{spine}</spine></package>')
    container = ('<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS_EPUB, compress_type=zipfile.ZIP_DEFLATED)
        for fname, content in xhtml:
            z.writestr(f"OEBPS/text/{fname}", content, compress_type=zipfile.ZIP_DEFLATED)
        for name, (data, _) in paper.images.items():
            z.writestr(f"OEBPS/images/{name}", data, compress_type=zipfile.ZIP_STORED)


def output_stem(paper):
    m = paper.meta
    head = []
    if m.get("first_author"):
        head.append(m["first_author"])
    if m.get("year"):
        head.append(m["year"])
    t = m.get("title") or paper.path.stem
    name = (" ".join(head) + " - " + t) if head else t
    name = re.sub(r'[\\/:*?"<>|]+', "", name)
    return name[:100].rstrip(" .-") or paper.path.stem


# ---------------------------------------------------------------------------
# Links, DOIs and the clipboard: get the PDF first, then convert it as usual.
# ---------------------------------------------------------------------------

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+[^\s\"<>.,;)\]])")
ARXIV_RE = re.compile(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:\s*)(\d{4}\.\d{4,5}(?:v\d+)?)", re.I)
URL_RE = re.compile(r"https?://[^\s\"<>]+[^\s\"<>.,;)\]]")
PMC_RE = re.compile(r"\b(PMC\d{5,9})\b", re.I)


URL_DOI = [(r"nature\.com/articles/([\w.-]+?)(?:\.pdf)?(?:[?#]|$)", "10.1038/{0}"),
           (r"elifesciences\.org/(?:articles|reviewed-preprints)/(\d+)", "10.7554/eLife.{0}"),
           (r"(?:bio|med)rxiv\.org/content/(10\.1101/[\d.]+)", "{0}")]


class FetchError(Exception):
    pass


def find_refs(text):
    """Links, DOIs, arXiv ids and PMC ids in a piece of text (a paste, a .txt or .url file)."""
    refs, seen = [], set()
    for m in re.finditer(r"%s|%s|%s|%s" % (URL_RE.pattern, ARXIV_RE.pattern, PMC_RE.pattern, DOI_RE.pattern), text, re.I):
        r = m.group(0).strip()
        k = r.lower()
        if any(k in s or s in k for s in seen):       # a DOI inside a link already found
            continue
        seen.add(k)
        refs.append(r)
    return refs


def is_ref(text):
    return bool(re.match(r"^\s*(https?://|10\.\d{4,9}/|doi:|arxiv:|PMC\d)", text, re.I))


def fetch_paper(ref, dest, log=print):
    """Download the PDF behind a link, DOI, arXiv id or PMC id. Article pages are followed to
    their PDF through the citation_pdf_url tag that most publishers print; DOIs fall back to
    an open-access copy listed by OpenAlex. Returns the saved path."""
    import http.cookiejar
    import json
    import urllib.request
    if OFFLINE:
        raise FetchError("Offline mode: downloads are switched off.")
    ref = ref.strip().rstrip(".")
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    doi = None
    m = ARXIV_RE.search(ref) or re.fullmatch(r"(\d{4}\.\d{4,5}(?:v\d+)?)", ref)
    pmc = PMC_RE.search(ref)
    if m:
        urls = [f"https://arxiv.org/pdf/{m.group(1)}"]
    elif pmc and not ref.lower().startswith("http"):
        urls = [f"https://europepmc.org/articles/{pmc.group(1).upper()}?pdf=render"]
    else:
        dm = DOI_RE.search(urllib.parse.unquote(ref))
        doi = dm.group(1) if dm else None
        for pat, fmt in URL_DOI:          # publisher links that carry the DOI in another form
            mm = re.search(pat, ref, re.I)
            if mm and not doi:
                doi = fmt.format(*mm.groups())
        urls = [ref if ref.lower().startswith("http") else f"https://doi.org/{doi}"] if (doi or ref.startswith("http")) else []
        if pmc:
            urls.append(f"https://europepmc.org/articles/{pmc.group(1).upper()}?pdf=render")
        nat = re.search(r"(https?://www\.nature\.com/articles/[\w.-]+?)(?:\.pdf)?(?:[?#].*)?$", ref)
        if nat:
            urls.insert(0, nat.group(1) + ".pdf")
    if not urls:
        raise FetchError(f"Not a link, DOI or arXiv id: {ref}")
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                             "Version/17.5 Safari/605.1.15",
               "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.8", "Accept-Language": "en"}

    def get(url, referer=None):
        h = dict(headers, **({"Referer": referer} if referer else {}))
        with opener.open(urllib.request.Request(url, headers=h), timeout=40) as r:
            return r.geturl(), r.headers.get("Content-Type", ""), r.headers.get("Content-Disposition", ""), r.read(60_000_000)

    def pdf_link(html_text, base):
        for pat in (r'<meta[^>]+(?:name|property)=["\']citation_pdf_url["\'][^>]*content=["\']([^"\']+)',
                    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:name|property)=["\']citation_pdf_url',
                    r'<link[^>]+type=["\']application/pdf["\'][^>]*href=["\']([^"\']+)',
                    r'<a[^>]+href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']'):
            mm = re.search(pat, html_text, re.I)
            if mm:
                return urllib.parse.urljoin(base, html.unescape(mm.group(1)))
        return None

    tried, last = set(), ""
    queue = list(urls)
    while queue:
        url = queue.pop(0)
        if url in tried:
            continue
        tried.add(url)
        try:
            final, ctype, disp, body = get(url, referer=next(iter(tried - {url}), None))
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
            continue
        if body[:5] == b"%PDF-" or "pdf" in ctype.lower() and body[:1024].lstrip()[:5] == b"%PDF-":
            name = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disp or "")
            name = urllib.parse.unquote(name.group(1)) if name else Path(urllib.parse.urlparse(final).path).name
            name = re.sub(r'[\\/:*?"<>|]+', "_", name or "paper")
            if not name.lower().endswith(".pdf"):
                name = (re.sub(r"[^\w.-]+", "_", doi) if doi else name or "paper") + ".pdf"
            out = unique(dest / name)
            out.write_bytes(body)
            log(f"downloaded {final} -> {out.name}")
            return out
        if "html" in ctype.lower() or body[:200].lstrip().startswith(b"<"):
            text = body.decode("utf-8", "replace")
            nxt = pdf_link(text, final)
            if nxt and nxt not in tried:
                queue.insert(0, nxt)
            if not doi:
                dm = re.search(r'<meta[^>]+name=["\'](?:citation_doi|dc\.identifier)["\'][^>]*content=["\'](?:doi:)?(10\.[^"\']+)', text, re.I)
                doi = dm.group(1) if dm else None
        last = f"{final} gave {ctype or 'no content type'}"
        if not queue and doi and "openalex" not in " ".join(tried):
            api = f"https://api.openalex.org/works/https://doi.org/{urllib.parse.quote(doi)}"
            tried.add(api)
            try:
                _, _, _, raw = get(api)
                w = json.loads(raw)
                locs = [w.get("best_oa_location") or {}] + (w.get("locations") or [])
                queue += [l.get("pdf_url") for l in locs if l and l.get("pdf_url")]
                queue += [l.get("landing_page_url") for l in locs if l and l.get("is_oa") and l.get("landing_page_url")]
            except Exception:
                pass
    raise FetchError(f"Could not get a PDF from {ref} ({last}). If the paper needs a library login, "
                     f"download the PDF in your browser and use the file instead.")


def clipboard_refs():
    """What is on the clipboard: a copied PDF file (macOS Finder), a file path, or text with links."""
    import subprocess
    if sys.platform == "darwin":
        try:
            r = subprocess.run(["osascript", "-e", "POSIX path of (the clipboard as «class furl»)"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                return [r.stdout.strip()]
        except Exception:
            pass
    if sys.platform == "darwin":       # PDF content copied from Preview, Safari or an iPhone (Universal Clipboard)
        out = Path.home() / "Downloads" / f"Clipboard {datetime.datetime.now():%Y-%m-%d %H.%M.%S}.pdf"
        script = (f'set f to POSIX file "{out}"\nset fh to open for access f with write permission\n'
                  'set eof fh to 0\nwrite (the clipboard as «class PDF ») to fh\nclose access fh')
        try:
            r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and out.exists() and out.read_bytes()[:5] == b"%PDF-":
                return [str(out)]
            if out.exists():
                out.unlink()
        except Exception:
            pass
    if os.name == "nt":
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-Clipboard -Format FileDropList | ForEach-Object { $_.FullName }"],
                               capture_output=True, text=True, timeout=10)
            files = [x.strip() for x in r.stdout.splitlines() if x.strip().lower().endswith(".pdf")]
            if files:
                return files
        except Exception:
            pass
    cmds = {"darwin": [["pbpaste"]], "win32": [["powershell", "-NoProfile", "-Command", "Get-Clipboard"]]}
    text = ""
    for cmd in cmds.get(sys.platform, [["wl-paste", "--no-newline"], ["xclip", "-selection", "clipboard", "-o"]]):
        try:
            text = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
            if text:
                break
        except Exception:
            continue
    text = text.strip().strip('"')
    if text and Path(text).expanduser().exists():
        return [text]
    return find_refs(text)


def resolve_inputs(items, workdir, log=print):
    """Turn paths, folders, links and DOIs into a list of local PDF paths."""
    pdfs = []
    for inp in items:
        p = Path(inp).expanduser()
        if p.exists():
            pdfs += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
        elif is_ref(inp) or ARXIV_RE.search(inp) or DOI_RE.search(inp):
            try:
                pdfs.append(fetch_paper(inp, workdir, log=log))
            except FetchError as e:
                log(f"!! {e}")
        else:
            log(f"!! Not found: {inp}")
    return pdfs


OFFLINE = False


def enrich_metadata(paper):
    """Fill the journal, year and authors from Crossref when the paper has a DOI. The DOI is the
    only thing sent; with --offline (or no connection) the converter keeps what it read itself."""
    import json
    import urllib.request
    m = paper.meta
    if OFFLINE or not m.get("doi"):
        return
    try:
        req = urllib.request.Request("https://api.crossref.org/works/" + urllib.parse.quote(m["doi"]),
                                     headers={"User-Agent": "paper2phone/1.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            w = json.load(r)["message"]
    except Exception:
        return
    journal = (w.get("container-title") or [None])[0]
    if journal and w.get("type", "journal-article") in ("journal-article", "posted-content", "proceedings-article", "review-article"):
        m["journal"] = html.unescape(journal)
    parts = ((w.get("issued") or w.get("published") or {}).get("date-parts") or [[None]])[0]
    if parts and parts[0]:
        m["year"] = str(parts[0])
    people = [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in w.get("author", []) if a.get("family")]
    if people and not m.get("authors"):
        m["authors"] = ", ".join(people)
    if people and not m.get("first_author"):
        m["first_author"] = w["author"][0].get("family")


def convert(pdf, outdir=None, fmt="epub", placement="first-mention", dpi=288, log=print):
    pdf = Path(pdf).expanduser()
    paper = Paper(pdf, dpi=dpi, placement=placement, log=log)
    paper.analyse()
    enrich_metadata(paper)
    paper.render_images()
    outdir = Path(outdir).expanduser() if outdir else pdf.parent
    outdir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(paper)
    written = []
    if fmt in ("epub", "both"):
        p = outdir / f"{stem}.epub"
        write_epub(paper, p)
        written.append(p)
    if fmt in ("html", "both"):
        p = outdir / f"{stem}.html"
        write_html(paper, p)
        written.append(p)
    log(f"{pdf.name}: {paper.summary()}")
    for w in paper.warnings:
        log(f"  note: {w}")
    for w in written:
        log(f"  -> {w}")
    return written, paper


# ---------------------------------------------------------------------------
# Drop folder: put PDFs in a synced folder (OneDrive, iCloud Drive, Dropbox) from any
# device; a computer running --watch converts them into the Library next to it.
# ---------------------------------------------------------------------------

INBOX, LIBRARY, ORIGINALS, FAILED = "Inbox", "Library", "Originals", "Could not convert"


def library_index(lib):
    """Library/index.html: every converted paper with its cover, newest first."""
    import json
    data_p = lib / ".library.json"
    try:
        entries = json.loads(data_p.read_text(encoding="utf-8"))
    except Exception:
        entries = []
    entries = [e for e in entries if (lib / e.get("html", "")).exists() or (lib / e.get("epub", "")).exists()]
    cards = []
    for e in sorted(entries, key=lambda e: e.get("added", ""), reverse=True):
        links = []
        if e.get("html"):
            links.append(f'<a href="{urllib.parse.quote(e["html"])}">Read</a>')
        if e.get("epub"):
            links.append(f'<a href="{urllib.parse.quote(e["epub"])}">EPUB for Books</a>')
        sub = ", ".join(x for x in (e.get("venue"), e.get("year")) if x)
        cover = f'<img src="{e["thumb"]}" alt=""/>' if e.get("thumb") else '<span class="blank"></span>'
        cards.append(f'<li>{cover}<div><h2>{esc(e.get("title", ""))}</h2>'
                     f'<p>{esc(e.get("authors") or "")}</p><p class="sub">{esc(sub)}</p>'
                     f'<p class="go">{"".join(links)}</p></div></li>')
    css = ("body{margin:0;background:#FDFDFC;color:#1B1D23;font:16px/1.45 -apple-system,system-ui,'Segoe UI',sans-serif}"
           "main{max-width:760px;margin:0 auto;padding:40px 20px 80px}h1{font:600 30px/1.15 ui-serif,'New York',Georgia,serif;margin:0 0 6px}"
           ".n{color:#575D6B;margin:0 0 28px}ul{list-style:none;margin:0;padding:0}li{display:flex;gap:16px;padding:16px 0;border-top:1px solid #E6E7EB}"
           "li img,.blank{flex:none;width:72px;height:108px;border-radius:6px;object-fit:cover;background:#26235E;box-shadow:0 1px 3px rgba(0,0,0,.15)}"
           "h2{font-size:16px;line-height:1.3;margin:0 0 4px}li p{margin:0;color:#575D6B;font-size:14px}.sub{color:#8B909B}"
           ".go{margin-top:10px!important}.go a{display:inline-block;margin-right:14px;color:#4B45B8;font-weight:600;text-decoration:none}"
           "@media(prefers-color-scheme:dark){body{background:#0F1117;color:#E7E8EC}li{border-color:#262A34}li p,.n{color:#A6ABB8}.go a{color:#A3ACFF}}")
    n = len(entries)
    doc = (f'<!doctype html><html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>'
           f'<title>Paper library</title><style>{css}</style></head><body><main><h1>Paper library</h1>'
           f'<p class="n">{n} paper{"" if n == 1 else "s"}. Add PDFs to the {INBOX} folder from any device.</p>'
           f'<ul>{"".join(cards)}</ul></main></body></html>')
    (lib / "index.html").write_text(doc, encoding="utf-8")


def remember(lib, paper, written):
    import json
    data_p = lib / ".library.json"
    try:
        entries = json.loads(data_p.read_text(encoding="utf-8"))
    except Exception:
        entries = []
    names = {Path(w).suffix: Path(w).name for w in written}
    thumb = ""
    try:
        pix = pymupdf.Pixmap(paper.images["cover.jpg"][0])
        small = pymupdf.Pixmap(pix, 144, 216, None)
        thumb = "data:image/jpeg;base64," + base64.b64encode(small.tobytes("jpeg", jpg_quality=75)).decode()
    except Exception:
        pass
    m = paper.meta
    entry = {"title": m.get("title") or paper.path.stem, "authors": m.get("authors"), "venue": m.get("journal"),
             "year": m.get("year"), "html": names.get(".html", ""), "epub": names.get(".epub", ""),
             "added": datetime.datetime.now().isoformat(timespec="seconds"), "thumb": thumb}
    entries = [e for e in entries if e.get("html") != entry["html"] or e.get("epub") != entry["epub"]] + [entry]
    data_p.write_text(json.dumps(entries, indent=1), encoding="utf-8")


def unique(path):
    path = Path(path)
    k = 2
    while path.exists():
        path = path.with_name(f"{path.stem} ({k}){path.suffix}")
        k += 1
    return path


def watch(root, once=False, interval=8, fmt="both", log=print):
    """Convert every PDF that lands in root/Inbox, and every .txt/.url/.webloc file holding links
    or DOIs (their PDFs are downloaded first); outputs go to root/Library."""
    root = Path(root).expanduser()
    inbox, lib, orig, bad = (root / d for d in (INBOX, LIBRARY, ORIGINALS, FAILED))
    for d in (inbox, lib, orig):
        d.mkdir(parents=True, exist_ok=True)
    if not (lib / "index.html").exists():
        library_index(lib)
    log(f"Watching {inbox} (outputs in {lib}). Press Ctrl+C to stop.")
    sizes = {}
    while True:
        for note in [f for f in sorted(inbox.iterdir()) if f.suffix.lower() in (".txt", ".url", ".webloc")]:
            try:
                size = note.stat().st_size
            except OSError:
                continue
            if sizes.get(note) != size and not once:
                sizes[note] = size
                continue
            sizes.pop(note, None)
            refs = find_refs(note.read_text(encoding="utf-8", errors="replace"))
            problems = []
            for ref in refs:
                try:
                    pdf = fetch_paper(ref, orig, log=log)
                    written, paper = convert(pdf, lib, fmt, log=log)
                    remember(lib, paper, written)
                    library_index(lib)
                except Exception as e:
                    problems.append(f"{ref}\n  {e}")
                    log(f"!! {ref}: {e}")
            if problems or not refs:
                bad.mkdir(exist_ok=True)
                dest = unique(bad / note.name)
                shutil.move(str(note), str(dest))
                dest.with_name(dest.stem + " (what went wrong).txt").write_text(
                    "\n\n".join(problems) or "No link, DOI or arXiv id found in this file.", encoding="utf-8")
            else:
                shutil.move(str(note), str(unique(orig / note.name)))
        for pdf in sorted(inbox.glob("*.pdf")) + sorted(inbox.glob("*.PDF")):
            try:
                size = pdf.stat().st_size
            except OSError:
                continue
            if size == 0 or sizes.get(pdf) != size:      # still syncing: wait for the size to settle
                sizes[pdf] = size
                if not once:
                    continue
            sizes.pop(pdf, None)
            try:
                written, paper = convert(pdf, lib, fmt, log=log)
                remember(lib, paper, written)
                library_index(lib)
                shutil.move(str(pdf), str(unique(orig / pdf.name)))
            except Exception as e:
                bad.mkdir(exist_ok=True)
                dest = unique(bad / pdf.name)
                shutil.move(str(pdf), str(dest))
                dest.with_name(dest.stem + " (what went wrong).txt").write_text(
                    f"{pdf.name} could not be converted.\n\n{type(e).__name__}: {e}\n\n{traceback.format_exc()}", encoding="utf-8")
                log(f"!! {pdf.name}: {e}")
        if once:
            return
        time.sleep(interval)


def install_service(root):
    """Start the drop-folder watcher at login (macOS launchd or Windows Task Scheduler)."""
    root = Path(root).expanduser().resolve()
    script = Path(__file__).resolve()
    py = Path(sys.executable).resolve()
    (root / INBOX).mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        agent = Path.home() / "Library/LaunchAgents/com.paper2phone.watch.plist"
        logf = Path.home() / "Library/Logs/paper2phone.log"
        agent.parent.mkdir(parents=True, exist_ok=True)
        args = "".join(f"<string>{esc(str(a))}</string>" for a in (py, script, "--watch", root))
        agent.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>com.paper2phone.watch</string>
<key>ProgramArguments</key><array>{args}</array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>ProcessType</key><string>Background</string>
<key>StandardOutPath</key><string>{esc(str(logf))}</string><key>StandardErrorPath</key><string>{esc(str(logf))}</string>
</dict></plist>
""", encoding="utf-8")
        os.system(f'launchctl unload "{agent}" 2>/dev/null; launchctl load "{agent}"')
        print(f"Installed. New PDFs in {root / INBOX} are converted while this Mac is awake.\n"
              f"Log: {logf}\nIf macOS asks whether python may access files in {root.name}, choose Allow.\n"
              f"To remove: launchctl unload \"{agent}\" && rm \"{agent}\"")
    elif os.name == "nt":
        pyw = py.with_name("pythonw.exe") if py.with_name("pythonw.exe").exists() else py
        cmd = f'schtasks /Create /F /SC ONLOGON /TN "Paper to Phone" /TR "\\"{pyw}\\" \\"{script}\\" --watch \\"{root}\\""'
        os.system(cmd)
        os.system('schtasks /Run /TN "Paper to Phone"')
        print(f"Installed. New PDFs in {root / INBOX} are converted while this PC is on.\n"
              f'To remove: schtasks /Delete /TN "Paper to Phone" /F')
    else:
        print(f"Add this to your startup programs:\n  {py} {script} --watch \"{root}\"")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Convert journal-article PDFs into phone-friendly EPUB/HTML, keeping figures.")
    ap.add_argument("inputs", nargs="*", help="PDF files, folders of PDFs, links to papers, DOIs or arXiv ids")
    ap.add_argument("--clipboard", action="store_true", help="convert the PDF, link or DOI on the clipboard")
    ap.add_argument("--offline", action="store_true", help="never go online (no journal lookup by DOI, no downloads)")
    ap.add_argument("-o", "--outdir", help="where to write (default: next to each PDF)")
    ap.add_argument("--format", choices=["epub", "html", "both"], default="both")
    ap.add_argument("--figures", choices=["first-mention", "in-place"], default="first-mention",
                    help="put each figure after the paragraph that first cites it, or where it sits in the PDF")
    ap.add_argument("--dpi", type=int, default=288, help="figure resolution (default 288)")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--watch", metavar="FOLDER", help="keep converting PDFs dropped into FOLDER/Inbox (e.g. a OneDrive folder)")
    ap.add_argument("--once", action="store_true", help="with --watch: convert what is waiting, then exit")
    ap.add_argument("--install-service", metavar="FOLDER", help="run --watch FOLDER automatically at login")
    a = ap.parse_args(argv)
    global OFFLINE
    OFFLINE = a.offline
    if a.install_service:
        install_service(a.install_service)
        return 0
    if a.watch:
        try:
            watch(a.watch, once=a.once, fmt="both")
        except KeyboardInterrupt:
            pass
        return 0
    items = list(a.inputs)
    if a.clipboard:
        found = clipboard_refs()
        if not found:
            print("The clipboard has no PDF, link or DOI.", file=sys.stderr)
            return 2
        items += found
    if not items:
        ap.print_help()
        return 2
    pdfs = resolve_inputs(items, Path(a.outdir or ".").expanduser())
    if not pdfs:
        return 1
    ok = 0
    for pdf in pdfs:
        try:
            convert(pdf, a.outdir, a.format, a.figures, a.dpi)
            ok += 1
        except Exception as e:
            print(f"!! {pdf.name}: {e}", file=sys.stderr)
            if a.debug:
                traceback.print_exc()
    return 0 if ok == len(pdfs) else 1


if __name__ == "__main__":
    sys.exit(main())
