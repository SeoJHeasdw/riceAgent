"""Builtin office tools: `create_presentation` + `edit_presentation` (.pptx) +
`create_workbook` (.xlsx) + `create_document` (.docx).

All are in-process generators (stdlib OOXML writers — see pptx_writer /
xlsx_writer / docx_writer) confined to the workspace sandbox, and all carry
`requires_approval=True`: they write files, so they ride the same HITL gate as
`write_file`. The agent can QA its own output afterwards via `read_file`, which
extracts .pptx/.xlsx/.docx back to text (read_extract).

`edit_presentation` (2026-07-10): create_presentation이 원본 스펙을
docProps/riceSpec.json으로 패키지에 임베드하므로, 슬라이드 단위 편집
(replace/insert/remove/move + theme/title)은 전체 스펙 재전송 없이 그 파트를
읽어 적용 후 재빌드한다 — "슬라이드 3장만 고쳐줘"가 30장 스펙 재생성이 아니라
연산 3개가 된다(소형 모델의 재생성 드리프트 방지). 외부에서 만들었거나
PowerPoint가 재저장해 임베드가 사라진 파일은 명확한 에러로 거절한다.

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
from .pptx_writer import DeckSpecError, build_presentation, read_embedded_spec
from .xlsx_writer import WorkbookSpecError, build_workbook

# image 레이아웃이 임베드할 수 있는 워크스페이스 이미지 상한/형식(형식 검증 자체는
# pptx_writer._image_meta가 매직바이트로 한다 — 확장자는 신뢰하지 않는다).
_MAX_ASSET_BYTES = 8 * 1024 * 1024

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
                "image{title, kicker?, image:'workspace-relative .png/.jpg', caption?, "
                "bullets?:[string] (adds text left / image right)} · "
                "closing{title, subtitle?, contact?}. "
                "Structure tip: open with title, put one stats/chart/table/timeline/image slide "
                "between bullet slides (never two identical layouts in a row), close with closing. "
                "Use a chart slide for any projection/trend/comparison with numbers; use an image "
                "slide for screenshots, diagrams, or photos already in the workspace."
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


_EDIT_PARAMETERS = {
    "type": "object",
    "properties": {
        "path": {
            "type": "string",
            "description": "Workspace-relative path of an existing .pptx created by create_presentation.",
        },
        "operations": {
            "type": "array",
            "description": (
                "Slide operations applied in order (slide numbers are 1-based and refer to the "
                "deck state at that point). Each: "
                "{action:'replace', index:N, slide:{...full slide object...}} · "
                "{action:'insert', index:N (insert before N; omit to append), slide:{...}} · "
                "{action:'remove', index:N} · "
                "{action:'move', index:N, to:M}. "
                "Slide objects use the same layouts/fields as create_presentation."
            ),
            "items": {"type": "object"},
        },
        "title": {"type": "string", "description": "Optionally replace the deck title."},
        "theme": {
            "type": ["object", "string"],
            "description": "Optionally replace the theme (same shape as create_presentation)."
        },
    },
    "required": ["path", "operations"],
}


def _apply_deck_operations(slides: list, operations: list) -> list[str]:
    """임베디드 스펙의 slides 배열에 연산을 순서대로 적용한다. 인덱스는 1-based,
    각 연산 시점의 덱 상태 기준. 잘못된 연산은 어느 것도 적용하지 않은 상태로
    ValueError를 던진다(호출부가 원본을 건드리기 전에 복사본에 적용)."""
    applied: list[str] = []
    for i, op in enumerate(operations):
        if not isinstance(op, dict):
            raise ValueError(f"operations[{i}] must be an object")
        action = str(op.get("action") or "").strip().lower()

        def index_of(key: str = "index", *, upper: int | None = None) -> int:
            raw = op.get(key)
            if not isinstance(raw, (int, float)):
                raise ValueError(f"operations[{i}].{key} (1-based slide number) is required for {action!r}")
            idx = int(raw)
            limit = upper if upper is not None else len(slides)
            if not 1 <= idx <= limit:
                raise ValueError(f"operations[{i}].{key}={idx} is out of range (deck has {len(slides)} slides)")
            return idx

        if action == "replace":
            slide = op.get("slide")
            if not isinstance(slide, dict):
                raise ValueError(f"operations[{i}].slide object is required for replace")
            idx = index_of()
            slides[idx - 1] = slide
            applied.append(f"replace slide {idx}")
        elif action == "insert":
            slide = op.get("slide")
            if not isinstance(slide, dict):
                raise ValueError(f"operations[{i}].slide object is required for insert")
            raw_at = op.get("index")
            at = int(raw_at) if isinstance(raw_at, (int, float)) else len(slides) + 1
            if not 1 <= at <= len(slides) + 1:
                raise ValueError(f"operations[{i}].index={at} is out of range for insert")
            slides.insert(at - 1, slide)
            applied.append(f"insert at {at}")
        elif action == "remove":
            idx = index_of()
            del slides[idx - 1]
            applied.append(f"remove slide {idx}")
        elif action == "move":
            idx = index_of()
            to = index_of("to")
            slide = slides.pop(idx - 1)
            slides.insert(to - 1, slide)
            applied.append(f"move slide {idx} → {to}")
        else:
            raise ValueError(f"operations[{i}].action must be replace/insert/remove/move, got {action!r}")
    if not applied:
        raise ValueError("operations must be a non-empty array")
    return applied


def tools(paths: RicePaths) -> list[tuple[ToolSpec, BuiltinTool]]:
    # Ensure the initial root; inner functions read `paths.workspace` live so a
    # relocation applied at a turn boundary is honored without rebuilding tools.
    paths.workspace.mkdir(parents=True, exist_ok=True)

    def _write(rel: str, suffix: str, data: bytes) -> str:
        root = paths.workspace
        if not rel.lower().endswith(suffix):
            raise ValueError(f"path must end with {suffix}: {rel!r}")
        target = resolve_workspace_path(root, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return rel

    def _load_asset(rel: str) -> bytes:
        """image 레이아웃의 워크스페이스 이미지 로더 — 샌드박스 resolve + 크기 상한.
        형식 검증(매직바이트)은 pptx_writer가 한다."""
        root = paths.workspace
        target = resolve_workspace_path(root, rel)
        if not target.is_file():
            raise ValueError(f"image not found in workspace: {rel}")
        if target.stat().st_size > _MAX_ASSET_BYTES:
            raise ValueError(f"image too large (> {_MAX_ASSET_BYTES // (1024 * 1024)} MB): {rel}")
        return target.read_bytes()

    async def create_presentation(args: dict) -> object:
        rel = str(args.get("path") or "")
        try:
            data, summary = build_presentation(args, load_asset=_load_asset)
        except DeckSpecError as exc:
            raise ValueError(f"invalid deck spec: {exc}") from None
        _write(rel, ".pptx", data)
        return {"path": rel, "bytes_written": len(data), **summary}

    async def edit_presentation(args: dict) -> object:
        root = paths.workspace
        rel = str(args.get("path") or "")
        if not rel.lower().endswith(".pptx"):
            raise ValueError(f"path must end with .pptx: {rel!r}")
        target = resolve_workspace_path(root, rel)
        if not target.is_file():
            raise ValueError(f"presentation not found: {rel}")
        spec = read_embedded_spec(target.read_bytes())
        if spec is None:
            raise ValueError(
                "this .pptx has no embedded RICE spec (created elsewhere, or re-saved by "
                "another app) — rebuild it with create_presentation instead"
            )
        slides = list(spec.get("slides") or [])
        operations = args.get("operations")
        if not isinstance(operations, list):
            raise ValueError("operations must be an array")
        applied = _apply_deck_operations(slides, operations)
        spec["slides"] = slides
        if isinstance(args.get("title"), str) and args["title"].strip():
            spec["title"] = args["title"]
        if args.get("theme") is not None:
            spec["theme"] = args["theme"]
        try:
            data, summary = build_presentation(spec, load_asset=_load_asset)
        except DeckSpecError as exc:
            raise ValueError(f"edited deck spec is invalid: {exc}") from None
        _write(rel, ".pptx", data)
        return {"path": rel, "bytes_written": len(data), "operations_applied": applied, **summary}

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
                    "slide list (11 layouts: title/section/bullets/two_column/stats/table/"
                    "chart/timeline/quote/image/closing + speaker notes). Use for pitch decks, "
                    "proposals, reviews, reports; use a `chart` slide (bar/line) for any "
                    "projection or trend, and an `image` slide for workspace screenshots/"
                    "diagrams. Long text auto-shrinks to fit its box. Returns a per-slide "
                    "summary (+ any `warnings`); QA the result with read_file on the .pptx "
                    "(extracts slide text + notes). To change slides later, use "
                    "edit_presentation instead of regenerating the whole deck."
                ),
                parameters=_PRESENTATION_PARAMETERS,
                source="builtin",
                requires_approval=True,  # writes into the workspace → HITL gate
            ),
            create_presentation,
        ),
        (
            ToolSpec(
                name="edit_presentation",
                description=(
                    "Edit an existing .pptx that was created by create_presentation: replace/"
                    "insert/remove/move whole slides (and optionally the deck title/theme) "
                    "without resending the full deck spec. Slide numbers are 1-based. The deck "
                    "is rebuilt from its embedded spec, so all layouts and design rules apply. "
                    "Fails clearly if the file was created elsewhere or re-saved by PowerPoint "
                    "(embedded spec missing) — rebuild with create_presentation in that case."
                ),
                parameters=_EDIT_PARAMETERS,
                source="builtin",
                requires_approval=True,  # writes into the workspace → HITL gate
            ),
            edit_presentation,
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
