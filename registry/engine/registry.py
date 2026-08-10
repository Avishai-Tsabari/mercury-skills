#!/usr/bin/env python3
"""
Registry Engine — generic, schema-driven, JSON-backed flat registry CLI.

One engine, many registries. A registry = a directory containing:
    schema.json    field definitions (the contract)
    data.json      the records (source of truth)
    history.jsonl  append-only change log

Usage:
    registry.py --dir <registry-dir> <command> [...]
    REGISTRY_DIR=<dir> registry.py <command> [...]

Zero dependencies. Python 3.9+.
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

TYPES = ("text", "number", "date", "bool", "enum", "list", "url")


# ---------- paths ----------

def reg_dir(args):
    d = args.dir or os.environ.get("REGISTRY_DIR")
    if not d:
        die("No registry directory given (--dir or REGISTRY_DIR)")
    return Path(d)


def die(msg, code=1):
    print(f"❌ {msg}")
    sys.exit(code)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------- schema ----------

def load_schema(d: Path):
    p = d / "schema.json"
    if not p.exists():
        die(f"No schema found at {p}. Run 'init' first.")
    s = json.loads(p.read_text(encoding="utf-8"))
    s.setdefault("key", "slug")
    s.setdefault("fields", [])
    s.setdefault("sort", [s["key"]])
    if not any(f["name"] == s["key"] for f in s["fields"]):
        s["fields"].insert(0, {"name": s["key"], "type": "text", "required": True})
    return s


def save_schema(d: Path, s):
    d.mkdir(parents=True, exist_ok=True)
    (d / "schema.json").write_text(
        json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def field(schema, name):
    for f in schema["fields"]:
        if f["name"] == name:
            return f
    return None


# ---------- data ----------

def load_data(d: Path):
    p = d / "data.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def save_data(d: Path, schema, rows):
    keys = schema.get("sort") or [schema["key"]]
    rows.sort(key=lambda r: tuple(str(r.get(k) or "") for k in keys))
    d.mkdir(parents=True, exist_ok=True)
    (d / "data.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def log(d: Path, action, key, detail=""):
    with (d / "history.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "action": action, "key": key,
                            "detail": detail}, ensure_ascii=False) + "\n")


def find(rows, keyfield, val):
    return next((r for r in rows if str(r.get(keyfield)) == str(val)), None)


# ---------- coercion & validation ----------

def coerce(f, raw):
    """Convert a CLI string into the typed value for field f."""
    t = f.get("type", "text")
    if raw is None:
        return None
    if raw == "":
        return None
    if t == "number":
        try:
            return float(raw) if "." in str(raw) else int(raw)
        except ValueError:
            die(f"Field '{f['name']}' must be a number (got '{raw}')")
    if t == "bool":
        return str(raw).lower() in ("1", "true", "yes", "y")
    if t == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(raw)):
            die(f"Field '{f['name']}' must be a YYYY-MM-DD date (got '{raw}')")
        return str(raw)
    if t == "list":
        return [x.strip() for x in str(raw).split(",") if x.strip()]
    if t == "enum":
        vals = f.get("values", [])
        if raw not in vals:
            if f.get("open"):
                vals.append(raw)          # open enum grows dynamically
            else:
                die(f"Invalid value for field '{f['name']}': '{raw}'. Allowed: {', '.join(vals)}")
        return raw
    if t == "url":
        if not (str(raw).startswith("http") or "/" in str(raw)):
            die(f"Field '{f['name']}' does not look like a valid link: '{raw}'")
    return str(raw)


def validate_rows(schema, rows):
    errs, seen = [], set()
    k = schema["key"]
    for r in rows:
        kv = r.get(k)
        if not kv:
            errs.append(f"Record without '{k}'")
            continue
        if kv in seen:
            errs.append(f"{kv}: duplicate key")
        seen.add(kv)
        for f in schema["fields"]:
            n, t = f["name"], f.get("type", "text")
            v = r.get(n)
            if f.get("required") and (v is None or v == "" or v == []):
                errs.append(f"{kv}: missing required field '{n}'")
            if v is None:
                continue
            if t == "enum" and not f.get("open") and v not in f.get("values", []):
                errs.append(f"{kv}: invalid value in field '{n}': '{v}'")
            if t == "list" and not isinstance(v, list):
                errs.append(f"{kv}: field '{n}' must be a list")
            if t == "number" and not isinstance(v, (int, float)):
                errs.append(f"{kv}: field '{n}' must be a number")
            if t == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(v)):
                errs.append(f"{kv}: field '{n}' is not a valid date")
    return errs


# ---------- display ----------

def label(schema, f, val):
    if val is None or val == "" or val == []:
        return ""
    if f.get("type") == "list":
        return ", ".join(str(x) for x in val)
    if f.get("type") == "bool":
        return "✅" if val else "—"
    if f.get("type") == "enum":
        return f.get("labels", {}).get(val, val)
    return str(val)


def fmt_row(schema, r):
    k = schema["key"]
    disp = schema.get("display") or [f["name"] for f in schema["fields"][:4]]
    parts = []
    for n in disp:
        if n == k:
            continue
        f = field(schema, n)
        if not f:
            continue
        v = label(schema, f, r.get(n))
        if v:
            parts.append(v)
    return f"  {str(r.get(k)):22s} {' · '.join(parts)}"


# ---------- commands ----------

def cmd_init(args):
    d = reg_dir(args)
    if (d / "schema.json").exists() and not args.force:
        die(f"A schema already exists at {d}. Use --force to overwrite.")
    if args.preset:
        p = Path(__file__).resolve().parent.parent / "presets" / f"{args.preset}.json"
        if not p.exists():
            die(f"No preset '{args.preset}'. Available: "
                f"{', '.join(sorted(x.stem for x in p.parent.glob('*.json')))}")
        schema = json.loads(p.read_text(encoding="utf-8"))
    else:
        schema = {"name": args.name or d.name, "title": args.title or d.name,
                  "key": "slug", "fields": [
                      {"name": "slug", "type": "text", "required": True},
                      {"name": "name", "type": "text", "required": True},
                      {"name": "notes", "type": "text"}],
                  "display": ["name", "notes"], "sort": ["slug"]}
    if args.name:
        schema["name"] = args.name
    if args.title:
        schema["title"] = args.title
    save_schema(d, schema)
    if not (d / "data.json").exists():
        save_data(d, schema, [])
    print(f"✅ Created registry '{schema['name']}' at {d} "
          f"({len(schema['fields'])} fields)")
    print("   Fields: " + ", ".join(f["name"] for f in schema["fields"]))


def cmd_fields(args):
    schema = load_schema(reg_dir(args))
    print(f"📐 {schema.get('title', schema['name'])} — key: {schema['key']}")
    for f in schema["fields"]:
        bits = [f.get("type", "text")]
        if f.get("required"):
            bits.append("required")
        if f.get("type") == "enum":
            bits.append("|".join(f.get("values", [])) + (" (open)" if f.get("open") else ""))
        if f.get("default") is not None:
            bits.append(f"default={f['default']}")
        print(f"  {f['name']:22s} [{'  '.join(bits)}]  {f.get('desc','')}")


def collect(schema, args, only_given=True):
    out = {}
    for f in schema["fields"]:
        raw = getattr(args, f["name"].replace("-", "_"), None)
        if raw is None and only_given:
            continue
        out[f["name"]] = coerce(f, raw)
    return out


def cmd_add(args):
    d = reg_dir(args)
    schema = load_schema(d)
    rows = load_data(d)
    k = schema["key"]
    kv = args.key_value
    if find(rows, k, kv):
        die(f"'{kv}' already exists. Use update.")
    rec = {k: kv, "created_at": today(), "updated_at": today()}
    for f in schema["fields"]:
        if f["name"] == k:
            continue
        if f.get("default") is not None:
            rec[f["name"]] = f["default"]
    rec.update(collect(schema, args))
    missing = [f["name"] for f in schema["fields"]
               if f.get("required") and not rec.get(f["name"])]
    if missing:
        die(f"Missing required fields: {', '.join(missing)}")
    rows.append(rec)
    save_data(d, schema, rows)
    save_schema(d, schema)      # open enums may have grown
    log(d, "add", kv, "")
    print(f"✅ Added '{kv}' to {schema.get('title', schema['name'])}")


def cmd_update(args):
    d = reg_dir(args)
    schema = load_schema(d)
    rows = load_data(d)
    k = schema["key"]
    r = find(rows, k, args.key_value)
    if not r:
        die(f"'{args.key_value}' not found")
    changes = collect(schema, args)
    changes.pop(k, None)
    if not changes:
        die("No field given to update")
    detail = []
    for n, v in changes.items():
        old = r.get(n)
        if old != v:
            detail.append(f"{n}: {old!r} → {v!r}")
        r[n] = v
    r["updated_at"] = today()
    save_data(d, schema, rows)
    save_schema(d, schema)
    log(d, "update", args.key_value, "; ".join(detail))
    print(f"✅ Updated '{args.key_value}'")
    for line in detail:
        print(f"   • {line}")


def cmd_archive(args):
    d = reg_dir(args)
    schema = load_schema(d)
    rows = load_data(d)
    r = find(rows, schema["key"], args.key_value)
    if not r:
        die(f"'{args.key_value}' not found")
    r["archived"] = True
    r["archived_at"] = today()
    if args.reason:
        r["notes"] = ((r.get("notes") or "") + f" | archived {today()}: {args.reason}").strip(" |")
    save_data(d, schema, rows)
    log(d, "archive", args.key_value, args.reason or "")
    print(f"🗄️ '{args.key_value}' archived (not deleted — still in data.json).")


def cmd_remove(args):
    """Hard delete. Requires --yes; prefer archive."""
    d = reg_dir(args)
    schema = load_schema(d)
    rows = load_data(d)
    r = find(rows, schema["key"], args.key_value)
    if not r:
        die(f"'{args.key_value}' not found")
    if not args.yes:
        print(f"⚠️ Permanent deletion of '{args.key_value}'. Add --yes to confirm "
              f"(or use archive, which is reversible).")
        sys.exit(2)
    rows = [x for x in rows if x is not r]
    save_data(d, schema, rows)
    log(d, "remove", args.key_value, json.dumps(r, ensure_ascii=False))
    print(f"🗑️ '{args.key_value}' deleted. (Full record kept in history.jsonl)")


def matches(schema, r, args):
    for f in schema["fields"]:
        want = getattr(args, "f_" + f["name"].replace("-", "_"), None)
        if want is None:
            continue
        have = r.get(f["name"])
        if f.get("type") == "list":
            if want not in (have or []):
                return False
        elif str(have) != str(coerce(f, want)):
            return False
    if args.search:
        blob = json.dumps(r, ensure_ascii=False).lower()
        if args.search.lower() not in blob:
            return False
    if args.changed_since:
        if (r.get("updated_at") or "") < args.changed_since:
            return False
    return True


def cmd_list(args):
    d = reg_dir(args)
    schema = load_schema(d)
    rows = [r for r in load_data(d) if args.all or not r.get("archived")]
    rows = [r for r in rows if matches(schema, r, args)]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    gb = args.group_by or schema.get("group_by")
    print(f"📋 {schema.get('title', schema['name'])} — {len(rows)} records")
    if gb:
        f = field(schema, gb)
        groups = {}
        for r in rows:
            groups.setdefault(r.get(gb) or "—", []).append(r)
        for g in sorted(groups):
            head = f.get("labels", {}).get(g, g) if f else g
            print(f"\n### {head} ({len(groups[g])})")
            for r in groups[g]:
                print(fmt_row(schema, r))
    else:
        for r in rows:
            print(fmt_row(schema, r))


def cmd_show(args):
    d = reg_dir(args)
    schema = load_schema(d)
    r = find(load_data(d), schema["key"], args.key_value)
    if not r:
        die(f"'{args.key_value}' not found")
    print(f"📄 {r.get(schema['key'])}")
    for f in schema["fields"]:
        v = label(schema, f, r.get(f["name"]))
        if v:
            print(f"   {f['name']:20s} {v}")
    for meta in ("created_at", "updated_at", "archived_at"):
        if r.get(meta):
            print(f"   {meta:20s} {r[meta]}")


def cmd_history(args):
    d = reg_dir(args)
    p = d / "history.jsonl"
    if not p.exists():
        print("No history.")
        return
    lines = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.key_value:
        lines = [l for l in lines if l["key"] == args.key_value]
    for l in lines[-args.limit:]:
        print(f"{l['ts']}  {l['action']:8s} {l['key']:22s} {l.get('detail','')}")


def cmd_validate(args):
    d = reg_dir(args)
    schema = load_schema(d)
    errs = validate_rows(schema, load_data(d))
    if errs:
        print("❌ Problems found:")
        for e in errs:
            print(f"   • {e}")
        sys.exit(1)
    print(f"✅ Valid — {len(load_data(d))} records.")


def cmd_export(args):
    d = reg_dir(args)
    schema = load_schema(d)
    rows = [r for r in load_data(d) if not r.get("archived")]
    cols = schema.get("export_columns") or schema.get("display") or \
        [f["name"] for f in schema["fields"]]
    title = schema.get("title", schema["name"])
    out = [f"# {title}", "",
           "> ⚠️ **Auto-generated file — do not edit by hand.**",
           f"> Source of truth: `{d}/data.json` · Export: `registry export`",
           f"> Last updated: {today()}", "",
           "| " + " | ".join(cols) + " |",
           "|" + "|".join(["---"] * len(cols)) + "|"]
    for r in rows:
        cells = [label(schema, field(schema, c) or {"name": c}, r.get(c)).replace("|", "\\|")
                 for c in cols]
        out.append("| " + " | ".join(cells) + " |")
    out.append("")
    target = Path(args.out or schema.get("export") or (d / f"{schema['name']}.md"))
    target.write_text("\n".join(out), encoding="utf-8")
    print(f"✅ Exported to {target} ({len(rows)} records)")


# ---------- parser ----------

def build_parser(schema):
    p = argparse.ArgumentParser(prog="registry", add_help=True)
    p.add_argument("--dir")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init", help="Create a new registry")
    i.add_argument("--name"); i.add_argument("--title")
    i.add_argument("--preset"); i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    sub.add_parser("fields", help="Show the schema").set_defaults(func=cmd_fields)

    def add_field_args(sp):
        for f in schema["fields"]:
            if f["name"] == schema["key"]:
                continue
            sp.add_argument(f"--{f['name']}", help=f.get("desc", f.get("type", "text")))

    a = sub.add_parser("add"); a.add_argument("key_value"); add_field_args(a)
    a.set_defaults(func=cmd_add)

    u = sub.add_parser("update"); u.add_argument("key_value"); add_field_args(u)
    u.set_defaults(func=cmd_update)

    ar = sub.add_parser("archive"); ar.add_argument("key_value")
    ar.add_argument("--reason"); ar.set_defaults(func=cmd_archive)

    rm = sub.add_parser("remove"); rm.add_argument("key_value")
    rm.add_argument("--yes", action="store_true"); rm.set_defaults(func=cmd_remove)

    l = sub.add_parser("list")
    for f in schema["fields"]:
        l.add_argument(f"--{f['name']}", dest="f_" + f["name"].replace("-", "_"))
    l.add_argument("--search")
    l.add_argument("--changed-since", dest="changed_since", metavar="YYYY-MM-DD",
                   help="Records updated on or after this date")
    l.add_argument("--group-by"); l.add_argument("--all", action="store_true")
    l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show"); s.add_argument("key_value"); s.set_defaults(func=cmd_show)

    h = sub.add_parser("history"); h.add_argument("key_value", nargs="?")
    h.add_argument("--limit", type=int, default=20); h.set_defaults(func=cmd_history)

    sub.add_parser("validate").set_defaults(func=cmd_validate)
    e = sub.add_parser("export"); e.add_argument("--out"); e.set_defaults(func=cmd_export)
    return p


def main():
    # peek at --dir so the parser can be built from the live schema
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--dir")
    known, _ = pre.parse_known_args()
    d = known.dir or os.environ.get("REGISTRY_DIR")
    schema = {"key": "slug", "fields": []}
    if d and (Path(d) / "schema.json").exists():
        schema = load_schema(Path(d))
    args = build_parser(schema).parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
