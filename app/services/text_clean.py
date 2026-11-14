"""Turn whatever a small language model returned for a TEXT field into plain readable text.

Small models sometimes answer a text field with an object ({"hook": "...whole story..."}), a list of paragraphs, or a
string that is itself a printed dict ("{'hook': \"...\\r\\n\\r\\n...\"}"). Saving that with str() shows the user Python
syntax and literal \\r\\n in the app (and feeds it to later prompts). `clean_text` unwraps all of those."""
from __future__ import annotations

import ast
import json
import re
from typing import Any


def _unwrap_string(text: str) -> Any:
    """A string that really is a JSON / Python-literal object or list -> that object; anything else -> the string."""
    t = text.strip()
    if len(t) > 1 and t[0] in "{[" and t[-1] in "}]":
        for loader in (json.loads, ast.literal_eval):
            try:
                value = loader(t)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(value, (dict, list)):
                return value
    return text


def clean_text(value: Any, _depth: int = 0) -> str:
    """Plain text with real line breaks: objects/lists are flattened (values only, in order, blank line between)."""
    if value is None or _depth > 6:
        return ""
    if isinstance(value, str):
        inner = _unwrap_string(value)
        if not isinstance(inner, str):
            return clean_text(inner, _depth + 1)
        text = inner
        if "\n" not in text and "\\n" in text:           # escaped line breaks that were never turned into real ones
            text = text.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    if isinstance(value, dict):
        parts = [clean_text(v, _depth + 1) for v in value.values()]
        return "\n\n".join(p for p in parts if p)
    if isinstance(value, (list, tuple)):
        parts = [clean_text(v, _depth + 1) for v in value]
        return "\n\n".join(p for p in parts if p)
    return str(value).strip()
