from __future__ import annotations


def chunk_markdown(markdown: str, *, max_chars: int = 1400, overlap: int = 180) -> list[str]:
    text = (markdown or "").strip()
    if not text:
        return []

    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        candidate = f"{current}\n\n{paragraph}".strip() if current else paragraph
        if current and len(candidate) > max_chars:
            chunks.append(current)
            if overlap > 0 and len(current) > overlap:
                current = current[-overlap:].strip()
                current = f"{current}\n\n{paragraph}".strip()
            else:
                current = paragraph
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks
