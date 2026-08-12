#!/usr/bin/env python3
"""clean.py — pre-delivery hygiene for text and files.

Two independent jobs, one CLI:

  text   strip invisible/exotic Unicode that LLMs emit and that breaks
         Hebrew RTL layout, WordPress, Word and newsletters
  file   strip authoring metadata (author, paths, tool names) from
         DOCX/XLSX/PPTX, PDF, JPEG and PNG before a file leaves the house

Python 3.9+, zero third-party dependencies.
PDF cleaning uses `qpdf` if it is on PATH; everything else is stdlib.

Explicit non-goal: this tool never rewrites wording to disguise that text
was produced by a model. It only removes characters and metadata that
should not have been there in the first place.
"""

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unicodedata
import zipfile

# ---------------------------------------------------------------- text rules

# Characters removed outright: zero-width, joiners, invisible operators,
# soft hyphen, BOM, word joiner, and the Unicode "tag" block (U+E0000+)
# that is used to smuggle hidden payloads inside otherwise normal text.
INVISIBLE = {
    "\u00ad": "SOFT HYPHEN",
    "\u180e": "MONGOLIAN VOWEL SEPARATOR",
    "\u200b": "ZERO WIDTH SPACE",
    "\u200c": "ZERO WIDTH NON-JOINER",
    "\u200d": "ZERO WIDTH JOINER",
    "\u2060": "WORD JOINER",
    "\u2061": "FUNCTION APPLICATION",
    "\u2062": "INVISIBLE TIMES",
    "\u2063": "INVISIBLE SEPARATOR",
    "\u2064": "INVISIBLE PLUS",
    "\ufeff": "ZERO WIDTH NO-BREAK SPACE (BOM)",
    "\ufffc": "OBJECT REPLACEMENT CHARACTER",
}

# Bidi control characters. Removed by default; keep them with --keep-bidi
# when a document genuinely mixes Hebrew with numbers/Latin and relies on
# explicit marks. RLM/LRM inside plain prose is almost always noise.
BIDI = {
    "\u200e": "LEFT-TO-RIGHT MARK",
    "\u200f": "RIGHT-TO-LEFT MARK",
    "\u202a": "LEFT-TO-RIGHT EMBEDDING",
    "\u202b": "RIGHT-TO-LEFT EMBEDDING",
    "\u202c": "POP DIRECTIONAL FORMATTING",
    "\u202d": "LEFT-TO-RIGHT OVERRIDE",
    "\u202e": "RIGHT-TO-LEFT OVERRIDE",
    "\u2066": "LEFT-TO-RIGHT ISOLATE",
    "\u2067": "RIGHT-TO-LEFT ISOLATE",
    "\u2068": "FIRST STRONG ISOLATE",
    "\u2069": "POP DIRECTIONAL ISOLATE",
}

# Exotic spaces → a plain space. NBSP is included: models sprinkle it
# freely and it breaks line wrapping in Word and in email clients.
SPACES = {
    "\u00a0": "NO-BREAK SPACE",
    "\u1680": "OGHAM SPACE MARK",
    "\u2000": "EN QUAD",
    "\u2001": "EM QUAD",
    "\u2002": "EN SPACE",
    "\u2003": "EM SPACE",
    "\u2004": "THREE-PER-EM SPACE",
    "\u2005": "FOUR-PER-EM SPACE",
    "\u2006": "SIX-PER-EM SPACE",
    "\u2007": "FIGURE SPACE",
    "\u2008": "PUNCTUATION SPACE",
    "\u2009": "THIN SPACE",
    "\u200a": "HAIR SPACE",
    "\u202f": "NARROW NO-BREAK SPACE",
    "\u205f": "MEDIUM MATHEMATICAL SPACE",
    "\u3000": "IDEOGRAPHIC SPACE",
}

# Typography normalisation — opt-in via --ascii-punct.
# Off by default: curly quotes and a real em dash are usually wanted.
PUNCT = {
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
    "\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-",
    "\u2026": "...", "\u2044": "/", "\u02bc": "'",
}

TAG_BLOCK = (0xE0000, 0xE007F)
VARIATION_SELECTORS = (0xFE00, 0xFE0F)


def analyse(text, keep_bidi=False):
    """Return {label: count} for everything this tool would touch."""
    found = {}

    def bump(label, n=1):
        found[label] = found.get(label, 0) + n

    for ch in text:
        cp = ord(ch)
        if ch in INVISIBLE:
            bump(INVISIBLE[ch])
        elif ch in BIDI and not keep_bidi:
            bump(BIDI[ch])
        elif ch in SPACES:
            bump(SPACES[ch])
        elif TAG_BLOCK[0] <= cp <= TAG_BLOCK[1]:
            bump("UNICODE TAG CHARACTER (hidden payload)")
        elif VARIATION_SELECTORS[0] <= cp <= VARIATION_SELECTORS[1]:
            bump("VARIATION SELECTOR")
        elif unicodedata.category(ch) == "Cf":
            bump("OTHER FORMAT CHARACTER U+%04X" % cp)
    return found


