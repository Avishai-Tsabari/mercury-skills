#!/usr/bin/env python3
"""
Tracker Engine — generic, schema-driven, SQLite-backed work tracker.

A tracker = a directory containing:
    schema.json    item fields, statuses, priorities, hierarchy config
    tracker.db     SQLite (items + item_history + groups)

Use a tracker only when items MOVE: status changes, an owner, a due date,
or grouping under a parent. Otherwise use the `registry` skill instead.

Usage:
    tracker.py --dir <tracker-dir> <command> [...]
    TRACKER_DIR=<dir> tracker.py <command> [...]

Zero dependencies. Python 3.9+.
"""

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DDL = """
CREATE TABLE IF NOT EXISTS groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    parent_id INTEGER REFERENCES groups(id),
    level INTEGER NOT NULL DEFAULT 0,
    meta TEXT DEFAULT '',
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(slug, parent_id)
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    group_id INTEGER REFERENCES groups(id),
    subgroup_id INTEGER REFERENCES groups(id),
    assignee TEXT,
    status TEXT NOT NULL,
    priority TEXT,
    due_date TEXT,
    tags TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    extra TEXT DEFAULT '{}',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    closed_at TEXT
);
CREATE TABLE IF NOT EXISTS item_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    changed_by TEXT DEFAULT 'mercury',
    changed_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_assignee ON items(assignee);
CREATE INDEX IF NOT EXISTS idx_items_group ON items(group_id);
CREATE INDEX IF NOT EXISTS idx_hist_item ON item_history(item_id);
"""

DEFAULT_SCHEMA = {
    "name": "tracker",
    "title": "Tracker",
    "item_label": "item",
    "group_label": "project",
    "subgroup_label": "module",
    "statuses": {"open": "🔲 Open", "in-progress": "🔄 In progress",
                 "done": "✅ Done", "blocked": "⛔ Blocked", "hold": "⏸️ On hold"},
    "closed_statuses": ["done"],
    "default_status": "open",
    "priorities": {"p0": "🔥 Critical", "p1": "🔴 High", "p2": "🟡 Normal", "p3": "⚪ Low"},
    "default_priority": "p2",
    "extra_fields": []
}


def die(msg, code=1):
    print(f"❌ {msg}")
    sys.exit(code)


def tdir(args):
    d = args.dir or os.environ.get("TRACKER_DIR")
    if not d:
        die("No tracker directory given (--dir or TRACKER_DIR)")
    return Path(d)


def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_schema(d: Path):
    p = d / "schema.json"
    if not p.exists():
        die(f"No schema found at {p}. Run 'init' first.")
    s = dict(DEFAULT_SCHEMA)
    s.update(json.loads(p.read_text(encoding="utf-8")))
    return s


def db(d: Path):
    d.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(d / "tracker.db")
    con.row_factory = sqlite3.Row
    con.executescript(DDL)
    return con


def hist(con, item_id, field, old, new):
    con.execute("INSERT INTO item_history(item_id,field,old_value,new_value) VALUES(?,?,?,?)",
                (item_id, field, str(old) if old is not None else None,
                 str(new) if new is not None else None))


def get_group(con, slug, parent_id=None):
    return con.execute(
        "SELECT * FROM groups WHERE slug=? AND (parent_id IS ? OR parent_id=?)",
        (slug, parent_id, parent_id)).fetchone()


# ---------- presets ----------

def preset_dirs():
    """Preset search path: space-local presets shadow built-ins."""
    return [
        (Path.cwd() / "trackers" / "presets", "space"),
        (Path(__file__).resolve().parent.parent / "presets", "built-in"),
    ]


def resolve_preset(name):
    for d, _tag in preset_dirs():
        p = d / f"{name}.json"
        if p.exists():
            return p
    avail = []
    for d, tag in preset_dirs():
        if d.is_dir():
            avail += [f"{x.stem} [{tag}]" for x in sorted(d.glob("*.json"))]
    die(f"No preset '{name}'. Available: {', '.join(avail) or '(none)'}")


# ---------- commands ----------

def cmd_init(args):
    d = tdir(args)
    if (d / "schema.json").exists() and not args.force:
        die(f"A schema already exists at {d}. Use --force.")
    s = dict(DEFAULT_SCHEMA)
    if args.preset:
        p = resolve_preset(args.preset)
        s.update(json.loads(p.read_text(encoding="utf-8")))
    if args.name:
        s["name"] = args.name
    if args.title:
        s["title"] = args.title
    d.mkdir(parents=True, exist_ok=True)
    (d / "schema.json").write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
    db(d).close()
    print(f"✅ Created tracker '{s['name']}' at {d}")
    print(f"   Statuses: {', '.join(s['statuses'])}")
    print(f"   Hierarchy: {s['group_label']} → {s['subgroup_label']} → {s['item_label']}")


