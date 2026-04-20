---
title: Vault Guide
description: How this vault is organised, the frontmatter schema every note follows, the tag taxonomy, and how to add a new note so it shows up correctly in Graph view.
tags: [meta, guide]
type: meta
updated: 2026-04-19
---

# Vault Guide

This vault is designed so that **Obsidian's Graph view tells the truth about the codebase**. If you follow the conventions below, new notes slot in automatically.

## Frontmatter schema

Every note starts with YAML frontmatter:

```yaml
---
title: "Human-readable title"
description: "One-sentence summary, used by Obsidian quick-switcher and by AI agents as context"
tags: [layer/coaching, module, python]
type: module | concept | guide | moc | home | meta | decision | runbook
module_path: backend/coaching_engine.py   # optional: source of truth
related:
  - "[[Link 1]]"
  - "[[Link 2]]"
updated: 2026-04-19
---
```

**Required keys:** `title`, `description`, `tags`, `type`, `updated`.
**Optional keys:** `module_path`, `related`, `status`.

## Tag taxonomy

Tags are hierarchical (use `/` as the separator):

| Tag prefix | Meaning |
|------------|---------|
| `layer/*` | Which system layer (e.g. `layer/audio`, `layer/coaching`, `layer/scoring`) |
| `lang/*` | Implementation language (`lang/python`, `lang/swift`, `lang/typescript`) |
| `stack/*` | Framework (`stack/fastapi`, `stack/react`, `stack/electron`) |
| `topic/*` | Domain concept (`topic/elm`, `topic/superpowers`, `topic/ace`) |
| Free tags | `module`, `concept`, `guide`, `runbook`, `decision`, `home`, `meta`, `moc` |

## Linking conventions

- Use `[[Note Title]]` — match the **title** in the target note's frontmatter (Obsidian resolves case-insensitively but matching exactly helps).
- Prefer **noun-phrase titles** (e.g. `[[Coaching Engine Architecture]]`), not imperative (`How to run coach`).
- Cross-link **both directions** where useful. Obsidian auto-populates the *Linked mentions* panel, but explicit links render on the graph edges.
- Embed a note with `![[Note Title]]` only when literally inlining its content.

## Graph-friendly notes

To get the most out of **Graph view**:

1. Every module note links to the modules that import it and the modules it imports.
2. Every concept note links to the modules that implement it.
3. Every guide links to the concepts it mentions.

This yields a triangle: **concept → module → concept**, so the graph clusters by topic, not by directory.

## Mermaid diagrams

All diagrams in this vault are authored in [mermaid](https://mermaid.js.org). Obsidian renders them natively. **Do not** use ASCII art, PlantUML, or Graphviz — they render inconsistently and lose info.

Common kinds used here:
- `graph LR` / `flowchart TD` — module dependencies, request flow
- `sequenceDiagram` — request/response timelines
- `quadrantChart` — the [[Communicator Superpowers]] 2×2
- `mindmap` — the vault map on [[Home]]

## Adding a new note

1. Copy the frontmatter template above.
2. Place it in the most appropriate directory (they're prefixed `00`–`99` to sort deliberately).
3. Link to it from at least one existing note (usually a MOC or [[Home]]).
4. Add tags that make it discoverable.
5. Preview the graph — your note should join an existing cluster, not float alone.

## Why this matters for AI agents

See the README section on "Why Obsidian Vaults for humans and AI agents." The TL;DR: frontmatter + wikilinks produce a **typed, semi-structured knowledge graph** that an LLM can navigate by following links, scoping by tag, or embedding individual notes — rather than having to re-read the whole codebase every time.