def clean_text(text, keep_bidi=False, ascii_punct=False,
               collapse=True, normalize="NFC"):
    out = []
    for ch in text:
        cp = ord(ch)
        if ch in INVISIBLE:
            continue
        if ch in BIDI:
            if keep_bidi:
                out.append(ch)
            continue
        if TAG_BLOCK[0] <= cp <= TAG_BLOCK[1]:
            continue
        if VARIATION_SELECTORS[0] <= cp <= VARIATION_SELECTORS[1]:
            continue
        if ch in SPACES:
            out.append(" ")
            continue
        if ascii_punct and ch in PUNCT:
            out.append(PUNCT[ch])
            continue
        if ch not in "\n\r\t" and unicodedata.category(ch) in ("Cf", "Cc"):
            continue
        out.append(ch)

    text = "".join(out)
    if normalize:
        text = unicodedata.normalize(normalize, text)
    if collapse:
        # trailing whitespace per line, and never more than two blank lines
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = text.strip("\n") + "\n" if text.strip() else ""
    return text


# ------------------------------------------------------------ file metadata

def clean_ooxml(path, out_path):
    """DOCX/XLSX/PPTX: rebuild the zip with blanked authoring properties.

    The property parts are replaced, not deleted: [Content_Types].xml and
    the package relationships still point at them, and Word/Excel refuse
    to open a package whose declared parts are missing.
    """
    removed = []
    tmp = out_path + ".tmp"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(
            tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            name = item.filename
            replacement = BLANK_PARTS.get(name)
            if replacement is not None:
                zout.writestr(item, replacement)
                removed.append(name + " (blanked)")
                continue
            zout.writestr(item, zin.read(name))
    os.replace(tmp, out_path)
    return removed


BLANK_CORE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<cp:coreProperties '
    'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:dcterms="http://purl.org/dc/terms/" '
    'xmlns:dcmitype="http://purl.org/dc/dcmitype/" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    "<dc:title></dc:title><dc:creator></dc:creator>"
    "<cp:lastModifiedBy></cp:lastModifiedBy>"
    "</cp:coreProperties>"
)

BLANK_APP_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties '
    'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
    "<Application></Application><Company></Company><Manager></Manager>"
    "</Properties>"
)

BLANK_CUSTOM_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
    '<Properties '
    'xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties" '
    'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"/>'
)

BLANK_PARTS = {
    "docProps/core.xml": BLANK_CORE_XML,
    "docProps/app.xml": BLANK_APP_XML,
    "docProps/custom.xml": BLANK_CUSTOM_XML,
}


def clean_pdf(path, out_path):
    """PDF: rebuild the file without the document info dictionary.

    Requires qpdf. `--empty --pages file 1-z --` produces a fresh document
    that carries the pages only, so /Info and the XMP packet are dropped.
    """
    if not shutil.which("qpdf"):
        raise RuntimeError(
            "qpdf is not installed — cannot clean PDF metadata. "
            "Install qpdf, or export the PDF again from a clean source.")
    tmp = out_path + ".tmp"
    cmd = ["qpdf", "--empty", "--pages", path, "1-z", "--", tmp]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    # qpdf exit 3 = warnings only, output is still written
    if proc.returncode not in (0, 3) or not os.path.exists(tmp):
        raise RuntimeError("qpdf failed: " + (proc.stderr or "").strip())
    os.replace(tmp, out_path)
    return ["/Info dictionary", "XMP metadata packet"]


JPEG_KEEP = {0xD8, 0xD9}


def clean_jpeg(path, out_path):
    """JPEG: drop every APPn segment (EXIF, XMP, Photoshop IRB) and comments."""
    data = open(path, "rb").read()
    if data[:2] != b"\xff\xd8":
        raise RuntimeError("not a JPEG")
    out = bytearray(b"\xff\xd8")
    removed = []
    i = 2
    while i < len(data) - 1:
        if data[i] != 0xFF:
            out += data[i:]
            break
        marker = data[i + 1]
        if marker == 0xDA:  # start of scan — rest is image data
            out += data[i:]
            break
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            out += data[i:i + 2]
            i += 2
            continue
        (seg_len,) = struct.unpack(">H", data[i + 2:i + 4])
        seg = data[i:i + 2 + seg_len]
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:
            if marker != 0xE0:  # keep APP0/JFIF, it is structural
                removed.append("APP%d segment" % (marker - 0xE0)
                               if marker != 0xFE else "comment")
                i += 2 + seg_len
                continue
        out += seg
        i += 2 + seg_len
    open(out_path, "wb").write(bytes(out))
    return removed


