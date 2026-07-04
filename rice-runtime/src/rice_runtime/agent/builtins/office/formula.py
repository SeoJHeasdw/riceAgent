"""office/formula.py — small Excel-formula engine (stdlib only).

`create_workbook` writes formula *strings* into the .xlsx, but nothing computes
them until the file is opened in Excel/Numbers. That leaves two gaps this module
closes (mirrors the `recalc` step of the excel-author reference skill, without
LibreOffice):

  1. **Cached values** — each formula cell gets a precomputed `<v>` so viewers
     (and our own `read_file` extraction) show numbers immediately.
  2. **Tool feedback** — the computed grid goes back to the model in the tool
     result, so it can quote real figures ("Year-3 revenue ₩2.4B") in the deck
     or catch its own modeling mistakes without reopening the file.

Scope: the arithmetic/reference/function subset that business models actually
use. Anything else evaluates to an Excel-style error literal (#NAME? etc.) —
the file still opens fine; Excel recomputes real values for exotic formulas.

Grammar (precedence low→high, standard Excel):
  compare(= <> < <= > >=) → concat(&) → add(+ -) → mul(* /) → pow(^, right)
  → unary(+ -) → postfix(%) → primary(number | string | bool | ref | range |
  function(...) | (expr))
References: A1, $B$2, Sheet2!C3, 'My Sheet'!A1:D9 (ranges only as function args).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

__all__ = [
    "CellError",
    "EvaluatedCell",
    "Grid",
    "col_to_index",
    "evaluate_grid",
    "index_to_col",
    "parse_cell_ref",
]


# ── A1 notation helpers ───────────────────────────────────────────────────────

def col_to_index(letters: str) -> int:
    """'A' → 1, 'Z' → 26, 'AA' → 27. Raises ValueError on empty/non-alpha."""
    if not letters or not letters.isalpha():
        raise ValueError(f"invalid column letters: {letters!r}")
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx


def index_to_col(idx: int) -> str:
    """1 → 'A', 27 → 'AA'."""
    if idx < 1:
        raise ValueError(f"column index must be >= 1: {idx}")
    out = ""
    while idx:
        idx, rem = divmod(idx - 1, 26)
        out = chr(ord("A") + rem) + out
    return out


_CELL_REF = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]{1,7})$")


def parse_cell_ref(ref: str) -> tuple[int, int]:
    """'$B$3' → (col=2, row=3). Raises ValueError if not a single-cell ref."""
    m = _CELL_REF.match(ref)
    if not m:
        raise ValueError(f"not a cell reference: {ref!r}")
    return col_to_index(m.group(1)), int(m.group(2))


# ── values ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CellError:
    """Excel-style error value (#DIV/0!, #VALUE!, …). Propagates through ops."""

    code: str

    def __str__(self) -> str:  # written into <v> for t="e" cells
        return self.code


ERR_DIV0 = CellError("#DIV/0!")
ERR_VALUE = CellError("#VALUE!")
ERR_NAME = CellError("#NAME?")
ERR_REF = CellError("#REF!")
ERR_NUM = CellError("#NUM!")
ERR_CYCLE = CellError("#CYCLE!")  # not a real Excel code; surfaced for circular refs

Scalar = float | str | bool | None | CellError


@dataclass
class EvaluatedCell:
    """One grid cell after evaluation: raw input + computed scalar."""

    value: Scalar = None  # literal cells: the literal; formula cells: computed
    formula: str | None = None  # formula text WITHOUT the leading '='


# (sheet_key, col, row) → EvaluatedCell. sheet_key = sheet name casefolded.
Grid = dict[tuple[str, int, int], EvaluatedCell]


def _sheet_key(name: str) -> str:
    return name.strip().casefold()


# ── tokenizer ────────────────────────────────────────────────────────────────

_TOKEN = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<number>(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<sheetref>(?:'[^']+'|[A-Za-z_][A-Za-z0-9_.]*)!\$?[A-Za-z]{1,3}\$?[0-9]{1,7})
  | (?P<ident>[A-Za-z_][A-Za-z0-9_.]*)
  | (?P<op><>|<=|>=|[=<>+\-*/^&%(),:])
    """,
    re.VERBOSE,
)

_REF_IDENT = re.compile(r"^\$?[A-Za-z]{1,3}\$?[0-9]{1,7}$")


@dataclass(frozen=True)
class _Tok:
    kind: str  # number | string | ref | ident | op
    text: str
    sheet: str | None = None  # for sheet-qualified refs


class _FormulaError(Exception):
    def __init__(self, err: CellError) -> None:
        super().__init__(err.code)
        self.err = err


def _tokenize(src: str) -> list[_Tok]:
    out: list[_Tok] = []
    pos = 0
    while pos < len(src):
        m = _TOKEN.match(src, pos)
        if m is None:
            raise _FormulaError(ERR_VALUE)
        pos = m.end()
        if m.lastgroup == "ws":
            continue
        text = m.group()
        if m.lastgroup == "number":
            out.append(_Tok("number", text))
        elif m.lastgroup == "string":
            out.append(_Tok("string", text[1:-1].replace('""', '"')))
        elif m.lastgroup == "sheetref":
            sheet_part, ref_part = text.split("!", 1)
            sheet = sheet_part[1:-1] if sheet_part.startswith("'") else sheet_part
            out.append(_Tok("ref", ref_part.replace("$", ""), sheet=sheet))
        elif m.lastgroup == "ident":
            # A bare ident that *parses* as a cell ref (B12) is a ref; otherwise a
            # function name / TRUE / FALSE.
            if _REF_IDENT.match(text):
                out.append(_Tok("ref", text.replace("$", "")))
            else:
                out.append(_Tok("ident", text))
        else:
            out.append(_Tok("op", text))
    return out


# ── AST ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Num:
    value: float


@dataclass(frozen=True)
class _Str:
    value: str


@dataclass(frozen=True)
class _Bool:
    value: bool


@dataclass(frozen=True)
class _Ref:
    sheet: str | None
    col: int
    row: int


@dataclass(frozen=True)
class _Range:
    sheet: str | None
    c1: int
    r1: int
    c2: int
    r2: int


@dataclass(frozen=True)
class _Unary:
    op: str
    operand: object


@dataclass(frozen=True)
class _Binary:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class _Call:
    name: str
    args: tuple[object, ...]


class _Parser:
    """Recursive-descent over the token list (precedence in module docstring)."""

    def __init__(self, tokens: list[_Tok]) -> None:
        self._toks = tokens
        self._pos = 0

    def parse(self) -> object:
        node = self._compare()
        if self._pos != len(self._toks):
            raise _FormulaError(ERR_VALUE)
        return node

    # ── helpers ──
    def _peek(self) -> _Tok | None:
        return self._toks[self._pos] if self._pos < len(self._toks) else None

    def _take_op(self, *ops: str) -> str | None:
        tok = self._peek()
        if tok and tok.kind == "op" and tok.text in ops:
            self._pos += 1
            return tok.text
        return None

    # ── precedence levels ──
    def _compare(self) -> object:
        node = self._concat()
        while (op := self._take_op("=", "<>", "<", "<=", ">", ">=")) is not None:
            node = _Binary(op, node, self._concat())
        return node

    def _concat(self) -> object:
        node = self._additive()
        while self._take_op("&") is not None:
            node = _Binary("&", node, self._additive())
        return node

    def _additive(self) -> object:
        node = self._multiplicative()
        while (op := self._take_op("+", "-")) is not None:
            node = _Binary(op, node, self._multiplicative())
        return node

    def _multiplicative(self) -> object:
        node = self._power()
        while (op := self._take_op("*", "/")) is not None:
            node = _Binary(op, node, self._power())
        return node

    def _power(self) -> object:
        node = self._unary()
        if self._take_op("^") is not None:
            return _Binary("^", node, self._power())  # right-assoc
        return node

    def _unary(self) -> object:
        if (op := self._take_op("+", "-")) is not None:
            return _Unary(op, self._unary())
        return self._postfix()

    def _postfix(self) -> object:
        node = self._primary()
        while self._take_op("%") is not None:
            node = _Unary("%", node)
        return node

    def _primary(self) -> object:
        tok = self._peek()
        if tok is None:
            raise _FormulaError(ERR_VALUE)
        if tok.kind == "number":
            self._pos += 1
            return _Num(float(tok.text))
        if tok.kind == "string":
            self._pos += 1
            return _Str(tok.text)
        if tok.kind == "ref":
            self._pos += 1
            col, row = parse_cell_ref(tok.text)
            if self._take_op(":") is not None:
                end = self._peek()
                if end is None or end.kind != "ref":
                    raise _FormulaError(ERR_REF)
                self._pos += 1
                c2, r2 = parse_cell_ref(end.text)
                # normalize corners; the end ref's sheet (if any) is ignored —
                # Excel requires both sides on the same sheet anyway.
                return _Range(
                    tok.sheet,
                    min(col, c2), min(row, r2),
                    max(col, c2), max(row, r2),
                )
            return _Ref(tok.sheet, col, row)
        if tok.kind == "ident":
            self._pos += 1
            upper = tok.text.upper()
            if upper == "TRUE":
                return _Bool(True)
            if upper == "FALSE":
                return _Bool(False)
            if self._take_op("(") is None:
                raise _FormulaError(ERR_NAME)
            args: list[object] = []
            if self._take_op(")") is None:
                while True:
                    args.append(self._compare())
                    if self._take_op(",") is not None:
                        continue
                    if self._take_op(")") is not None:
                        break
                    raise _FormulaError(ERR_VALUE)
            return _Call(upper, tuple(args))
        if tok.kind == "op" and tok.text == "(":
            self._pos += 1
            node = self._compare()
            if self._take_op(")") is None:
                raise _FormulaError(ERR_VALUE)
            return node
        raise _FormulaError(ERR_VALUE)


# ── evaluator ────────────────────────────────────────────────────────────────

def _num(v: Scalar) -> float:
    """Coerce a scalar to a number the way Excel arithmetic does."""
    if isinstance(v, CellError):
        raise _FormulaError(v)
    if v is None:
        return 0.0
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, float):
        return v
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else 0.0
        except ValueError:
            raise _FormulaError(ERR_VALUE) from None
    raise _FormulaError(ERR_VALUE)


def _text(v: Scalar) -> str:
    if isinstance(v, CellError):
        raise _FormulaError(v)
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        return format(v, ".15g")
    return str(v)


def _is_number(v: Scalar) -> bool:
    return isinstance(v, float) and not isinstance(v, bool)


class _Evaluator:
    """Resolves refs against a Grid, memoizing formula results + cycle guard."""

    def __init__(self, grid: Grid, default_sheet: str, sheet_keys: set[str]) -> None:
        self._grid = grid
        self._default = default_sheet
        self._sheets = sheet_keys
        self._memo: dict[tuple[str, int, int], Scalar] = {}
        self._in_progress: set[tuple[str, int, int]] = set()

    # ── cell resolution ──
    def cell_value(self, sheet: str | None, col: int, row: int, *, current: str | None = None) -> Scalar:
        """Value of one cell. `sheet=None` binds to `current` (the sheet whose
        formula is being evaluated) so unqualified refs stay sheet-local."""
        key = (_sheet_key(sheet) if sheet else (current or self._default), col, row)
        if sheet and key[0] not in self._sheets:
            return ERR_REF
        if key in self._memo:
            return self._memo[key]
        cell = self._grid.get(key)
        if cell is None:
            return None
        if cell.formula is None:
            return cell.value
        if key in self._in_progress:
            return ERR_CYCLE
        self._in_progress.add(key)
        try:
            result = self.evaluate(cell.formula, current_sheet=key[0])
        finally:
            self._in_progress.discard(key)
        self._memo[key] = result
        return result

    def evaluate(self, formula: str, *, current_sheet: str) -> Scalar:
        try:
            ast = _Parser(_tokenize(formula.lstrip("="))).parse()
            return self._eval(ast, current_sheet)
        except _FormulaError as exc:
            return exc.err
        except (OverflowError, ValueError, ZeroDivisionError):
            return ERR_NUM
        except RecursionError:
            return ERR_CYCLE

    # ── AST walk ──
    def _eval(self, node: object, sheet: str) -> Scalar:
        if isinstance(node, _Num):
            return node.value
        if isinstance(node, _Str):
            return node.value
        if isinstance(node, _Bool):
            return node.value
        if isinstance(node, _Ref):
            return self.cell_value(node.sheet, node.col, node.row, current=sheet)
        if isinstance(node, _Range):
            raise _FormulaError(ERR_VALUE)  # bare range in scalar context
        if isinstance(node, _Unary):
            val = _num(self._eval(node.operand, sheet))
            if node.op == "-":
                return -val
            if node.op == "%":
                return val / 100.0
            return val
        if isinstance(node, _Binary):
            return self._binary(node, sheet)
        if isinstance(node, _Call):
            return self._call(node, sheet)
        raise _FormulaError(ERR_VALUE)

    def _binary(self, node: _Binary, sheet: str) -> Scalar:
        op = node.op
        if op == "&":
            return _text(self._eval(node.left, sheet)) + _text(self._eval(node.right, sheet))
        if op in ("=", "<>", "<", "<=", ">", ">="):
            return self._compare(op, self._eval(node.left, sheet), self._eval(node.right, sheet))
        lhs = _num(self._eval(node.left, sheet))
        rhs = _num(self._eval(node.right, sheet))
        if op == "+":
            return lhs + rhs
        if op == "-":
            return lhs - rhs
        if op == "*":
            return lhs * rhs
        if op == "/":
            if rhs == 0:
                raise _FormulaError(ERR_DIV0)
            return lhs / rhs
        if op == "^":
            try:
                result = lhs**rhs
            except (OverflowError, ZeroDivisionError):
                raise _FormulaError(ERR_NUM) from None
            if isinstance(result, complex):
                raise _FormulaError(ERR_NUM)
            return float(result)
        raise _FormulaError(ERR_VALUE)

    @staticmethod
    def _compare(op: str, lhs: Scalar, rhs: Scalar) -> Scalar:
        if isinstance(lhs, CellError):
            raise _FormulaError(lhs)
        if isinstance(rhs, CellError):
            raise _FormulaError(rhs)
        if _is_number(lhs) and _is_number(rhs):
            a, b = lhs, rhs
        elif isinstance(lhs, str) and isinstance(rhs, str):
            a, b = lhs.casefold(), rhs.casefold()  # type: ignore[assignment]
        else:
            # Mixed types: Excel orders number < text < bool. Empty coerces to
            # the other side's zero value.
            def rank(v: Scalar) -> tuple[int, object]:
                if v is None:
                    return (0, 0.0)
                if _is_number(v):
                    return (0, v)
                if isinstance(v, str):
                    return (1, v.casefold())
                return (2, bool(v))

            a, b = rank(lhs), rank(rhs)  # type: ignore[assignment]
        if op == "=":
            return a == b
        if op == "<>":
            return a != b
        if op == "<":
            return a < b  # type: ignore[operator]
        if op == "<=":
            return a <= b  # type: ignore[operator]
        if op == ">":
            return a > b  # type: ignore[operator]
        return a >= b  # type: ignore[operator]

    # ── ranges / function args ──
    def _flatten(self, node: object, sheet: str) -> list[Scalar]:
        """Range → cell values (row-major); scalar node → [value]."""
        if isinstance(node, _Range):
            key = _sheet_key(node.sheet) if node.sheet else sheet
            if node.sheet and key not in self._sheets:
                return [ERR_REF]
            if (node.r2 - node.r1 + 1) * (node.c2 - node.c1 + 1) > 100_000:
                return [ERR_REF]
            return [
                self.cell_value(node.sheet, c, r, current=sheet)
                for r in range(node.r1, node.r2 + 1)
                for c in range(node.c1, node.c2 + 1)
            ]
        return [self._eval(node, sheet)]

    def _numbers(self, args: tuple[object, ...], sheet: str) -> list[float]:
        """Numeric values across args, skipping blanks/text from ranges (SUM rules)."""
        out: list[float] = []
        for arg in args:
            if isinstance(arg, _Range):
                for v in self._flatten(arg, sheet):
                    if isinstance(v, CellError):
                        raise _FormulaError(v)
                    if _is_number(v):
                        out.append(v)
            else:
                out.append(_num(self._eval(arg, sheet)))
        return out

    def _call(self, node: _Call, sheet: str) -> Scalar:
        name, args = node.name, node.args

        # Lazy forms first — their unused branch must not evaluate (nor error).
        if name == "IF":
            if not 2 <= len(args) <= 3:
                raise _FormulaError(ERR_VALUE)
            cond = self._eval(args[0], sheet)
            if isinstance(cond, CellError):
                raise _FormulaError(cond)
            truthy = bool(_num(cond)) if not isinstance(cond, str) else cond.strip() != ""
            if truthy:
                return self._eval(args[1], sheet)
            return self._eval(args[2], sheet) if len(args) == 3 else False
        if name == "IFERROR":
            if len(args) != 2:
                raise _FormulaError(ERR_VALUE)
            try:
                val = self._eval(args[0], sheet)
            except _FormulaError:
                return self._eval(args[1], sheet)
            return self._eval(args[1], sheet) if isinstance(val, CellError) else val

        fn = _FUNCTIONS.get(name)
        if fn is None:
            raise _FormulaError(ERR_NAME)
        return fn(self, args, sheet)


# ── function library ─────────────────────────────────────────────────────────

def _fn_sum(ev: _Evaluator, args, sheet) -> Scalar:
    return float(sum(ev._numbers(args, sheet)))


def _fn_product(ev: _Evaluator, args, sheet) -> Scalar:
    return float(math.prod(ev._numbers(args, sheet)))


def _fn_average(ev: _Evaluator, args, sheet) -> Scalar:
    nums = ev._numbers(args, sheet)
    if not nums:
        raise _FormulaError(ERR_DIV0)
    return sum(nums) / len(nums)


def _fn_median(ev: _Evaluator, args, sheet) -> Scalar:
    nums = sorted(ev._numbers(args, sheet))
    if not nums:
        raise _FormulaError(ERR_NUM)
    mid = len(nums) // 2
    return float(nums[mid]) if len(nums) % 2 else (nums[mid - 1] + nums[mid]) / 2


def _fn_min(ev: _Evaluator, args, sheet) -> Scalar:
    nums = ev._numbers(args, sheet)
    return float(min(nums)) if nums else 0.0


def _fn_max(ev: _Evaluator, args, sheet) -> Scalar:
    nums = ev._numbers(args, sheet)
    return float(max(nums)) if nums else 0.0


def _fn_count(ev: _Evaluator, args, sheet) -> Scalar:
    n = 0
    for arg in args:
        for v in ev._flatten(arg, sheet):
            if _is_number(v):
                n += 1
    return float(n)


def _fn_counta(ev: _Evaluator, args, sheet) -> Scalar:
    n = 0
    for arg in args:
        for v in ev._flatten(arg, sheet):
            if v is not None and v != "":
                n += 1
    return float(n)


def _fn_round(ev: _Evaluator, args, sheet, mode: str = "half") -> Scalar:
    if not 1 <= len(args) <= 2:
        raise _FormulaError(ERR_VALUE)
    value = _num(ev._eval(args[0], sheet))
    digits = int(_num(ev._eval(args[1], sheet))) if len(args) == 2 else 0
    factor = 10.0**digits
    scaled = value * factor
    if mode == "up":
        out = math.copysign(math.ceil(abs(scaled) - 1e-12), scaled)
    elif mode == "down":
        out = math.copysign(math.floor(abs(scaled) + 1e-12), scaled)
    else:  # Excel rounds half away from zero (not banker's rounding)
        out = math.copysign(math.floor(abs(scaled) + 0.5), scaled)
    return out / factor


def _fn_abs(ev: _Evaluator, args, sheet) -> Scalar:
    if len(args) != 1:
        raise _FormulaError(ERR_VALUE)
    return abs(_num(ev._eval(args[0], sheet)))


def _fn_sqrt(ev: _Evaluator, args, sheet) -> Scalar:
    if len(args) != 1:
        raise _FormulaError(ERR_VALUE)
    value = _num(ev._eval(args[0], sheet))
    if value < 0:
        raise _FormulaError(ERR_NUM)
    return math.sqrt(value)


def _fn_power(ev: _Evaluator, args, sheet) -> Scalar:
    if len(args) != 2:
        raise _FormulaError(ERR_VALUE)
    return ev._binary(_Binary("^", args[0], args[1]), sheet)


def _bool_args(ev: _Evaluator, args, sheet) -> list[bool]:
    """AND/OR operand list — errors propagate; blank cells are ignored (Excel)."""
    out: list[bool] = []
    for arg in args:
        for v in ev._flatten(arg, sheet):
            if isinstance(v, CellError):
                raise _FormulaError(v)
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            out.append(bool(_num(v)))
    return out


def _fn_and(ev: _Evaluator, args, sheet) -> Scalar:
    return all(_bool_args(ev, args, sheet))


def _fn_or(ev: _Evaluator, args, sheet) -> Scalar:
    return any(_bool_args(ev, args, sheet))


def _fn_not(ev: _Evaluator, args, sheet) -> Scalar:
    if len(args) != 1:
        raise _FormulaError(ERR_VALUE)
    return not bool(_num(ev._eval(args[0], sheet)))


def _fn_concat(ev: _Evaluator, args, sheet) -> Scalar:
    parts: list[str] = []
    for arg in args:
        for v in ev._flatten(arg, sheet):
            parts.append(_text(v))
    return "".join(parts)


def _fn_npv(ev: _Evaluator, args, sheet) -> Scalar:
    """NPV(rate, v1, v2, …) — first cash flow discounted one period (Excel rule)."""
    if len(args) < 2:
        raise _FormulaError(ERR_VALUE)
    rate = _num(ev._eval(args[0], sheet))
    if rate <= -1:
        raise _FormulaError(ERR_NUM)
    values = ev._numbers(args[1:], sheet)
    return float(sum(v / (1 + rate) ** (i + 1) for i, v in enumerate(values)))


def _fn_irr(ev: _Evaluator, args, sheet) -> Scalar:
    """IRR(values) — rate where NPV(values, t0-based) = 0. Bisection over a sign
    change; mirrors Excel closely enough for pitch-deck economics."""
    if not args:
        raise _FormulaError(ERR_VALUE)
    values = ev._numbers(args[:1], sheet)
    if len(args) > 1:  # optional guess — accepted, unused (bisection is robust)
        _num(ev._eval(args[1], sheet))
    if not values or all(v >= 0 for v in values) or all(v <= 0 for v in values):
        raise _FormulaError(ERR_NUM)

    def npv(rate: float) -> float:
        return sum(v / (1 + rate) ** i for i, v in enumerate(values))

    lo, hi = -0.9999, 10.0
    f_lo, f_hi = npv(lo), npv(hi)
    if f_lo * f_hi > 0:
        raise _FormulaError(ERR_NUM)
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = npv(mid)
        if abs(f_mid) < 1e-9:
            return mid
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return (lo + hi) / 2


def _fn_sumproduct(ev: _Evaluator, args, sheet) -> Scalar:
    if not args:
        raise _FormulaError(ERR_VALUE)
    arrays = [ev._flatten(arg, sheet) for arg in args]
    length = len(arrays[0])
    if any(len(a) != length for a in arrays):
        raise _FormulaError(ERR_VALUE)
    total = 0.0
    for i in range(length):
        prod = 1.0
        for arr in arrays:
            v = arr[i]
            if isinstance(v, CellError):
                raise _FormulaError(v)
            prod *= v if _is_number(v) else 0.0
        total += prod
    return total


_FUNCTIONS: dict[str, object] = {
    "SUM": _fn_sum,
    "PRODUCT": _fn_product,
    "AVERAGE": _fn_average,
    "MEDIAN": _fn_median,
    "MIN": _fn_min,
    "MAX": _fn_max,
    "COUNT": _fn_count,
    "COUNTA": _fn_counta,
    "ROUND": _fn_round,
    "ROUNDUP": lambda ev, args, sheet: _fn_round(ev, args, sheet, mode="up"),
    "ROUNDDOWN": lambda ev, args, sheet: _fn_round(ev, args, sheet, mode="down"),
    "ABS": _fn_abs,
    "SQRT": _fn_sqrt,
    "POWER": _fn_power,
    "AND": _fn_and,
    "OR": _fn_or,
    "NOT": _fn_not,
    "CONCATENATE": _fn_concat,
    "CONCAT": _fn_concat,
    "NPV": _fn_npv,
    "IRR": _fn_irr,
    "SUMPRODUCT": _fn_sumproduct,
}


# ── public entry point ───────────────────────────────────────────────────────

def evaluate_grid(grid: Grid, sheet_names: list[str]) -> None:
    """Compute every formula cell in place: `cell.value` becomes the cached
    result (float/str/bool/CellError). Non-formula cells are untouched.
    `sheet_names` must list every sheet so cross-sheet refs can be validated."""
    keys = {_sheet_key(n) for n in sheet_names}
    if not keys:
        return
    evaluator = _Evaluator(grid, default_sheet=next(iter(keys)), sheet_keys=keys)
    for (sheet, col, row), cell in grid.items():
        if cell.formula is not None:
            # grid keys are already sheet keys — pass through so the formula's
            # unqualified refs resolve against its own sheet.
            cell.value = evaluator.cell_value(sheet, col, row)
