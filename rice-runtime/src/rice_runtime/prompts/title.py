"""Conversation-title prompt — the "name this chat" summarizer.

One instruction shapes every session title, so it lives in one place (not in the
route or the orchestrator). Language is deliberately NOT a parameter: the prompt
tells the model to title in the conversation's own language, so a Korean chat gets
a Korean title and an English chat an English one — no locale plumbing, matching
how ChatGPT/Claude/Gemini name conversations.
"""

from __future__ import annotations

from ..core.types import Message

# Hard ceiling so a misbehaving model can't return a paragraph as a "title".
TITLE_MAX_CHARS = 60

_TITLE_SYSTEM = (
    "You generate a short title for a chat conversation, like the conversation "
    "titles shown in ChatGPT or Claude.\n"
    "Rules:\n"
    "- 2 to 6 words; a noun phrase, never a full sentence.\n"
    "- Write the title in the SAME LANGUAGE the user is writing in.\n"
    "- Capture the topic; ignore greetings and pleasantries.\n"
    '- No surrounding quotes, no trailing punctuation, no "Title:" prefix.\n'
    "Respond with the title text and nothing else."
)


def build_title_messages(first_user: str, assistant_answer: str = "") -> tuple[str, list[Message]]:
    """Build (system, messages) for a one-shot title generation from the opening
    exchange. `assistant_answer` is optional — a turn can be titled from just the
    user's first message when no answer is available."""
    excerpt = f"User: {first_user.strip()}"
    if assistant_answer.strip():
        excerpt += f"\n\nAssistant: {assistant_answer.strip()}"
    return _TITLE_SYSTEM, [Message(role="user", content=f"Title this conversation.\n\n{excerpt}")]


def clean_title(raw: str) -> str:
    """Normalize a model-returned title: first non-empty line, strip quotes/markup
    and an accidental 'Title:' prefix, then cap to `TITLE_MAX_CHARS`."""
    line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    if line[:6].lower() == "title:":
        line = line[6:].strip()
    line = line.strip("\"'`").strip()
    return line[:TITLE_MAX_CHARS].strip()
