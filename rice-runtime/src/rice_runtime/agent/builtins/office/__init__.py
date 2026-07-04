"""Builtin office tools: `create_presentation` (.pptx) + `create_workbook` (.xlsx).

Both are pure in-process generators (stdlib OOXML writers — see pptx_writer /
xlsx_writer) confined to the workspace sandbox, and both carry
`requires_approval=True`: they write files, so they ride the same HITL gate as
`write_file`. The agent can QA its own output afterwards via `read_file`, which
extracts .pptx/.xlsx back to text (read_extract).

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
            "type": "object",
            "description": (
                "Optional brand palette + fonts. Pick colors that fit THIS topic "
                "(don't default to blue): primary = dominant dark brand color "
                "(white text must be readable on it), secondary = light tint, "
                "accent = sharp contrast color."
            ),
            "properties": {
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
                "timeline{title, kicker?, steps:[{title, desc?}] (2-6)} · "
                "quote{quote, attribution?} · "
                "closing{title, subtitle?, contact?}. "
                "Structure tip: open with title, put one stats/table/timeline slide between "
                "bullet slides (never two identical layouts in a row), close with closing."
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

    return [
        (
            ToolSpec(
                name="create_presentation",
                description=(
                    "Create a designed .pptx slide deck in the workspace from a structured "
                    "slide list (9 layouts: title/section/bullets/two_column/stats/table/"
                    "timeline/quote/closing + speaker notes). Use for pitch decks, proposals, "
                    "reports. Returns a per-slide summary; QA the result with read_file on "
                    "the .pptx (extracts slide text + notes)."
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
    ]
