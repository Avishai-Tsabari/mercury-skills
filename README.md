# mercury-skills

Reusable agent skills for [Mercury](https://github.com/Avishai-Tsabari/mercury) assistants (and any [pi](https://github.com/badlogic/pi)-based agent).

Each top-level directory is one self-contained skill: a `SKILL.md` the agent reads, plus a zero-dependency Python engine and schema presets. No npm install, no build step — Python 3.9+ is the only requirement.

## Skills

| Skill | What it manages | Storage |
|-------|-----------------|---------|
| [`registry`](registry/) | Flat lists of things that **exist** — clients, domains, repos, systems, secrets locations, inventory | JSON + append-only history log |
| [`tracker`](tracker/) | Work that **moves** — tasks, bugs, invoices, features, with status flow, assignees, due dates, hierarchy | SQLite with full item history |
| [`clean-delivery`](clean-delivery/) | Pre-delivery hygiene — invisible Unicode in generated text, authoring metadata in DOCX/PDF/JPEG/PNG | stateless, operates on files |

`registry` and `tracker` are complementary and both SKILL.md files include a mandatory routing test: an item with a changing status, an owner, a deadline, or a hierarchy belongs in a tracker; everything else is a registry. **The default is always registry** — upgrading later is easy, shrinking a bloated tracker is not.

## Install

### Into a Mercury space (per-space skills)

Copy the skill directories into the space's `.pi/skills/`:

```bash
git clone https://github.com/Avishai-Tsabari/mercury-skills.git
cp -r mercury-skills/registry mercury-skills/tracker mercury-skills/clean-delivery \
  <project>/.mercury/spaces/<space>/.pi/skills/
```

Pi discovers them automatically on the next message — name and description are loaded into the system prompt; the full SKILL.md body is read on demand.

### Globally (all spaces)

Copy into the Mercury project's global skills directory instead:

```bash
cp -r mercury-skills/registry mercury-skills/tracker mercury-skills/clean-delivery \
  <project>/.mercury/global/skills/
```

## Data layout

Skill code and data are deliberately separate. Engines and presets live wherever the skill is installed; the data each registry/tracker holds lives in the agent's workspace:

```
<workspace>/
  registries.json          # index of every registry & tracker created (source of truth)
  registries/<name>/       # one directory per registry: schema.json, data.json, history.jsonl
  trackers/<name>/         # one directory per tracker: schema.json, tracker.db
```

Updating the skills never touches data; deleting a registry's data never breaks the skill.

## CLI quick taste

```bash
# registry: what do we have?
python3 registry/engine/registry.py --dir registries/domains init --preset domains
python3 registry/engine/registry.py --dir registries/domains add example.com --registrar cloudflare
python3 registry/engine/registry.py --dir registries/domains list

# tracker: what's moving?
python3 tracker/engine/tracker.py --dir trackers/tasks init --preset tasks
python3 tracker/engine/tracker.py --dir trackers/tasks add "Ship the landing page" --assignee dana --due 2026-09-01
python3 tracker/engine/tracker.py --dir trackers/tasks report

# clean-delivery: is this safe to send out?
python3 clean-delivery/engine/clean.py text article.md --check
python3 clean-delivery/engine/clean.py file proposal.pdf
```

Both engines are schema-driven: `init --preset <name>` starts from a preset (see each skill's `presets/`), or `init` alone creates a minimal schema you extend. The CLI grows flags dynamically from the schema — a field added to `schema.json` is immediately filterable and settable.

## License

MIT
