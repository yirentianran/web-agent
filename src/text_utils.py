"""Shared text processing utilities."""


def strip_markdown_fences(text: str) -> str:
    """Remove ``` markdown code fences from LLM response text."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines)
    return text
