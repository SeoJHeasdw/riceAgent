"""SessionTitler — names a chat from its opening exchange (the "name this
conversation" step). Behavior lives here; the orchestrator only sequences it.

WHY A MODEL CALL, not first-line truncation: a real summary reads like the titles
in ChatGPT/Claude/Gemini and, because the prompt says so, comes back in the
conversation's own language — no locale wiring.

WHY HERE, not the route: the best title draws on the first user message AND the
first answer, which only exist once the turn has run, so the orchestrator triggers
this post-answer.

FAILURE IS NON-FATAL: any provider/parse error (or the echo fallback, which can't
summarize) returns None; the session stays untitled and the next turn retries.
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

from ..core.types import Message
from ..prompts import TITLE_MAX_CHARS, build_title_messages, clean_title
from .interfaces import ISessionTitler
from .providers.interfaces import ILLMProvider

log = logging.getLogger("rice.agent.titler")

# Bound the content fed to the titler so a long first answer can't balloon tokens.
_MAX_SNIPPET_CHARS = 1000
# Title generation is short; keep it low-variance so reopened chats read steadily.
_TITLE_TEMPERATURE = 0.3


class SessionTitler(ISessionTitler):
    """Implements `agent.interfaces.ISessionTitler`. Decoupled from the session
    store: it reads/writes the title through two injected callables, so it depends
    on neither `ISessionStore` (server) nor `ITranscript` (agent)."""

    def __init__(
        self,
        *,
        resolve_provider: Callable[[], ILLMProvider],
        get_title: Callable[[str], str | None],
        set_title: Callable[[str, str], None],
    ) -> None:
        self._resolve_provider = resolve_provider
        self._get_title = get_title
        self._set_title = set_title

    async def maybe_title(self, session_id: str, history: Sequence[Message]) -> str | None:
        # First time only — never relabel a session the user may already see named.
        if self._get_title(session_id):
            return None
        first_user = next((m.content for m in history if m.role == "user"), "")
        if not first_user.strip():
            return None
        answer = next(
            (m.content for m in reversed(history) if m.role == "assistant" and m.content),
            "",
        )
        provider = self._resolve_provider()
        # The echo fallback (no API key / offline) echoes input — a useless title.
        if getattr(provider, "name", "") == "echo":
            return None
        try:
            title = await self._generate(provider, first_user, answer)
        except Exception:  # titling must never break a turn
            log.exception("title generation failed for session %s", session_id)
            return None
        if not title:
            return None
        # The user may manually rename the session while this model call is in
        # flight. Re-check after awaiting so a late generated title never wins.
        if self._get_title(session_id):
            return None
        self._set_title(session_id, title)
        return title

    async def _generate(self, provider: ILLMProvider, first_user: str, answer: str) -> str:
        system, messages = build_title_messages(
            first_user[:_MAX_SNIPPET_CHARS], answer[:_MAX_SNIPPET_CHARS]
        )
        parts: list[str] = []
        async for chunk in provider.stream(
            system=system, messages=messages, tools=(), temperature=_TITLE_TEMPERATURE
        ):
            if chunk.kind == "text" and chunk.text:
                parts.append(chunk.text)
                # A title is one short line — stop as soon as we have it so a rambly
                # model can't drag this call out (it would otherwise stall the
                # client's post-turn title request).
                joined = "".join(parts)
                if "\n" in joined or len(joined) >= TITLE_MAX_CHARS:
                    break
        return clean_title("".join(parts))
