"""Table-aware RTF chunking and vector attachment utilities."""

from mr_norm.rtf_tables.chunk import MAX_POINT_TOKENS, chunk_rtf
from mr_norm.rtf_tables.vectorize import attach_vectors

__all__ = ["MAX_POINT_TOKENS", "attach_vectors", "chunk_rtf"]
