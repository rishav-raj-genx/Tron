"""
Prompt Sanitization & Injection Defense Utility.
Sanitizes external web snippets, arXiv abstracts, and news titles before
injecting them into LLM evaluation and deduplication prompts.
"""

import re

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", re.IGNORECASE),
    re.compile(r"(system\s+prompt|system\s+instruction|developer\s+mode):?", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(a|an)?\s*[a-zA-Z0-9_\-\s]+", re.IGNORECASE),
    re.compile(r"<\s*/?(system|user|assistant|im_start|im_end|instruction)\s*>", re.IGNORECASE),
    re.compile(r"\[\s*/?(INST|SYS)\s*\]", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|above)\s+guidelines?", re.IGNORECASE),
]


def sanitize_untrusted_text(text: str, max_chars: int = 1500) -> str:
    """
    Sanitize external untrusted text (news titles, snippets, arXiv abstracts)
    before injecting into LLM prompts.

    1. Removes non-printable characters and control codes (keeps standard whitespace).
    2. Neutralizes prompt delimiter markers (markdown fences, triple quotes, horizontal rules).
    3. Neutralizes common prompt injection attack phrases.
    4. Enforces a strict maximum length bound to prevent excessive token consumption.
    """
    if not text:
        return ""

    # Keep only printable characters and normal whitespace
    cleaned = "".join(ch for ch in str(text) if ch.isprintable() or ch in "\n\r\t")

    # Neutralize prompt delimiters and markdown code fences
    cleaned = cleaned.replace("```", "'''")
    cleaned = cleaned.replace("===", "---")
    cleaned = cleaned.replace("<|im_start|>", "[im_start]")
    cleaned = cleaned.replace("<|im_end|>", "[im_end]")

    # Neutralize known prompt injection attack phrases
    for pattern in INJECTION_PATTERNS:
        cleaned = pattern.sub("[FILTERED_PROMPT_INJECTION]", cleaned)

    return cleaned[:max_chars].strip()
