"""Keyword matching and snippet extraction."""

import re


class KeywordMatcher:
    """Case-insensitive, word-boundary matcher that also accepts possessives ("Deni's", "Denis")."""

    def __init__(self, keywords: list[str]):
        unique: dict[str, str] = {}
        for kw in keywords:
            kw = kw.strip()
            if kw:
                unique.setdefault(kw.lower(), kw)
        self.keywords = list(unique.values())
        # Longest first so multi-word keywords win over their parts.
        alternatives = sorted(self.keywords, key=len, reverse=True)
        body = "|".join(re.escape(k).replace(r"\ ", r"\s+") for k in alternatives)
        self.pattern = re.compile(rf"\b(?:{body})(?:['\u2019]?s)?\b", re.IGNORECASE) if body else None

    def find(self, text: str) -> list[str]:
        """Return the configured keywords found in text, in configured order."""
        if not text or not self.pattern:
            return []
        found = {self._canonical(m.group(0)) for m in self.pattern.finditer(text)}
        return [k for k in self.keywords if k in found]

    def snippet(self, text: str, radius: int = 220) -> str:
        """Return a window of text around the first match, so the keyword is visible in the UI."""
        if not text:
            return ""
        m = self.pattern.search(text) if self.pattern else None
        if not m:
            return _truncate(text, radius * 2)
        start = max(0, m.start() - radius)
        end = min(len(text), m.end() + radius)
        if start > 0:
            space = text.find(" ", start)
            start = space + 1 if 0 <= space < m.start() else start
        if end < len(text):
            space = text.rfind(" ", m.end(), end)
            end = space if space > m.end() else end
        return ("…" if start > 0 else "") + text[start:end].strip() + ("…" if end < len(text) else "")

    def _canonical(self, matched: str) -> str:
        norm = re.sub(r"['\u2019]?s$", "", matched, flags=re.IGNORECASE)
        norm = " ".join(norm.split()).lower()
        for k in self.keywords:
            if k.lower() == norm or k.lower() == matched.lower():
                return k
        return norm


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rsplit(" ", 1)[0] + "…"