def cmd_group_add(args):
    d = tdir(args); s = load_schema(d); con = db(d)
    parent_id = None
    if args.parent:
        g = get_group(con, args.parent)
        if not g:
            die(f"{s['group_label']} '{args.parent}' not found")
        parent_id = g["id"]
    if get_group(con, args.slug, parent_id):
        die(f"'{args.slug}' already exists")
    con.execute("INSERT INTO groups(slug,name,parent_id,level,meta) VALUES(?,?,?,?,?)",
                (args.slug, args.name or args.slug, parent_id,
                 1 if parent_id else 0, args.meta or ""))
    con.commit()
    lbl = s["subgroup_label"] if parent_id else s["group_label"]
    print(f"✅ Added {lbl} '{args.slug}'")


def cmd_group_list(args):
    d = tdir(args); s = load_schema(d); con = db(d)
    rows = con.execute("SELECT * FROM groups ORDER BY level, slug").fetchall()
    tops = [r for r in rows if r["parent_id"] is None]
    print(f"🗂️ {s['group_label']}s — {len(tops)}")
    for t in tops:
        n = con.execute("SELECT COUNT(*) c FROM items WHERE group_id=?", (t["id"],)).fetchone()["c"]
        print(f"  {t['slug']:20s} {t['name']}  ({n})")
        for c in [r for r in rows if r["parent_id"] == t["id"]]:
            print(f"    └ {c['slug']:16s} {c['name']}")


def resolve_groups(con, s, group, subgroup):
    gid = sgid = None
    if group:
        g = get_group(con, group)
        if not g:
            die(f"{s['group_label']} '{group}' not found — add it with 'group add'")
        gid = g["id"]
    if subgroup:
        sg = get_group(con, subgroup, gid)
        if not sg:
            die(f"{s['subgroup_label']} '{subgroup}' not found under '{group}'")
        sgid = sg["id"]
    return gid, sgid


def cmd_add(args):
    d = tdir(args); s = load_schema(d); con = db(d)
    status = args.status or s["default_status"]
    if status not in s["statuses"]:
        die(f"Invalid status '{status}'. Allowed: {', '.join(s['statuses'])}")
    prio = args.priority or s.get("default_priority")
    if prio and s.get("priorities") and prio not in s["priorities"]:
        die(f"Invalid priority '{prio}'. Allowed: {', '.join(s['priorities'])}")
    gid, sgid = resolve_groups(con, s, args.group, args.subgroup)
    extra = {}
    for f in s.get("extra_fields", []):
        v = getattr(args, f["name"].replace("-", "_"), None)
        if v is not None:
            extra[f["name"]] = v
    cur = con.execute(
        "INSERT INTO items(title,group_id,subgroup_id,assignee,status,priority,"
        "due_date,tags,notes,extra) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (args.title, gid, sgid, args.assignee, status, prio, args.due,
         args.tags or "", args.notes or "", json.dumps(extra, ensure_ascii=False)))
    hist(con, cur.lastrowid, "created", None, args.title)
    con.commit()
    print(f"✅ #{cur.lastrowid} — {args.title}")


def cmd_update(args):
    d = tdir(args); s = load_schema(d); con = db(d)
    r = con.execute("SELECT * FROM items WHERE id=?", (args.id,)).fetchone()
    if not r:
        die(f"Item #{args.id} not found")
    changes = {}
    for f in ("title", "assignee", "status", "priority", "tags", "notes"):
        v = getattr(args, f)
        if v is not None:
            changes[f] = v
    if args.due is not None:
        changes["due_date"] = args.due
    if changes.get("status") and changes["status"] not in s["statuses"]:
        die(f"Invalid status '{changes['status']}'")
    if changes.get("priority") and s.get("priorities") and changes["priority"] not in s["priorities"]:
        die(f"Invalid priority '{changes['priority']}'")
    if args.group or args.subgroup:
        gid, sgid = resolve_groups(con, s, args.group, args.subgroup)
        if args.group:
            changes["group_id"] = gid
        if args.subgroup:
            changes["subgroup_id"] = sgid
    if not changes:
        die("No field given to update")
    for f, v in changes.items():
        if str(r[f]) != str(v):
            hist(con, args.id, f, r[f], v)
    sets = ", ".join(f"{f}=?" for f in changes) + ", updated_at=datetime('now')"
    if changes.get("status") in s.get("closed_statuses", []):
        sets += ", closed_at=datetime('now')"
    elif changes.get("status"):
        sets += ", closed_at=NULL"    # reopened — clear the stale close stamp
    con.execute(f"UPDATE items SET {sets} WHERE id=?", (*changes.values(), args.id))
    con.commit()
    print(f"✅ #{args.id} updated: " + ", ".join(changes))


