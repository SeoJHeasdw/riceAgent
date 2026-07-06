"""Builtin office tools: `create_presentation` (.pptx) + `create_workbook`
(.xlsx) + `create_document` (.docx).

All three are pure in-process generators (stdlib OOXML writers — see pptx_writer /
xlsx_writer / docx_writer) confined to the workspace sandbox, and all carry
`requires_approval=True`: they write files, so they ride the same HITL gate as
`write_file`. The agent can QA its own output afterwards via `read_file`, which
extracts .pptx/.xlsx/.docx back to text (read_extract).

Robustness note (small models): the builders are fault-tolerant — a malformed
slide/block degrades to a salvage rendering and is reported in the result's
`warnings`, so an imperfect spec from a local sLLM still yields a valid file
rather than a hard failure. Only an entirely empty spec is rejected.

Why in-process instead of shelling out to python-pptx/openpyxl/LibreOffice (the
hermes-agent office skill approach): RICE's builtin layer is dependency-free by
design (pyproject: "the agent core runs on the stdlib alone"), results come back
in the same tool call (computed formula values feed the model's next step), and
there is no sandbox/package-install failure mode on end-user machines.
"""

from __future__ import annotations

from ....core.paths import RicePaths
from ....core.types import ToolSpec
from ...tool_dispatch import BuiltinTool
from ..workspace import resolve_workspace_path
from .docx_writer import DocumentSpecError, build_document
from .pptx_writer import DeckSpecError, build_presentation
from .xlsx_writer import WorkbookSpecError, build_workbook

_PRESENTATION_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Workspace-relative output path ending in .pptx (e.g. 'pitch/nutriai.pptx').",
        },
        "title": {"type": "string", "description": "Deck title for document properties."},
        "theme": {
            "description": (
                "Optional palette + fonts. EASIEST: set a preset that fits the topic "
                "— one of 'forest', 'health', 'finance', 'midnight', 'rose', 'mono' "
                "— either as a bare string (theme: 'forest') or as "
                "{\"preset\": \"forest\"}. Presets are pre-tuned so white text stays "
                "readable; prefer them over hand-picking hex. To fine-tune, add any "
                "of primary/secondary/accent (6-digit hex) to override that preset "
                "color, or omit preset and give all three for a fully custom palette "
                "(primary = dominant dark brand color, secondary = light tint, "
                "accent = sharp contrast). Don't default to blue."
            ),
            "type": ["object", "string"],
            "properties": {
                "preset": {
                    "type": "string",
                    "enum": ["forest", "health", "finance", "midnight", "rose", "mono"],
                    "description": "Named palette; override individual colors below if needed.",
                },
                "primary": {"type": "string", "description": "6-digit hex, e.g. '1E2761'."},
                "secondary": {"type": "string", "description": "6-digit hex light tint, e.g. 'CADCFC'."},
                "accent": {"type": "string", "description": "6-digit hex accent, e.g. 'F96167'."},
                "heading_font": {"type": "string", "description": "Heading typeface (default Georgia)."},
                "body_font": {"type": "string", "description": "Body typeface (default Calibri)."},
                "ea_font": {"type": "string", "description": "Optional East-Asian typeface for CJK text."},
            },
        },
        "slides": {
            "type": "array",
            "description": (
                "Slides in order. Each object needs `layout` plus that layout's fields; "
                "any slide may add `notes` (speaker notes). Layouts: "
                "title{title, subtitle?, kicker?, footer?} · "
                "section{title, subtitle?} (divider, auto-numbered) · "
                "bullets{title, kicker?, bullets:[string | {text, sub:[string]}]} · "
                "two_column{title, kicker?, columns:[{heading, bullets:[string]}] (2-3 cards)} · "
                "stats{title, kicker?, stats:[{value, label, note?}] (1-4 big callouts), footnote?} · "
                "table{title, kicker?, headers:[string], rows:[[string]], footnote?} · "
                "chart{title, kicker?, chart:{type:'column'|'line', categories:[string], "
                "series:[{name, values:[number]}] (1-3), or values:[number] for one series}, footnote?} · "
                "timeline{title, kicker?, steps:[{title, desc?}] (2-6)} · "
                "quote{quote, attribution?} · "
                "closing{title, subtitle?, contact?}. "
                "Structure tip: open with title, put one stats/chart/table/timeline slide between "
                "bullet slides (never two identical layouts in a row), close with closing. "
                "Use a chart slide for any projection/trend/comparison with numbers."
            ),
            "items": {"type": "object"},
        },
    },
    "required": ["path", "slides"],
}

_WORKBOOK_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Workspace-relative output path ending in .xlsx (e.g. 'pitch/model.xlsx').",
        },
        "title": {"type": "string", "description": "Workbook title for document properties."},
        "sheets": {
            "type": "array",
            "description": (
                "Sheets in order. Each: {name, rows:[[cell]], widths?:[number] (column "
                "widths in characters), freeze?:'A2' (freeze rows above/cols left of it), "
                "merges?:['A1:C1']}. A cell is a scalar, or an object "
                "{value | formula, style?, format?}. formula is a live Excel formula "
                "('=' optional) using A1/Sheet!A1 refs — supported functions: SUM, AVERAGE, "
                "MIN, MAX, COUNT, COUNTA, PRODUCT, MEDIAN, ROUND(UP/DOWN), ABS, SQRT, POWER, "
                "IF, IFERROR, AND, OR, NOT, CONCAT, NPV, IRR, SUMPRODUCT. "
                "style: header (white on brand fill) | title | bold | input (blue = hand-entered "
                "assumption) | link (green = pulls from another sheet) | note (gray, cite sources) "
                "| highlight | default(black = computed). format: int | decimal | percent | "
                "percent2 | usd | krw | eur | date | general, or a raw Excel format code. "
                "Modeling rule: every derived number must be a formula referencing input cells, "
                "never a precomputed constant — reviewers flex the blue inputs and everything updates."
            ),
            "items": {"type": "object"},
        },
    },
    "required": ["path", "sheets"],
}

