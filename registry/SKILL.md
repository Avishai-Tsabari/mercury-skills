---
name: registry
description: Create and manage a registry — a flat list of things that exist (systems, clients, domains, keys, repos, inventory, equipment). Use when the user asks to "map", "keep a list of", or "track what we have". If items have a moving status, an owner, or a deadline — use the tracker skill instead.
---

# Registry — flat lists of what exists

## When this is the right skill

**Signature question:** **"What do we have?"**

A registry = one flat entity, no flow. Items get added, updated, and grow stale — but they never **move** between states.

### The routing test (mandatory before creating)

| Question | If yes |
|---|---|
| Is there a status that changes over time? | → `tracker` |
| Does each item have a human owner? | → `tracker` |
| Is there a deadline / target date? | → `tracker` |
| Is hierarchy needed (project → module → item)? | → `tracker` |
| **None of the above** | **`registry`** ✅ |

When it's borderline, ask one question: *"Do you want to know what exists, or to follow what's happening?"*

⚠️ **The default is always `registry`.** Upgrading a registry to a tracker is easy; shrinking a bloated tracker back down is very hard. Don't reach for SQLite because "we might want a status someday".

> A single `status` field with descriptive values (active / planned / retired) is **still a registry** — it's an attribute, not a workflow. The boundary is: does anyone *advance* the item.

## How it's built

One engine, many schemas. No code generation — just a **schema**.

```
<registry-dir>/
  schema.json     the field contract
  data.json       source of truth
  history.jsonl   append-only change log
```

Engine: `engine/registry.py` (next to this SKILL.md) · Python 3.9+ · zero dependencies.

## Working with the user

1. **Run the routing test.** If it's a tracker — switch to the `tracker` skill.
2. **Propose a schema** — fields, types, what's required, how to sort and group. Present it in plain language and ask for approval. Never create without approval.
3. **Check for a matching preset** in `presets/` — prefer starting from one and adapting it.
4. **Create** with `init`, then **register** the new registry in the workspace-level `registries.json` index (create the index if it doesn't exist).
5. **Export** to readable Markdown (`export`) and explain that the file is generated — never edit it by hand.

## Field types

`text` · `number` · `date` (YYYY-MM-DD) · `bool` · `list` (comma-separated) · `url` · `enum`

An `enum` with `"open": true` grows dynamically — a new value is added to the schema automatically. Without `open`, an unknown value is rejected.

## Commands

Paths are relative to the workspace root (the directory the agent works in). Adjust `SKILL_DIR` to wherever this skill is installed (e.g. `.pi/skills/registry`).

```bash
R=<SKILL_DIR>/engine/registry.py
D=registries/clients

python3 $R --dir $D init --preset clients      # create from a preset
python3 $R --dir $D fields                     # show the schema
python3 $R --dir $D add acme --name "Acme Inc" --vertical retail --stage live
python3 $R --dir $D update acme --stage paying --monthly_fee 250
python3 $R --dir $D list --vertical retail     # dynamic filters on any field
python3 $R --dir $D list --search "acme" --json
python3 $R --dir $D show acme
python3 $R --dir $D archive acme --reason "closed the business"   # reversible ✅
python3 $R --dir $D remove acme --yes          # permanent deletion ⚠️
python3 $R --dir $D history acme
python3 $R --dir $D validate
python3 $R --dir $D export --out CLIENTS.md
```

## Available presets

`clients` clients and pilots · `domains` domains and DNS · `adr` architecture decisions ·
`ai-models` AI models and providers · `secrets` key locations (never values) ·
`repos` repositories · `inventory` stock · `systems` systems and services ·
`content` content assets

## Iron rules

- 🔐 **Secrets are never stored in a registry** — only *where* they live and when they were last rotated.
- 🗄️ **Deleting means archiving.** `archive` before `remove`. `remove` requires `--yes` and explicit user confirmation.
- 📄 **The exported Markdown files are generated** — never edit them by hand, only re-run `export`.
- 📌 **Every new registry is registered in the workspace `registries.json` index** — otherwise it will be forgotten within a month.
- 🔁 If asked to add a moving status + owner + deadline — **that's a migration to `tracker`**, not a schema extension.