PNG_STRIP = {b"tEXt", b"iTXt", b"zTXt", b"eXIf", b"tIME", b"iCCP", b"dSIG"}


def clean_png(path, out_path):
    data = open(path, "rb").read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise RuntimeError("not a PNG")
    out = bytearray(data[:8])
    removed = []
    i = 8
    while i < len(data):
        (length,) = struct.unpack(">I", data[i:i + 4])
        ctype = data[i + 4:i + 8]
        chunk = data[i:i + 12 + length]
        if ctype in PNG_STRIP:
            removed.append(ctype.decode("ascii") + " chunk")
        else:
            out += chunk
        i += 12 + length
        if ctype == b"IEND":
            break
    open(out_path, "wb").write(bytes(out))
    return removed


HANDLERS = {
    ".docx": clean_ooxml, ".xlsx": clean_ooxml, ".pptx": clean_ooxml,
    ".pdf": clean_pdf,
    ".jpg": clean_jpeg, ".jpeg": clean_jpeg,
    ".png": clean_png,
}


# ------------------------------------------------------------------- CLI

def cmd_text(args):
    if args.path and args.path != "-":
        raw = open(args.path, encoding="utf-8").read()
    else:
        raw = sys.stdin.read()

    found = analyse(raw, keep_bidi=args.keep_bidi)

    if args.check:
        report = {"clean": not found, "found": found,
                  "total": sum(found.values())}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not found else 1

    cleaned = clean_text(raw, keep_bidi=args.keep_bidi,
                         ascii_punct=args.ascii_punct,
                         collapse=not args.no_collapse)

    if args.in_place:
        if not args.path or args.path == "-":
            sys.exit("--in-place needs a file path")
        open(args.path, "w", encoding="utf-8").write(cleaned)
        summary = {"file": args.path, "removed": found,
                   "total": sum(found.values())}
        print(json.dumps(summary, ensure_ascii=False, indent=2),
              file=sys.stderr)
    elif args.out:
        open(args.out, "w", encoding="utf-8").write(cleaned)
        summary = {"file": args.out, "removed": found,
                   "total": sum(found.values())}
        print(json.dumps(summary, ensure_ascii=False, indent=2),
              file=sys.stderr)
    else:
        sys.stdout.write(cleaned)
    return 0


def cmd_file(args):
    results = []
    exit_code = 0
    for path in args.paths:
        ext = os.path.splitext(path)[1].lower()
        handler = HANDLERS.get(ext)
        entry = {"file": path}
        if not handler:
            entry["status"] = "skipped"
            entry["reason"] = "unsupported type %s" % (ext or "(none)")
            results.append(entry)
            continue
        out_path = path if args.in_place else (
            args.out or _suffixed(path))
        try:
            if args.in_place:
                fd, tmp = tempfile.mkstemp(suffix=ext,
                                           dir=os.path.dirname(path) or ".")
                os.close(fd)
                removed = handler(path, tmp)
                os.replace(tmp, path)
            else:
                removed = handler(path, out_path)
            entry["status"] = "cleaned"
            entry["output"] = out_path if not args.in_place else path
            entry["removed"] = removed
        except Exception as exc:  # noqa: BLE001 — report, do not crash the batch
            entry["status"] = "error"
            entry["reason"] = str(exc)
            exit_code = 1
        results.append(entry)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return exit_code


def _suffixed(path):
    base, ext = os.path.splitext(path)
    return base + ".clean" + ext


def main():
    p = argparse.ArgumentParser(
        prog="clean.py",
        description="Pre-delivery hygiene: invisible Unicode in text, "
                    "authoring metadata in files.")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("text", help="clean a text/markdown file or stdin")
    t.add_argument("path", nargs="?", help="file path, or - for stdin")
    t.add_argument("--check", action="store_true",
                   help="report only, do not modify (exit 1 if dirty)")
    t.add_argument("--in-place", action="store_true")
    t.add_argument("--out", help="write the cleaned text here")
    t.add_argument("--keep-bidi", action="store_true",
                   help="keep RLM/LRM and bidi isolates")
    t.add_argument("--ascii-punct", action="store_true",
                   help="also fold curly quotes, dashes and ellipses to ASCII")
    t.add_argument("--no-collapse", action="store_true",
                   help="do not trim trailing spaces / extra blank lines")
    t.set_defaults(func=cmd_text)

    f = sub.add_parser("file", help="strip metadata from documents and images")
    f.add_argument("paths", nargs="+")
    f.add_argument("--in-place", action="store_true")
    f.add_argument("--out", help="output path (single input only)")
    f.set_defaults(func=cmd_file)

    args = p.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