def cmd_list(args):
    d = tdir(args); s = load_schema(d); con = db(d)
    q = ("SELECT i.*, g.slug gslug, sg.slug sgslug FROM items i "
         "LEFT JOIN groups g ON g.id=i.group_id "
         "LEFT JOIN groups sg ON sg.id=i.subgroup_id WHERE 1=1")
    p = []
    if args.status:
        q += " AND i.status=?"; p.append(args.status)
    elif not args.all:
        closed = s.get("closed_statuses", ["done"])
        q += f" AND i.status NOT IN ({','.join('?' * len(closed))})"; p += closed
    for col, val in (("assignee", args.assignee), ("priority", args.priority),
                     ("g.slug", args.group), ("sg.slug", args.subgroup)):
        if val:
            q += f" AND {col}=?"; p.append(val)
    if args.due_before:
        q += " AND i.due_date IS NOT NULL AND i.due_date<=?"; p.append(args.due_before)
    if args.tag:
        q += " AND i.tags LIKE ?"; p.append(f"%{args.tag}%")
    if args.search:
        q += " AND (i.title LIKE ? OR i.notes LIKE ?)"; p += [f"%{args.search}%"] * 2
    q += " ORDER BY i.priority, i.due_date IS NULL, i.due_date, i.id"
    rows = con.execute(q, p).fetchall()
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2)); return
    print(f"📋 {s['title']} — {len(rows)} items")
    key = args.group_by or "assignee"
    groups = {}
    for r in rows:
        groups.setdefault(r[key] if key in r.keys() and r[key] else "—", []).append(r)
    for g in sorted(groups):
        print(f"\n### {g} ({len(groups[g])})")
        for r in groups[g]:
            st = s["statuses"].get(r["status"], r["status"])
            pr = (s.get("priorities") or {}).get(r["priority"], r["priority"] or "")
            due = f" 📅 {r['due_date']}" if r["due_date"] else ""
            where = f" [{r['gslug']}]" if r["gslug"] else ""
            print(f"  #{r['id']:<4} {st}  {pr}  {r['title']}{where}{due}")


def cmd_show(args):
    d = tdir(args); s = load_schema(d); con = db(d)
    r = con.execute(
        "SELECT i.*, g.slug gslug, sg.slug sgslug FROM items i "
        "LEFT JOIN groups g ON g.id=i.group_id LEFT JOIN groups sg ON sg.id=i.subgroup_id "
        "WHERE i.id=?", (args.id,)).fetchone()
    if not r:
        die(f"Item #{args.id} not found")
    print(f"#{r['id']} — {r['title']}")
    print(f"   status   {s['statuses'].get(r['status'], r['status'])}")
    if r["priority"]:
        print(f"   priority {(s.get('priorities') or {}).get(r['priority'], r['priority'])}")
    for lbl, v in (("assignee", r["assignee"]), (s["group_label"], r["gslug"]),
                   (s["subgroup_label"], r["sgslug"]), ("due", r["due_date"]),
                   ("tags", r["tags"]), ("notes", r["notes"]),
                   ("created", r["created_at"]), ("updated", r["updated_at"])):
        if v:
            print(f"   {lbl:8s} {v}")
    ex = json.loads(r["extra"] or "{}")
    for k, v in ex.items():
        print(f"   {k:8s} {v}")


def cmd_history(args):
    d = tdir(args); con = db(d)
    q = "SELECT * FROM item_history"
    p = []
    if args.id:
        q += " WHERE item_id=?"; p.append(args.id)
    q += " ORDER BY id DESC LIMIT ?"; p.append(args.limit)
    for r in reversed(con.execute(q, p).fetchall()):
        print(f"{r['changed_at']}  #{r['item_id']:<4} {r['field']:10s} "
              f"{r['old_value']} → {r['new_value']}")


