"""The REAL embedding model. Deselected by default (marker `embedding`): it needs a ~130 MB download
on first run. Run it after changing the model or upgrading fastembed:

    uv run pytest -m embedding -v
"""

import math

import pytest

from app.config import get_settings
from app.db.models import EMBEDDING_DIM
from app.rag.chunking import chunk_document
from app.rag.corpus import load_corpus
from app.rag.embeddings import FastEmbedder, build_embedder

pytestmark = pytest.mark.embedding

MODEL_LIMIT = 512  # bge-small-en-v1.5: anything longer is silently truncated


@pytest.fixture(scope="module")
def embedder() -> FastEmbedder:
    s = get_settings()
    emb = build_embedder(s.embedding_model, s.embedding_cache_dir)
    assert isinstance(emb, FastEmbedder)
    return emb


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def test_vectors_have_the_schema_dimension_and_unit_length(embedder: FastEmbedder) -> None:
    v = embedder.embed_passages(["Clean the suction strainer basket."])[0]
    assert len(v) == EMBEDDING_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in v)), 1.0, rel_tol=1e-3)  # cosine = dot


def test_model_id_names_library_version_and_quantized_source(embedder: FastEmbedder) -> None:
    assert embedder.model_id.startswith("fastembed-")
    assert embedder.model_id.endswith(":Qdrant/bge-small-en-v1.5-onnx-Q")


def test_meaning_beats_wording(embedder: FastEmbedder) -> None:
    """The point of embeddings: a paraphrase is closer than a text sharing words but not meaning."""
    q = embedder.embed_query("the pump strainer is blocked")
    near, far = embedder.embed_passages(
        [
            "Clogged suction strainer: differential pressure rises, discharge pressure falls.",
            "Pump the brakes and block the road before the strainer truck arrives.",
        ]
    )
    assert _cos(q, near) > _cos(q, far)


def test_counting_never_truncates(embedder: FastEmbedder) -> None:
    """The model's tokenizer caps at 512; our counter must not, or it can't see long chunks."""
    assert embedder.count_tokens("word " * 2000) > 2000


def test_every_corpus_chunk_fits_the_model(embedder: FastEmbedder) -> None:
    budget = get_settings().chunk_max_tokens
    for doc in load_corpus(get_settings().docs_dir):
        for c in chunk_document(doc, embedder.count_tokens, budget):
            assert c.token_count <= budget < MODEL_LIMIT, (c.doc_id, c.section_ref, c.token_count)
