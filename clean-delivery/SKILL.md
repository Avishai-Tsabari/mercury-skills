---
name: clean-delivery
description: Pre-delivery hygiene for anything that leaves the house — strip invisible/exotic Unicode from generated text (it breaks Hebrew RTL, Word, WordPress and newsletters) and strip authoring metadata from DOCX/PDF/JPEG/PNG. Use before sending an article, proposal, report or document to a client. Never rewrites wording.
---

# clean-delivery — the last step before a file leaves the house

## When this is the right skill

Anything produced by a model or exported from a local tool and then **handed to someone outside the team**: an article, a proposal, a report, a PDF, a newsletter draft.

Two problems, both invisible until they embarrass you:

1. **Text carries characters nobody typed.** Models emit zero-width spaces, no-break spaces, word joiners, bidi marks and — increasingly — Unicode *tag* characters that can hide an entire payload inside ordinary-looking prose. In Hebrew this is not cosmetic: a stray RLM or NBSP **breaks RTL layout**, and pasting into WordPress or Word drags the junk along.
2. **Files carry authoring metadata.** Every DOCX and PDF you export names the author, the tool and sometimes the local filesystem path. A proposal sent to a client should not disclose who wrote it, on which machine, with which template.

## What this skill deliberately does NOT do

It **never rewrites wording to disguise that text was written by AI.** Tools in this space usually bundle a "beat the detector" layer; we left it out on purpose. We sell AI content systems openly — hiding it is a reputational risk to the client, and the technique is unprovable anyway. This skill only removes what should not have been in the file in the first place.

## How it's built

```
clean-delivery/
  SKILL.md
  engine/clean.py     Python 3.9+, zero dependencies
```

PDF cleaning shells out to `qpdf` when it is on PATH. Everything else — OOXML, JPEG, PNG, all text handling — is pure stdlib.

## Commands

```bash
C=<SKILL_DIR>/engine/clean.py

# --- text ---------------------------------------------------------------
python3 $C text article.md --check          # report only; exit 1 if dirty
python3 $C text article.md                  # cleaned text to stdout
python3 $C text article.md --in-place       # rewrite the file
python3 $C text draft.md --out final.md
cat draft.md | python3 $C text -             # stdin → stdout

# --- files --------------------------------------------------------------
python3 $C file proposal.pdf                # → proposal.clean.pdf
python3 $C file report.docx cover.jpg --in-place
```

### Text flags

| Flag | Effect |
|---|---|
| `--check` | Report what was found, change nothing. Exit code 1 = dirty. Use in a pre-send gate. |
| `--keep-bidi` | Keep RLM/LRM and bidi isolates. Use only when a Hebrew document genuinely mixes directions and relies on explicit marks. |
| `--ascii-punct` | Also fold curly quotes, em dashes and ellipses to ASCII. **Off by default** — for a magazine article you usually want the real typography. Turn it on for plain-text email, CSV or code. |
| `--no-collapse` | Keep trailing spaces and runs of blank lines. |

Default behaviour: remove invisible characters, remove bidi controls, turn exotic spaces into a plain space, drop Unicode tag characters and variation selectors, normalise to NFC, trim trailing whitespace, collapse 3+ blank lines to 2.

### Supported file types

| Type | What is removed |
|---|---|
| `.docx` `.xlsx` `.pptx` | `docProps/core.xml`, `app.xml`, `custom.xml` — blanked, not deleted (a package whose declared parts are missing will not open) |
| `.pdf` | Document info dictionary and the XMP packet, by rebuilding the file from its pages. **Requires `qpdf`.** |
| `.jpg` `.jpeg` | Every APP1–APP15 segment (EXIF, XMP, Photoshop IRB) and comments. APP0/JFIF is kept — it is structural. |
| `.png` | `tEXt` `iTXt` `zTXt` `eXIf` `tIME` `iCCP` `dSIG` chunks |

Unsupported types are reported as `skipped`, never silently passed. Output is JSON, one entry per file.

## Working with the user

1. **Default to `--check` first** on anything you did not generate yourself. Show what was found before changing a file the user cares about.
2. **Never clean in place without saying so.** For a client-facing document prefer the `.clean.` copy, so the original stays available.
3. **Two flags need a decision, not a default:** `--keep-bidi` (Hebrew documents with mixed direction) and `--ascii-punct` (destroys intentional typography). Ask when it matters.
4. **Image EXIF is a privacy call, not a formatting one** — a photo from a phone carries GPS coordinates. Mention it when the user attaches photos to something public.

## Where it fits

- **Content pipeline** (article generation, interview → article): run `text --check` on every draft before it reaches the client, and `text --in-place` before publishing.
- **Proposals and reports**: run `file` on the exported PDF before it is sent.
- **Privacy baseline**: metadata stripping is the cheap end of "do not leak what you did not mean to send".