_DOCUMENT_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Workspace-relative output path ending in .docx (e.g. 'reports/analysis.docx').",
        },
        "title": {"type": "string", "description": "Document title (rendered large at the top)."},
        "subtitle": {"type": "string", "description": "Optional subtitle under the title."},
        "theme": {
            "description": (
                "Optional palette — a preset name ('forest'/'health'/'finance'/'midnight'/"
                "'rose'/'mono') as a string or {\"preset\": ...}, used for heading/accent "
                "colors. Presets are pre-tuned; prefer them over custom hex."
            ),
            "type": ["object", "string"],
        },
        "blocks": {
            "type": "array",
            "description": (
                "Document body as an ordered list of blocks. Each block is an object with a "
                "`type` (inferred if omitted): "
                "heading{level:1-3, text} · paragraph{text} (blank lines split paragraphs) · "
                "bullets{items:[string | {text, level:0-2}]} · numbered{items:[...]} · "
                "table{headers:[string], rows:[[string]]} · quote{text} · "
                "callout{text, label?} (shaded key-takeaway box) · divider{}. "
                "A bare string is treated as a paragraph. Use headings to structure a report, "
                "bullets for lists, tables for comparisons, callout for the key message."
            ),
            "items": {"type": "object"},
        },
    },
    "required": ["path", "blocks"],
}


def tools(paths: RicePaths) -> list[tuple[ToolSpec, BuiltinTool]]:
    root = paths.workspace
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()

    def _write(rel: str, suffix: str, data: bytes) -> str:
        if not rel.lower().endswith(suffix):
            raise ValueError(f"path must end with {suffix}: {rel!r}")
        target = resolve_workspace_path(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel

    async def create_presentation(args: dict) -> object:
        rel = str(args.get("path") or "")
        try:
            data, summary = build_presentation(args)
        except DeckSpecError as exc:
            raise ValueError(f"invalid deck spec: {exc}") from None
        _write(rel, ".pptx", data)
        return {"path": rel, "bytes_written": len(data), **summary}

    async def create_workbook(args: dict) -> object:
        rel = str(args.get("path") or "")
        try:
            data, summary = build_workbook(args)
        except WorkbookSpecError as exc:
            raise ValueError(f"invalid workbook spec: {exc}") from None
        _write(rel, ".xlsx", data)
        return {"path": rel, "bytes_written": len(data), **summary}

    async def create_document(args: dict) -> object:
        rel = str(args.get("path") or "")
        try:
            data, summary = build_document(args)
        except DocumentSpecError as exc:
            raise ValueError(f"invalid document spec: {exc}") from None
        _write(rel, ".docx", data)
        return {"path": rel, "bytes_written": len(data), **summary}

    return [
        (
            ToolSpec(
                name="create_presentation",
                description=(
                    "Create a designed .pptx slide deck in the workspace from a structured "
                    "slide list (10 layouts: title/section/bullets/two_column/stats/table/"
                    "chart/timeline/quote/closing + speaker notes). Use for pitch decks, "
                    "proposals, reviews, reports; use a `chart` slide (bar/line) for any "
                    "projection or trend. Returns a per-slide summary (+ any `warnings`); "
                    "QA the result with read_file on the .pptx (extracts slide text + notes)."
                ),
                parameters=_PRESENTATION_PARAMETERS,
                source="builtin",
                requires_approval=True,  # writes into the workspace → HITL gate
            ),
            create_presentation,
        ),
        (
            ToolSpec(
                name="create_workbook",
                description=(
                    "Create an .xlsx workbook in the workspace with live Excel formulas, "
                    "number formats and financial-model styling (blue inputs / black formulas "
                    "/ green cross-sheet links). Formulas are also computed locally and the "
                    "results returned, so use it as a calculator for projections (revenue "
                    "models, unit economics, NPV/IRR) and quote the computed numbers."
                ),
                parameters=_WORKBOOK_PARAMETERS,
                source="builtin",
                requires_approval=True,  # writes into the workspace → HITL gate
            ),
            create_workbook,
        ),
        (
            ToolSpec(
                name="create_document",
                description=(
                    "Create a formatted .docx Word document in the workspace from an ordered "
                    "list of blocks (headings, paragraphs, bullet/numbered lists, tables, "
                    "quotes, callouts, dividers). Use for reports, analyses, proposals, memos, "
                    "one-pagers — any long-form prose deliverable (vs slides or spreadsheets). "
                    "Returns a structural outline (+ any `warnings`); QA with read_file on the "
                    ".docx."
                ),
                parameters=_DOCUMENT_PARAMETERS,
                source="builtin",
                requires_approval=True,  # writes into the workspace → HITL gate
            ),
            create_document,
        ),
    ]
