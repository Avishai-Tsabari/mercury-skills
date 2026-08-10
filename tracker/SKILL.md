---
name: tracker
description: Create and manage a tracker — following work that moves (tasks, bugs, invoices, features, client requests). Use when items have a changing status, an assignee, a deadline, or a hierarchy. If it's just a list of what exists — use the registry skill instead.
---

# Tracker — following work that moves

## When this is the right skill

**Signature question:** **"What's the status of…?"** or **"Who owns this?"**

A tracker = items that move between states, with ownership and a timeline, and with history worth preserving.

### The routing test

You need **at least two** of these to justify a tracker:
- A status that changes over time (not an attribute — **movement**)
- A human assignee
- A due date
- A hierarchy: group → subgroup → item

Fewer than two? → **`registry`**. ⚠️ The default is always registry.

## How it's built

One engine, many schemas.

```
<tracker-dir>/
  schema.json    statuses, priorities, hierarchy labels, extra fields
  tracker.db     SQLite: items + groups + item_history
```

Engine: `engine/tracker.py` (next to this SKILL.md) · Python 3.9+ · zero dependencies (built-in sqlite3).

**Hierarchy:** `group` → `subgroup` → `item`. The labels change per domain — for tasks it's project → module → task, for bugs it's product → component → bug, for invoices it's client → subscription → invoice.

## Working with the user

1. **Run the routing test.** A flat list → `registry`.
2. **Propose a schema:** which statuses, what counts as "closed", which priorities, what the hierarchy is, which extra fields. Present it and ask for approval.
3. **Check for a preset** in `presets/`.
4. **Create** with `init`, add groups, then **register** the tracker in the workspace-level `registries.json` index.
5. Explain that updates can be reported in natural language — the agent translates them into commands.

## Commands

Paths are relative to the workspace root. Adjust `SKILL_DIR` to wherever this skill is installed (e.g. `.pi/skills/tracker`).

```bash
T=<SKILL_DIR>/engine/tracker.py
D=trackers/bugs

python3 $T --dir $D init --preset bugs
python3 $T --dir $D group add webapp --name "Web App"
python3 $T --dir $D group add checkout --name "Checkout" --parent webapp
python3 $T --dir $D add "Double booking in calendar" --group webapp --subgroup checkout \
        --assignee dana --priority sev2 --client acme --due 2026-08-05
python3 $T --dir $D update 3 --status in-progress --notes "reproduced"
python3 $T --dir $D list --assignee dana --group-by status
python3 $T --dir $D list --status blocked
python3 $T --dir $D show 3
python3 $T --dir $D history 3
python3 $T --dir $D report          # summary: by status, by assignee, past due, blocked
python3 $T --dir $D delete 3 --yes  # ⚠️ permanent deletion
```

`list` without a filter automatically hides closed items. `--all` shows everything.

## Available presets

`tasks` tasks · `bugs` bugs and client issues · `invoices` invoices and collection ·
`features` feature pipeline

## Iron rules

- 📊 **The tracker is the source of truth** — never keep statuses in the agent's memory. Always query.
- ⛔ **A `blocked` status requires a reason** in the notes — otherwise the block is worthless.
- 🗑️ **Deletion requires `--yes` and explicit user confirmation.** There is no "cancelled" status by default — if one is needed, add it to the schema instead of deleting.
- 📜 **Every change is recorded in `item_history`** — never edit the DB by hand.
- 🔁 Three or fewer statuses with no assignee and no due date = **it should have been a registry**.
