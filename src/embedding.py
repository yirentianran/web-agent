"""Embedding API stub — Phase 2 optional."""


def pack_embedding(arr):
    """Pack numpy array to bytes (Phase 2)."""
    raise NotImplementedError("Embedding not configured")


def unpack_embedding(blob: bytes):
    """Unpack bytes to numpy array (Phase 2)."""
    raise NotImplementedError("Embedding not configured")


async def embed_text(text: str):
    """Call embedding API (Phase 2)."""
    raise NotImplementedError(
        "Embedding not configured. Set EMBEDDING_API_URL and EMBEDDING_API_KEY."
    )
