"""PDS retrieval via local Chroma, with a keyword fallback for offline reliability."""

from __future__ import annotations

import logging
from functools import lru_cache

from aussiecare.config import CHROMA_DIR, PDS_DIR, openai_http_client

logger = logging.getLogger(__name__)
COLLECTION = "aussiecare-pds"


def _load_pds_docs() -> list[tuple[str, str]]:
    docs: list[tuple[str, str]] = []
    for path in sorted(PDS_DIR.glob("*.md")):
        docs.append((path.stem, path.read_text(encoding="utf-8")))
    return docs


def _keyword_search(query: str, k: int = 4) -> list[str]:
    q = query.lower()
    scored: list[tuple[int, str]] = []
    for _name, text in _load_pds_docs():
        score = 0
        for word in q.split():
            if len(word) > 3 and word in text.lower():
                score += 1
        if "flood" in q and "flood" in text.lower():
            score += 5
        if "kangaroo" in q and "kangaroo" in text.lower():
            score += 5
        if "hire" in q or "rental" in q:
            if "hire" in text.lower() or "rental" in text.lower():
                score += 5
        if any(w in q for w in ("storm", "tree", "power", "kids", "ses")):
            if "ses" in text.lower() or "emergency" in text.lower():
                score += 4
        if score:
            scored.append((score, text))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [text for _score, text in scored[:k]] or [text for _name, text in _load_pds_docs()[:k]]


@lru_cache(maxsize=1)
def _chroma_ready() -> bool:
    try:
        from langchain_chroma import Chroma
        from langchain_openai import OpenAIEmbeddings

        CHROMA_DIR.mkdir(parents=True, exist_ok=True)
        embeddings = OpenAIEmbeddings(http_client=openai_http_client())
        store = Chroma(
            collection_name=COLLECTION,
            persist_directory=str(CHROMA_DIR),
            embedding_function=embeddings,
        )
        existing = store.get(include=[])
        if not existing.get("ids"):
            texts = []
            metadatas = []
            ids = []
            for name, text in _load_pds_docs():
                for index, block in enumerate(_split_blocks(text)):
                    texts.append(block)
                    metadatas.append({"source": name})
                    ids.append(f"{name}-{index}")
            if texts:
                store.add_texts(texts=texts, metadatas=metadatas, ids=ids)
        return True
    except Exception:
        logger.warning("Chroma/OpenAI embeddings unavailable; using keyword PDS retrieval.")
        return False


def _split_blocks(text: str) -> list[str]:
    blocks = [block.strip() for block in text.split("\n\n") if block.strip()]
    return blocks or [text]


def retrieve_pds_chunks(query: str, k: int = 4) -> list[str]:
    if _chroma_ready():
        try:
            from langchain_chroma import Chroma
            from langchain_openai import OpenAIEmbeddings

            store = Chroma(
                collection_name=COLLECTION,
                persist_directory=str(CHROMA_DIR),
                embedding_function=OpenAIEmbeddings(http_client=openai_http_client()),
            )
            hits = store.similarity_search(query, k=k)
            chunks = [hit.page_content for hit in hits]
            if chunks:
                return chunks
        except Exception:
            logger.exception("Chroma search failed; falling back to keywords.")
    return _keyword_search(query, k=k)
