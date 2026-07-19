"""plugin/bundle.py — 플러그인 번들 레이아웃 워커(경로·이름 규칙의 단일 소유).

플러그인 번들의 세 프롬프트 표면 디렉토리를 걷는 규칙이 loader(주입·스캔)와
builtin(카탈로그 메타)에 중복돼 있었다 — commands/agents 소비(2026-07-19)로
세 번째 사본이 생기기 전에 여기로 모은다. 순수 경로 함수만: 파일을 읽거나
파싱하지 않는다(그건 loader 몫).

레이아웃(CONTRACTS §2.2):
  skills/*.md              flat 스킬
  skills/<sub>/SKILL.md    표준 스킬(형제 파일 = 번들 리소스)
  commands/**/*.md         슬래시 커맨드(중첩 = `:` 네임스페이스)
  agents/*.md              서브에이전트 페르소나
"""

from __future__ import annotations

from pathlib import Path

# 플러그인당 command 파일 상한 — 인덱스/프롬프트 표면에 실리므로 bounded.
COMMANDS_MAX = 40
# 플러그인당 agent 파일 상한 — delegate 도구 스펙/페르소나 조회 표면도 bounded.
AGENTS_MAX = 40


def skill_md_paths(plugin_dir: Path) -> list[Path]:
    """스킬 md 경로 — 두 레이아웃(flat + 표준) 정렬 합본. skills/ 없으면 빈 목록."""
    skills_dir = plugin_dir / "skills"
    if not skills_dir.is_dir():
        return []
    return sorted(skills_dir.glob("*.md")) + sorted(skills_dir.glob("*/SKILL.md"))


def skill_name_for(md_path: Path) -> str:
    """스킬 md 경로 → 표시 이름(파일 stem, 표준 레이아웃은 디렉토리 이름).
    frontmatter `name` 이 있으면 그쪽이 이기고, 이건 폴백/카탈로그용이다."""
    return md_path.stem if md_path.name != "SKILL.md" else md_path.parent.name


def command_paths(plugin_dir: Path) -> list[Path]:
    """`commands/**/*.md` — 정렬·상한 적용(숨김 경로 제외)."""
    commands_dir = plugin_dir / "commands"
    if not commands_dir.is_dir():
        return []
    try:
        paths = sorted(
            p
            for p in commands_dir.rglob("*.md")
            if p.is_file()
            and not any(part.startswith(".") for part in p.relative_to(commands_dir).parts)
        )
    except OSError:
        return []
    return paths[:COMMANDS_MAX]


def command_name(plugin_dir: Path, md_path: Path) -> str:
    """커맨드 이름: 파일 stem, 중첩 디렉토리는 `:` 로 잇는다(git/commit.md →
    git:commit — Claude Code 네임스페이스 관례와 동형)."""
    rel = md_path.relative_to(plugin_dir / "commands")
    return ":".join([*rel.parts[:-1], rel.stem])


def agent_paths(plugin_dir: Path) -> list[Path]:
    """`agents/*.md` — 정렬·상한 적용(평면만, Claude Code 관례와 동일)."""
    agents_dir = plugin_dir / "agents"
    if not agents_dir.is_dir():
        return []
    return sorted(p for p in agents_dir.glob("*.md") if p.is_file())[:AGENTS_MAX]


def display_skill_names(plugin_dir: Path) -> tuple[str, ...]:
    """카탈로그/UI 용 이름 목록 — 스킬(두 레이아웃) + 커맨드. frontmatter 를 읽지
    않는 경로 유래 이름이다(빠른 메타 조회용 — 주입 경로는 frontmatter 우선)."""
    names = [skill_name_for(p) for p in skill_md_paths(plugin_dir)]
    names += [command_name(plugin_dir, p) for p in command_paths(plugin_dir)]
    return tuple(names)
