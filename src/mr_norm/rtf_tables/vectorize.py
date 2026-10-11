"""Attach vectors to chunks. The embedder is injected: this module does not load a model.

Search embeds the question at query time. Indexing embeds `chunk["text"]` with the
same model the vector tool already uses (`deepvk/USER-bge-m3`). No new search code
is required for a table row to be found: the vector tool and the text tool both
read that text, and the indexer stores the whole payload.
"""

from __future__ import annotations

from typing import Any, Callable


Encode = Callable[[list[str]], list[list[float]]]


def attach_vectors(chunks: list[dict[str, Any]], encode: Encode) -> list[dict[str, Any]]:
    if not chunks:
        return chunks
    vectors = encode([str(chunk.get("text") or "") for chunk in chunks])
    if len(vectors) != len(chunks):
        raise ValueError(f"encode returned {len(vectors)} vectors for {len(chunks)} chunks")
    for chunk, vector in zip(chunks, vectors):
        chunk["vector"] = [float(value) for value in vector]
    return chunks

