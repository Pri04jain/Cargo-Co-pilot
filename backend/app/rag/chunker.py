"""
Text chunker for RAG indexing.

Splits document page text into overlapping chunks suitable for embedding.

Design choices:
  - Chunk at sentence boundaries when possible (avoids splitting mid-sentence,
    which hurts retrieval quality).
  - Overlap between chunks (default 50 tokens) so a sentence that straddles a
    chunk boundary is still retrievable from both chunks.
  - Per-page chunking so every chunk knows its page number — essential for
    the citation system ("from page 2 of invoice INV-001").
  - We keep chunks small (≈ 400 tokens) so the embedding captures a focused
    semantic unit rather than blending many unrelated facts.

Why not use LangChain's text splitter?
  LangChain's RecursiveCharacterTextSplitter works fine, but it's a heavy
  dependency. Our shipping documents are structured and short — a simple
  sentence-boundary splitter is easier to understand, test, and explain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TextChunk:
    text: str
    page_number: int
    chunk_index: int          # sequential index within the whole document
    char_start: int = 0       # character offset within the page text


def chunk_document(
    pages: list[tuple[int, str]],  # [(page_number, raw_text), ...]
    max_tokens: int = 400,
    overlap_tokens: int = 50,
    chars_per_token: float = 4.0,  # rough approximation; avoids a tokenizer call
) -> list[TextChunk]:
    """
    Split a document's pages into overlapping text chunks.

    Args:
        pages: list of (page_number, text) tuples in page order
        max_tokens: maximum tokens per chunk (approximate)
        overlap_tokens: tokens to overlap between adjacent chunks
        chars_per_token: chars-per-token estimate for size calculation

    Returns:
        list of TextChunk objects, chunk_index is document-global

    The approximate size approach avoids importing tiktoken here, which would
    add latency to every chunk operation. The eval harness validates that chunks
    are below the embedding model's actual token limit.
    """
    max_chars = int(max_tokens * chars_per_token)
    overlap_chars = int(overlap_tokens * chars_per_token)

    all_chunks: list[TextChunk] = []
    global_index = 0

    for page_number, text in pages:
        if not text or not text.strip():
            continue

        # Split into sentences — use a simple regex that handles
        # common abbreviations and decimal numbers without splitting them
        sentences = _split_sentences(text)

        page_chunks = _pack_sentences(
            sentences=sentences,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )

        for char_start, chunk_text in page_chunks:
            if chunk_text.strip():
                all_chunks.append(TextChunk(
                    text=chunk_text.strip(),
                    page_number=page_number,
                    chunk_index=global_index,
                    char_start=char_start,
                ))
                global_index += 1

    return all_chunks


def _split_sentences(text: str) -> list[str]:
    """
    Split text into sentences, preserving the separator.

    Handles:
      - "Mr. Smith" and "3.14" (avoids splitting on abbreviation dots)
      - Newlines as soft sentence breaks
      - Consecutive newlines as hard paragraph breaks
    """
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # Split on sentence-ending punctuation followed by whitespace,
    # OR on double newlines (paragraph breaks)
    pattern = r'(?<=[.!?])\s+(?=[A-Z])|(?:\n\n+)'
    parts = re.split(pattern, text)

    # Clean and filter empty parts
    return [p.strip() for p in parts if p.strip()]


def _pack_sentences(
    sentences: list[str],
    max_chars: int,
    overlap_chars: int,
) -> list[tuple[int, str]]:
    """
    Greedily pack sentences into chunks of at most max_chars.
    Returns list of (char_start_offset, chunk_text).
    """
    chunks: list[tuple[int, str]] = []
    current: list[str] = []
    current_len = 0
    char_offset = 0

    for sentence in sentences:
        sentence_len = len(sentence) + 1  # +1 for space separator

        if current_len + sentence_len > max_chars and current:
            # Flush current chunk
            chunk_text = " ".join(current)
            chunks.append((char_offset, chunk_text))

            # Keep last few sentences as overlap for the next chunk
            overlap_text = ""
            overlap_sentences: list[str] = []
            for s in reversed(current):
                if len(overlap_text) + len(s) < overlap_chars:
                    overlap_sentences.insert(0, s)
                    overlap_text += s
                else:
                    break

            current = overlap_sentences
            current_len = sum(len(s) + 1 for s in current)
            char_offset += len(chunk_text)

        current.append(sentence)
        current_len += sentence_len

    # Flush final chunk
    if current:
        chunks.append((char_offset, " ".join(current)))

    return chunks
