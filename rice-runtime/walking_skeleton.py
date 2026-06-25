"""Walking skeleton — prove the seam end to end, no web/LLM deps.

Runs the FULL turn path with the offline `echo` provider:

    build_container (composition root)
      → orchestrator.run_turn          (the convergence point)
        → learning.prefetch            (injects guidance)
        → tools = builtin+connector+plugin (empty in the skeleton)
        → AgentLoop → EchoProvider     (streams thinking + message)
        → learning.observe             (no effects yet)
      → SSE events

It persists into an isolated `RICE_HOME` (a temp dir) so it touches nothing real,
then prints every SSE event the turn emits. If this prints `thinking → message* →
done`, the spine is wired correctly.

    python walking_skeleton.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from rice_runtime import build_container  # noqa: E402
from rice_runtime.core.config import RuntimeArgs  # noqa: E402
from rice_runtime.core.paths import RicePaths  # noqa: E402


async def main() -> int:
    tmp = tempfile.mkdtemp(prefix="rice-skeleton-")
    container = build_container(RuntimeArgs(), paths=RicePaths.from_env(tmp))
    print(f"# RICE_HOME={tmp}")
    print(f"# provider={container.resolve_provider().name}\n")

    session = container.sessions.create(model="echo")
    print(f"# session={session.id}\n")

    print("# --- streaming turn: 'hello rice' ---")
    async for event in container.agent.run_turn(session.id, "hello rice"):
        print(f"  [{event.type.value:>22}]  {event.data}")

    # confirm persistence round-tripped through the session store
    history = container.sessions.history(session.id)
    print(f"\n# persisted {len(history)} messages:")
    for m in history:
        print(f"  - {m.role}: {m.content!r}")

    container.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