def cmd_report(args):
    d = tdir(args); s = load_schema(d); con = db(d)
    closed = s.get("closed_statuses", ["done"])
    print(f"📊 {s['title']} — {today()}\n")
    tot = con.execute("SELECT status, COUNT(*) c FROM items GROUP BY status").fetchall()
    active = sum(r["c"] for r in tot if r["status"] not in closed)
    print(f"Total active: {active}")
    for r in tot:
        print(f"   {s['statuses'].get(r['status'], r['status'])}: {r['c']}")
    print("\nBy assignee:")
    ph = ",".join("?" * len(closed))
    for r in con.execute(
            f"SELECT COALESCE(assignee,'—') a, COUNT(*) c FROM items "
            f"WHERE status NOT IN ({ph}) GROUP BY a ORDER BY c DESC", closed):
        print(f"   {r['a']:14s} {r['c']}")
    over = con.execute(
        f"SELECT id,title,due_date FROM items WHERE status NOT IN ({ph}) "
        f"AND due_date IS NOT NULL AND due_date < ? ORDER BY due_date",
        (*closed, today())).fetchall()
    if over:
        print("\n⚠️ Past due:")
        for r in over:
            print(f"   #{r['id']} {r['title']} (due {r['due_date']})")
    blocked = [k for k in s["statuses"] if k == "blocked"]
    if blocked:
        rows = con.execute("SELECT id,title,notes FROM items WHERE status='blocked'").fetchall()
        print(f"\n⛔ Blocked: {len(rows)}")
        for r in rows:
            print(f"   #{r['id']} {r['title']} — {r['notes'] or 'no reason recorded'}")


def cmd_delete(args):
    d = tdir(args); con = db(d)
    r = con.execute("SELECT * FROM items WHERE id=?", (args.id,)).fetchone()
    if not r:
        die(f"Item #{args.id} not found")
    if not args.yes:
        print(f"⚠️ Permanent deletion of #{args.id} — {r['title']}. Add --yes to confirm.")
        sys.exit(2)
    hist(con, args.id, "deleted", r["title"], None)
    con.execute("DELETE FROM items WHERE id=?", (args.id,))
    con.commit()
    print(f"🗑️ #{args.id} deleted.")


def main():
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--dir")
    known, _ = pre.parse_known_args()
    d = known.dir or os.environ.get("TRACKER_DIR")
    s = dict(DEFAULT_SCHEMA)
    if d and (Path(d) / "schema.json").exists():
        s = load_schema(Path(d))

    p = argparse.ArgumentParser(prog="tracker")
    p.add_argument("--dir")
    sub = p.add_subparsers(dest="cmd", required=True)

    i = sub.add_parser("init")
    i.add_argument("--name"); i.add_argument("--title")
    i.add_argument("--preset"); i.add_argument("--force", action="store_true")
    i.set_defaults(func=cmd_init)

    g = sub.add_parser("group"); gs = g.add_subparsers(dest="gcmd", required=True)
    ga = gs.add_parser("add"); ga.add_argument("slug"); ga.add_argument("--name")
    ga.add_argument("--parent"); ga.add_argument("--meta"); ga.set_defaults(func=cmd_group_add)
    gs.add_parser("list").set_defaults(func=cmd_group_list)

    a = sub.add_parser("add"); a.add_argument("title")
    a.add_argument("--group"); a.add_argument("--subgroup"); a.add_argument("--assignee")
    a.add_argument("--status"); a.add_argument("--priority"); a.add_argument("--due")
    a.add_argument("--tags"); a.add_argument("--notes")
    for f in s.get("extra_fields", []):
        a.add_argument(f"--{f['name']}", help=f.get("desc", ""))
    a.set_defaults(func=cmd_add)

    u = sub.add_parser("update"); u.add_argument("id", type=int)
    for opt in ("title", "group", "subgroup", "assignee", "status",
                "priority", "due", "tags", "notes"):
        u.add_argument(f"--{opt}")
    u.set_defaults(func=cmd_update)

    l = sub.add_parser("list")
    for opt in ("status", "assignee", "priority", "group", "subgroup",
                "tag", "search", "due-before", "group-by"):
        l.add_argument(f"--{opt}")
    l.add_argument("--all", action="store_true"); l.add_argument("--json", action="store_true")
    l.set_defaults(func=cmd_list)

    sh = sub.add_parser("show"); sh.add_argument("id", type=int); sh.set_defaults(func=cmd_show)
    h = sub.add_parser("history"); h.add_argument("id", type=int, nargs="?")
    h.add_argument("--limit", type=int, default=20); h.set_defaults(func=cmd_history)
    sub.add_parser("report").set_defaults(func=cmd_report)
    dl = sub.add_parser("delete"); dl.add_argument("id", type=int)
    dl.add_argument("--yes", action="store_true"); dl.set_defaults(func=cmd_delete)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
