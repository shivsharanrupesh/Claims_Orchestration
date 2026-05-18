"""
rag/chroma_rag.py
──────────────────
ChromaDB RAG pipeline — the open-source alternative to Azure AI Search.

WHY CHROMADB IN OPTION B:
  ✓ Open source — pip install chromadb, no Azure account needed for setup
  ✓ Same vector search capability as Azure AI Search for this use case
  ✓ HTTP client mode — points to the ChromaDB Container App in Azure
  ✓ Saves $200-500/month vs Azure AI Search Standard tier
  ✓ Built-in OpenAI embedding integration

TWO COLLECTIONS:
  fraud-patterns   → historical fraud cases for Fraud Specialist RAG
  policy-docs      → policy wording for Validation Specialist

HOW THE RAG PIPELINE WORKS:
  1. INGESTION (run once / nightly):
     - Load PDF/text documents from Azure Blob Storage
     - Split into ~500-token chunks using LangChain text splitter
     - Embed each chunk using Azure OpenAI text-embedding-3-small
     - Store vectors + metadata in ChromaDB

  2. RETRIEVAL (per claim, per agent query):
     - Fraud Specialist sends a query describing the incident
     - We embed the query using the same embedding model
     - ChromaDB finds the top-K most similar historical fraud patterns
     - Agent receives the matching text + similarity scores as context

WHY THIS IS BETTER THAN JUST SENDING EVERYTHING TO THE LLM:
  - LLM context window is limited (~128K tokens)
  - We might have 50,000 historical fraud cases
  - RAG retrieves only the 5-10 most relevant cases
  - This makes the Fraud Specialist's reasoning much more grounded
"""

from __future__ import annotations
from typing import Any, Optional
from loguru import logger
import chromadb
from chromadb.config import Settings as ChromaInternalSettings
from langchain_openai import AzureOpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_core.documents import Document

from src.config import settings


def _get_embeddings() -> AzureOpenAIEmbeddings:
    """
    Returns the Azure OpenAI embedding model.
    Uses text-embedding-3-small (1536 dims, cheap, fast).
    """
    return AzureOpenAIEmbeddings(
        azure_endpoint=settings.azure_openai.endpoint,
        api_key=settings.azure_openai.api_key,
        api_version=settings.azure_openai.api_version,
        azure_deployment=settings.azure_openai.deployment_emb,
    )


def _get_chroma_client() -> chromadb.HttpClient:
    """
    Connect to the ChromaDB Container App via HTTP.
    For local development: runs ChromaDB locally with chroma run
    For production: points to the Container App URL
    """
    return chromadb.HttpClient(
        host=settings.chroma.host,
        port=settings.chroma.port,
        settings=ChromaInternalSettings(anonymized_telemetry=False),
    )


class FraudRAGPipeline:
    """
    RAG pipeline for the Fraud Specialist agent.

    INGESTION SIDE: indexes historical fraud cases into ChromaDB
    RETRIEVAL SIDE: given an incident description, returns similar fraud cases
    """

    def __init__(self) -> None:
        self._client = _get_chroma_client()
        self._embeddings = _get_embeddings()
        self._collection_name = settings.chroma.collection_fraud

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """
        Search for similar fraud patterns in ChromaDB.

        Called by the Fraud MCP server when the Fraud Specialist queries it.
        Returns top_k most similar historical fraud cases with metadata.

        Args:
            query: Natural language description of the incident
                   e.g. "third auto collision claim in 18 months, same body shop"
            top_k: How many similar cases to return (default 5)

        Returns:
            List of dicts with 'id', 'text', 'metadata', 'similarity'
        """
        try:
            collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )

            # Embed the query using Azure OpenAI
            query_embedding = self._embeddings.embed_query(query)

            # Search ChromaDB
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, collection.count() or 1),
                include=["documents", "metadatas", "distances"],
            )

            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            return [
                {
                    "id": metas[i].get("id", str(i)),
                    "text": docs[i],
                    "fraud_type": metas[i].get("fraud_type", "unknown"),
                    "outcome": metas[i].get("outcome", "unknown"),
                    "similarity": round(1 - distances[i], 4),  # cosine distance → similarity
                }
                for i in range(len(docs))
            ]
        except Exception as e:
            logger.error(f"chroma.search.error | collection={self._collection_name} error={e}")
            return []

    def ingest_fraud_case(self, case_id: str, text: str,
                          fraud_type: str, outcome: str) -> None:
        """
        Add a single historical fraud case to the index.
        Called from scripts/seed_fraud_index.py during setup.

        Args:
            case_id:    Unique identifier (e.g. "FRAUD-2023-001")
            text:       Description of the fraud pattern
            fraud_type: Category (e.g. "staged_collision", "inflated_repair")
            outcome:    What happened (e.g. "denied", "referred_to_police")
        """
        collection = self._client.get_or_create_collection(
            name=self._collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        embedding = self._embeddings.embed_documents([text])[0]
        collection.add(
            ids=[case_id],
            documents=[text],
            embeddings=[embedding],
            metadatas=[{"id": case_id, "fraud_type": fraud_type, "outcome": outcome}],
        )
        logger.info(f"chroma.ingest | collection={self._collection_name} id={case_id}")

    def ingest_documents_from_blob(self, texts: list[str], metadatas: list[dict]) -> None:
        """
        Batch ingest multiple documents.
        Used when importing historical claims from Azure Blob Storage.
        """
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=500, chunk_overlap=50
        )
        collection = self._client.get_or_create_collection(self._collection_name)

        all_chunks = []
        all_metas = []
        for text, meta in zip(texts, metadatas):
            chunks = splitter.split_text(text)
            all_chunks.extend(chunks)
            all_metas.extend([meta] * len(chunks))

        embeddings = self._embeddings.embed_documents(all_chunks)
        ids = [f"chunk-{i}" for i in range(len(all_chunks))]
        collection.add(
            ids=ids, documents=all_chunks,
            embeddings=embeddings, metadatas=all_metas,
        )
        logger.info(f"chroma.batch_ingest | count={len(all_chunks)}")

    def count(self) -> int:
        """How many vectors are in the fraud index."""
        try:
            c = self._client.get_or_create_collection(self._collection_name)
            return c.count()
        except Exception:
            return 0


class PolicyRAGPipeline:
    """
    RAG pipeline for policy documents.
    Used by the Validation Specialist to look up coverage clauses.
    Smaller collection — typically 200-500 policy document chunks.
    """

    def __init__(self) -> None:
        self._client = _get_chroma_client()
        self._embeddings = _get_embeddings()
        self._collection_name = settings.chroma.collection_policy

    def search(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Find relevant policy clauses for a given damage type and incident."""
        try:
            collection = self._client.get_or_create_collection(self._collection_name)
            query_embedding = self._embeddings.embed_query(query)
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, collection.count() or 1),
                include=["documents", "metadatas", "distances"],
            )
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]
            return [
                {"clause_id": metas[i].get("clause_id", ""),
                 "text": docs[i],
                 "similarity": round(1 - distances[i], 4)}
                for i in range(len(docs))
            ]
        except Exception as e:
            logger.error(f"policy_rag.search.error | {e}")
            return []
