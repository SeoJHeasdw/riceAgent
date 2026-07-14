---
name: business-planning
version: 1
description: Turn a business request into an investor-grade deliverable — financial model (.xlsx) first, then a designed pitch deck (.pptx) — using the create_workbook and create_presentation tools.
state: active
tags: [business, pitch-deck, financial-model, pptx, xlsx]
source: builtin
---

# Business Planning & Documentation

Use this procedure whenever the user asks for a business plan, investor pitch
deck, service proposal, market/revenue analysis, or any "make me a PPT/Excel
for X" request. Deliverables are real files built with `create_workbook` and
`create_presentation` — never just an outline in chat unless asked.

## Step 1 — Frame the ask (one clarifying pass, max)

Extract from the request: **audience** (VC investor / executive / internal),
**goal** (raise money / get approval / inform), **length** (default ~10 slides),
**tone**, and **language**. Write deck text in the conversation's language;
keep kickers/labels short. If something essential is missing, ask once —
otherwise state your assumptions on the final slide's speaker notes and proceed.

## Step 2 — Gather the numbers before writing anything

Collect market size, growth rate, pricing comparables, and cost drivers
(use `fetch_url` / connected search tools when available; otherwise estimate
conservatively and label estimates as such). Every figure needs a source or an
explicit assumption — untraceable numbers are what kill investor decks.

## Step 3 — Build the financial model FIRST (`create_workbook`)

The deck quotes the model, so the model comes first. Standard 3-sheet shape:

1. **Inputs** — every assumption as a `style: "input"` (blue) cell with a
   `style: "note"` cell citing its source/date next to it.
2. **Model** — projections where every derived cell is a `formula`
   (`=B2*(1+Inputs!B5)`), never a precomputed constant. Pull inputs with
   cross-sheet refs (`style: "link"` for pulled anchors). Build revenue as
   users × conversion × price; costs bottom-up; add NPV/IRR when the audience
   is investors.
3. **Checks** — boolean sanity formulas (`=AND(Model!B4>0, ...)`) that must all
   be TRUE.

The tool computes formulas locally and returns the grid — **read the computed
numbers from the tool result and quote exactly those in the deck**. If
`formula_errors` is non-empty, fix and regenerate before moving on. Ignore
`formula_warnings` (functions like VLOOKUP/SUMIF the local engine doesn't
evaluate) — those formulas are correct and Excel computes them on open; don't
rewrite them.

## Step 4 — Write the deck (`create_presentation`)

Investor arc for ~10 slides (adapt, don't pad):

| # | layout | content |
|---|--------|---------|
| 1 | title | product one-liner, round/date kicker |
| 2 | section or bullets | the problem — pain points, who hurts, cost of the pain |
| 3 | stats | market size: TAM / growth % / one proof-of-pain number |
| 4 | bullets | solution — what it does, why now |
| 5 | two_column | differentiation: incumbents vs us |
| 6 | table | business/revenue model: streams, pricing, target, mix |
| 7 | stats or table | projections — quote the model's computed Y1/Y2/Y3 numbers |
| 8 | timeline | go-to-market / roadmap milestones |
| 9 | bullets or stats | team, traction, or unit economics (pick the strongest) |
| 10 | closing | the ask (amount + use of funds) and contact |

Design rules the tool doesn't enforce for you:
- Pick a `theme` whose colors fit the topic (health → greens, fintech → deep
  navy…). Don't ship the default palette unchanged.
- Never two identical layouts back-to-back; every slide carries one number,
  comparison, or visual structure — no wall-of-bullets slides.
- Stat values stay short ("$4.4B", "17%"); explanation goes in `label`/`note`.
- Put the delivery script and assumptions in `notes` (speaker notes) per slide.
- Every number on a slide comes from the workbook you built or from the user.
  NEVER cite invented third-party statistics ("73% of enterprises — Gartner");
  a made-up citation in an investor deck is worse than no number at all. If a
  figure is an assumption, say so on the slide or in its notes.

## Step 5 — QA loop (mandatory)

1. `read_file` the .pptx — check slide order, missing content, leftover
   placeholder text, numbers matching the model exactly.
2. `read_file` the .xlsx — check computed values and that Checks are all TRUE.
3. Fix and regenerate (tools overwrite in place), then re-read. One full
   fix-and-verify cycle minimum before presenting the result.
4. In your final answer: link both files, summarize the storyline in 3–4
   bullets, and list the key assumptions the user should validate.

## Pitfalls

- Hardcoding a computed number into the model (breaks when inputs change).
- Deck numbers that drift from model numbers — always re-quote after edits.
- 15 slides of filler: if a slide doesn't advance the investment case, cut it.
- Writing English labels on a Korean deck (or vice versa) — match the
  conversation language throughout, including table headers.
