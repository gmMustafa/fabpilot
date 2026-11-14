"""Text -> vector, locally (3.2b). fastembed runs the model with ONNX Runtime: no PyTorch, no API.

The rest of the code only sees the `Embedder` protocol, so the library can be swapped (e.g. to
sentence-transformers) by adding one class and re-ingesting. `model_id` is stored with every
document: vectors from different models must never be compared.

Embedding is CPU-bound and synchronous. Async callers (the API, 3.3) must run it off the event loop:
    vec = await asyncio.to_thread(embedder.embed_query, question)
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

from app.db.models import EMBEDDING_DIM

# bge v1.5: queries MAY get this instruction, passages never (model card). It's optional for v1.5;
# whether it helps on our corpus is measured in 3.4, not assumed.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class Embedder(Protocol):
    model_id: str  # stored per document, e.g. "fastembed-0.9.1:Qdrant/bge-small-en-v1.5-onnx-Q"
    dim: int

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...

    def count_tokens(self, text: str) -> int: ...


class FastEmbedder:
    """bge-small-en-v1.5 via fastembed. Loading takes a few seconds: create ONE per process."""

    def __init__(self, model_name: str, cache_dir: Path, *, query_prefix: str = BGE_QUERY_PREFIX):
        import fastembed  # heavy import (onnxruntime): only when an embedder is really built
        from fastembed import TextEmbedding
        from tokenizers import Tokenizer

        from app.runtime import use_system_certificates

        use_system_certificates()  # first run downloads the model (see runtime.py)
        self._model = TextEmbedding(model_name, cache_dir=str(cache_dir))
        # Public on purpose: the retrieval eval (3.4) switches it off to measure what it's worth.
        self.query_prefix = query_prefix  # set before the first embed_query() below
        source = next(
            m.sources.hf for m in TextEmbedding._list_supported_models() if m.model == model_name
        )
        # The library version is part of the id: fastembed could change the ONNX export (this one
        # is quantized: "-onnx-Q") in an update, and that changes the vectors.
        self.model_id = f"fastembed-{fastembed.__version__}:{source}"
        self.dim = len(self.embed_query("probe"))
        if self.dim != EMBEDDING_DIM:
            raise ValueError(f"{model_name} has {self.dim} dims, the schema has {EMBEDDING_DIM}")

        # A COPY of the model's tokenizer, for counting only. The model's own one (and fastembed's
        # public token_count()) truncates at 512, so it can never report a chunk as too long:
        # 2 002 tokens are counted as 512. `.model.tokenizer` is not public API; the live test
        # (test_embeddings_live) fails if it moves.
        onnx_model: Any = self._model.model  # .tokenizer isn't on fastembed's typed base class
        counter = Tokenizer.from_str(onnx_model.tokenizer.to_str())
        counter.no_truncation()
        counter.no_padding()
        self._counter = counter

    def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = [v.tolist() for v in self._model.passage_embed(list(texts))]
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vector: list[float] = next(iter(self._model.query_embed(self.query_prefix + text))).tolist()
        return vector

    def count_tokens(self, text: str) -> int:
        """Real model tokens, including [CLS]/[SEP], never truncated."""
        return len(self._counter.encode(text).ids)


def build_embedder(model_name: str, cache_dir: Path) -> Embedder:
    return FastEmbedder(model_name, cache_dir)
