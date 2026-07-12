---
name: skill-authoring
version: 1
description: Write, improve, or save a RICE skill (SKILL.md) — when the user asks to create/save a skill (스킬 만들기/저장/개선), when capturing a reusable procedure with propose_skill, or when a skill isn't being found or injected (discovery/routing).
state: active
tags: [skill, 스킬, skill-md, propose-skill, authoring]
source: builtin
---

# Authoring RICE Skills

Follow this procedure when creating a new skill, improving an existing one, or
diagnosing why a skill isn't being picked up. RICE skills are NOT Claude Code
plugin skills — they are single SKILL.md files managed by the learning loop,
proposed via the `propose_skill` tool and approved by the user (HITL card).

## 1. Decide whether it should be a skill at all

- Capture **methods**, not facts: a skill is a repeatable procedure (numbered
  steps, exact commands/tools, pitfalls). A declarative fact ("the user's
  deadline is Friday") belongs in memory, not a skill.
- Worth saving: a multi-step procedure (roughly 3+ tool calls) you would run
  the same way again, or a correction to an injected skill you noticed while
  working.
- Not worth saving: one-off answers, anything fully covered by an existing
  skill (improve that skill instead — propose with its exact name).

## 2. Name and metadata (these decide discovery)

Skill routing is lexical (name/tags strongest, then description/body) — no
embeddings. Metadata IS the retrieval index:

1. `name`: kebab-case, class-level, English (e.g. `weekly-report-build`,
   not `report` or `build-the-report-for-kim`).
2. `description`: one line stating WHEN to use it, written with the words a
   user would actually say — include both English and Korean task words if the
   user works in Korean (e.g. "weekly report / 주간 보고서").
3. `tags`: 3–5 short tokens covering the task's vocabulary. Add a negative
   tag `not:<word>` to carve off a known false-positive neighborhood.

## 3. Write the body

1. Start with one sentence: when this skill applies.
2. Numbered steps, each one verifiable — name the exact tools to call
   (e.g. `create_workbook`, `fetch_url`) and the exact commands/arguments.
3. End with verification: how to check the result (read back the file, etc.).
4. Include pitfalls you actually hit this session — they are the highest-value
   lines in the skill.
5. Body is markdown only — do NOT include `---` frontmatter in the body, do
   not leave TODO/TBD placeholders, and close every ``` code fence.

## 4. Propose it

Call `propose_skill` with name + body (+ description/tags for new skills, or a
one-line `changelog` for improvements). Remember:

- For an existing skill the body REPLACES the whole body — carry over the
  still-valid parts, don't submit a fragment.
- Proposing stages an approval card; nothing is written until the user
  approves. Mention in your answer that you proposed it.
- The runtime verifier rejects: near-empty bodies, unclosed code fences,
  frontmatter inside the body. It warns on: no numbered steps, dangling
  endings, placeholders, tools that don't exist in the current catalog, and
  near-duplicate names. If rejected, fix the body and propose again in the
  same turn.

## 5. Rules that always apply

- A skill is a saved default, not an order — the user's current request
  outranks it. Never write a skill that tells the agent to bypass approval or
  run destructive commands; approvals still gate those regardless.
- Plugin-provided skills are read-only (vendor-owned). To improve one, propose
  a NEW skill under your own name that adapts its procedure.
- A polished skill can be shared: the user can export it from the My Skills
  screen as a market artifact (workspace `skill-exports/`). Mention this when
  a skill has matured through several approved improvements.
