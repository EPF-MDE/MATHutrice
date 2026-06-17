"""
step3_vectorstore.py — BDD vectorielle pgvector + LangChain
============================================================
Version pgvector (PostgreSQL existant — déploiement EPF).

Interface IDENTIQUE à la version ChromaDB (aliases garantis).
Pour revenir à ChromaDB : remplacer ce fichier, rien d'autre ne change.

Table créée automatiquement dans PostgreSQL :
  langchain_pg_collection / langchain_pg_embedding

Installation :
    pip install pgvector psycopg2-binary langchain-postgres langchain-huggingface

.env requis :
    DATABASE_URL=postgresql://postgres:password@localhost:5432/mathutrice

Prérequis SQL (une fois) :
    CREATE EXTENSION IF NOT EXISTS vector;

Emplacement : generator_test/rag/step3_vectorstore.py
"""

import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Chemins (compatibilité avec l'ancien code ChromaDB) ─
_THIS_DIR  = Path(__file__).resolve().parent
OUT_DIR    = _THIS_DIR / "output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Conservés pour compatibilité — non utilisés avec pgvector
CHROMA_DIR  = OUT_DIR / "chromadb"
STORE_PATH  = OUT_DIR / "vectorstore.pkl"

COLLECTION_NAME = "mathutrice_rag"


# ══════════════════════════════════════════════════════════
# CONNEXION
# ══════════════════════════════════════════════════════════

def _get_connection_string() -> str:
    """Lit DATABASE_URL depuis .env ou variables d'environnement."""
    from dotenv import load_dotenv
    load_dotenv()

    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError(
            "DATABASE_URL non défini dans .env\n"
            "Exemple : DATABASE_URL=postgresql://postgres:password@localhost:5432/mathutrice"
        )
    return url


# ══════════════════════════════════════════════════════════
# EMBEDDINGS
# ══════════════════════════════════════════════════════════

def _get_langchain_embeddings():
    """Solon FR (sentence-transformers) — identique à ChromaDB."""
    try:
        from langchain_huggingface import HuggingFaceEmbeddings
        embeddings = HuggingFaceEmbeddings(
            model_name    = "OrdalieTech/Solon-embeddings-large-0.1",
            model_kwargs  = {"device": "cpu"},
            encode_kwargs = {"normalize_embeddings": True},
        )
        logger.info("Embeddings : Solon FR (OrdalieTech)")
        return embeddings
    except Exception as e:
        logger.warning(f"Solon FR indisponible ({e}) → MiniLM fallback")
        from langchain_huggingface import HuggingFaceEmbeddings
        return HuggingFaceEmbeddings(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )


# ══════════════════════════════════════════════════════════
# CONVERSION chunks ↔ Documents LangChain
# ══════════════════════════════════════════════════════════

def _chunks_to_documents(chunks: list[dict]):
    """Convertit nos chunks en Documents LangChain."""
    from langchain_core.documents import Document

    docs = []
    for c in chunks:
        m = c.get("metadata", {})
        # pgvector stocke les métadonnées en JSONB — pas de restriction de type
        flat_meta = {
            "chunk_id"      : str(c.get("id", "")),
            "notion"        : str(m.get("notion", "")),
            "niveau"        : str(m.get("niveau", "")),
            "doc_type"      : str(m.get("doc_type", "exercices")),
            "pdf_source"    : str(m.get("pdf_source", "")),
            "page"          : int(m.get("page", 1)),
            "difficulte"    : int(m.get("difficulte", 3)),
            "type_exercice" : str(m.get("type_exercice", "")),
            "concept"       : str(m.get("concept", "") or ""),
            "has_latex"     : bool(m.get("has_latex", False)),
            "char_count"    : int(m.get("char_count", len(c.get("text", "")))),
            "llm_enriched"  : bool(m.get("llm_enriched", False)),
            "prerequis"     : json.dumps(m.get("prerequis", []),
                                         ensure_ascii=False),
        }
        docs.append(Document(
            page_content = c.get("text", ""),
            metadata     = flat_meta,
        ))
    return docs


def _document_to_chunk(doc) -> dict:
    """Reconvertit un Document LangChain en chunk interne."""
    m = dict(doc.metadata)
    try:
        prereq = json.loads(m.get("prerequis", "[]"))
    except Exception:
        prereq = []
    return {
        "id"  : m.get("chunk_id", ""),
        "text": doc.page_content,
        "metadata": {**m, "prerequis": prereq},
    }


# ══════════════════════════════════════════════════════════
# CLASSE PRINCIPALE — PGVectorStore
# ══════════════════════════════════════════════════════════

class PGVectorStore:
    """
    BDD vectorielle pgvector avec LangChain.
    Interface identique à ChromaVectorStore — aucun autre
    fichier ne change.
    """

    def __init__(self, chroma_dir=None, collection_name: str = COLLECTION_NAME):
        # chroma_dir ignoré (compat signature) — pgvector utilise PostgreSQL
        self.collection_name = collection_name
        self._store          = None
        self._embeddings     = None

    def _init_store(self, embeddings=None):
        """Initialise ou charge le store pgvector."""
        from langchain_postgres.vectorstores import PGVector

        self._embeddings = embeddings or _get_langchain_embeddings()
        conn_str         = _get_connection_string()

        self._store = PGVector(
            embeddings      = self._embeddings,
            collection_name = self.collection_name,
            connection      = conn_str,
            use_jsonb       = True,   # métadonnées en JSONB → filtres SQL
        )
        return self._store

    # ── Propriété chunks ────────────────────────────────
    @property
    def chunks(self) -> list[dict]:
        """Retourne tous les chunks indexés."""
        if self._store is None:
            self._init_store()
        try:
            docs = self._store.similarity_search("", k=10000)
            return [_document_to_chunk(d) for d in docs]
        except Exception:
            return []

    def __len__(self) -> int:
        """Nombre de chunks indexés (requête directe PostgreSQL)."""
        if self._store is None:
            self._init_store()
        try:
            from psycopg2 import connect
            from urllib.parse import urlparse

            url  = urlparse(_get_connection_string())
            conn = connect(
                host     = url.hostname,
                port     = url.port or 5432,
                dbname   = url.path.lstrip("/"),
                user     = url.username,
                password = url.password,
            )
            cur = conn.cursor()
            cur.execute(
                "SELECT COUNT(*) FROM langchain_pg_embedding e "
                "JOIN langchain_pg_collection c ON e.collection_id = c.uuid "
                "WHERE c.name = %s",
                (self.collection_name,)
            )
            count = cur.fetchone()[0]
            conn.close()
            return count
        except Exception:
            return len(self.chunks)

    # ── Ajout ───────────────────────────────────────────
    def add(self, chunks: list[dict], embeddings=None):
        """Indexe les chunks dans pgvector."""
        import time
        if not chunks:
            return

        if self._store is None:
            self._init_store(embeddings)

        print(f"  pgvector : indexation de {len(chunks)} chunks...")
        t0   = time.perf_counter()
        docs = _chunks_to_documents(chunks)
        ids  = [str(c.get("id", f"chunk_{i}")) for i, c in enumerate(chunks)]

        self._store.add_documents(documents=docs, ids=ids)
        dt = time.perf_counter() - t0
        print(f"  pgvector : ✓ {len(chunks)} chunks indexés en {dt:.1f}s")

    # ── Recherche ────────────────────────────────────────
    def search(self, query: str, k: int = 5,
               filters: dict = None) -> list[dict]:
        """
        Recherche sémantique avec filtres sur les métadonnées.

        Filtres (syntaxe LangChain PGVector) :
            {"notion": "trigonométrie"}
            {"difficulte": {"$lte": 3}}
        """
        if self._store is None:
            self._init_store()

        try:
            kwargs = {"k": k}
            if filters:
                kwargs["filter"] = filters

            docs_scores = self._store.similarity_search_with_score(query, **kwargs)
            results = []
            for doc, score in docs_scores:
                chunk      = _document_to_chunk(doc)
                similarity = max(0.0, 1.0 - score)
                results.append({"chunk": chunk, "score": similarity})
            return results
        except Exception as e:
            logger.error(f"pgvector search error: {e}")
            return []

    def get_by_id(self, chunk_id: str) -> Optional[dict]:
        """Récupère un chunk par son ID."""
        if self._store is None:
            self._init_store()
        try:
            results = self._store.similarity_search(
                "", k=1, filter={"chunk_id": chunk_id}
            )
            if results:
                return _document_to_chunk(results[0])
        except Exception:
            pass
        return None

    # ── LangChain natif (MMR) ────────────────────────────
    def as_langchain_retriever(self, k: int = 4,
                                search_type: str = "mmr",
                                filters: dict = None):
        """Retourne un retriever LangChain natif (MMR pour la diversité)."""
        if self._store is None:
            self._init_store()

        search_kwargs = {"k": k}
        if filters:
            search_kwargs["filter"] = filters

        return self._store.as_retriever(
            search_type   = search_type,
            search_kwargs = search_kwargs,
        )

    # ── Sauvegarde / Chargement ──────────────────────────
    def save(self, path=None):
        """pgvector persiste automatiquement dans PostgreSQL."""
        logger.debug("pgvector : persistance automatique dans PostgreSQL")

    @classmethod
    def load(cls, path=None, chroma_dir=None,
             collection_name: str = COLLECTION_NAME) -> "PGVectorStore":
        """Charge le store pgvector existant."""
        store = cls(collection_name=collection_name)
        store._init_store()
        try:
            count = len(store)
            print(f"  pgvector chargé : {count} chunks "
                  f"(collection '{collection_name}')")
        except Exception as e:
            logger.warning(f"pgvector : erreur chargement — {e}")
        return store

    # ── Stats ────────────────────────────────────────────
    def stats(self) -> dict:
        """Stats pour le dashboard admin."""
        from collections import Counter
        chunks = self.chunks
        if not chunks:
            return {"status": "empty", "total_chunks": 0}

        return {
            "status"           : "ready",
            "total_chunks"     : len(chunks),
            "notions"          : list(set(
                c["metadata"].get("notion", "") for c in chunks
            )),
            "difficulte_dist"  : dict(Counter(
                c["metadata"].get("difficulte") for c in chunks
            )),
            "types"            : dict(Counter(
                c["metadata"].get("type_exercice", "") for c in chunks
            )),
            "llm_enriched_pct" : round(
                sum(1 for c in chunks if c["metadata"].get("llm_enriched"))
                / len(chunks) * 100
            ) if chunks else 0,
            "backend"          : "pgvector",
        }


# ══════════════════════════════════════════════════════════
# ALIASES — compatibilité avec le reste du code
# ══════════════════════════════════════════════════════════

# Tous les fichiers qui importaient ChromaVectorStore
# fonctionnent sans modification
ChromaVectorStore   = PGVectorStore
LocalVectorStore    = PGVectorStore
SemanticVectorStore = PGVectorStore


# ══════════════════════════════════════════════════════════
# FONCTION run() — point d'entrée step3
# ══════════════════════════════════════════════════════════

def run(chunks: list[dict]) -> PGVectorStore:
    """Indexe les chunks dans pgvector. Appelé par run_pipeline_batch.py."""
    store = PGVectorStore()
    store.add(chunks)
    return store


if __name__ == "__main__":
    print("pgvector store — test connexion")
    try:
        store = PGVectorStore.load()
        print(store.stats())
    except Exception as e:
        print(f"Erreur : {e}")
        print("Vérifier :")
        print("  - DATABASE_URL dans .env")
        print("  - CREATE EXTENSION vector; dans PostgreSQL")
        print("  - pip install pgvector psycopg2-binary langchain-postgres")
