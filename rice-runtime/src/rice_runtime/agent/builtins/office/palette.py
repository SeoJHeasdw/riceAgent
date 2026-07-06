"""office/palette.py — named theme palettes + a *lenient* resolver.

Shared by the .pptx and .docx builders so a `theme` value means the same thing
everywhere: a bare preset name ("forest"), a preset + per-color overrides
({"preset": "forest", "accent": "F97316"}), or a fully custom
{primary, secondary, accent} triad.

Every preset is a curated dark-primary / light-secondary / sharp-accent triad
that already passes "white text readable on primary" — picking one removes the
single most common way a (small) model wrecks a document: guessing clashing hex.

Leniency is deliberate. A malformed color or an unknown preset must NEVER abort
document generation — for a local sLLM a single bad field would otherwise cost
the whole file. `resolve_palette` therefore degrades to a sane default and
appends a human-readable note to `warnings` instead of raising.
"""

from __future__ import annotations

import re

__all__ = ["PRESETS", "DEFAULT", "resolve_palette"]

_HEX = re.compile(r"^#?([0-9A-Fa-f]{6})$")

DEFAULT: dict[str, str] = {"primary": "1E2761", "secondary": "CADCFC", "accent": "F96167"}

PRESETS: dict[str, dict[str, str]] = {
    "forest": {"primary": "1B4332", "secondary": "D8F3DC", "accent": "F4A259"},
    "health": {"primary": "0F5257", "secondary": "CDEDE3", "accent": "FF7A5C"},
    "finance": {"primary": "0A2540", "secondary": "D6E4F0", "accent": "18A957"},
    "midnight": {"primary": "1A1B3A", "secondary": "C9CCE8", "accent": "F65A83"},
    "rose": {"primary": "6A213A", "secondary": "F7D9E0", "accent": "E8A33D"},
    "mono": {"primary": "24272B", "secondary": "E3E5E8", "accent": "6C7A89"},
}


def _hex(value: object, fallback: str, field: str, warnings: list[str]) -> str:
    if value is None:
        return fallback
    m = _HEX.match(str(value).strip())
    if not m:
        warnings.append(f"theme.{field}: {value!r} is not 6-digit hex — using {fallback}")
        return fallback
    return m.group(1).upper()


def resolve_palette(raw: object, warnings: list[str] | None = None) -> dict[str, str]:
    """`theme` value → {primary, secondary, accent} hex triad. Never raises:
    unknown preset / bad hex degrade to the default and note it in `warnings`."""
    warn = warnings if warnings is not None else []
    if isinstance(raw, str):
        raw = {"preset": raw}
    raw = raw if isinstance(raw, dict) else {}

    base = DEFAULT
    preset = raw.get("preset")
    if preset is not None:
        key = str(preset).strip().lower()
        if key in PRESETS:
            base = PRESETS[key]
        else:
            warn.append(
                f"theme.preset {preset!r} is unknown — using default palette "
                f"(presets: {', '.join(sorted(PRESETS))})"
            )

    return {
        "primary": _hex(raw.get("primary"), base["primary"], "primary", warn),
        "secondary": _hex(raw.get("secondary"), base["secondary"], "secondary", warn),
        "accent": _hex(raw.get("accent"), base["accent"], "accent", warn),
    }
