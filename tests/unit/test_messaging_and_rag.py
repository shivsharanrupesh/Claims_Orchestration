"""
tests/unit/test_messaging_and_rag.py
─────────────────────────────────────
Unit tests for the Redis queue and ChromaDB RAG pipeline.
All external clients are mocked.
"""

from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest

from src.messaging.redis_queue import RedisQueue
from src.rag.chroma_rag import FraudRAGPipeline


# ── Redis Queue ────────────────────────────────────────────────────────────────

class TestRedisQueue:

    @patch("src.messaging.redis_queue.redis.Redis")
    def _make_queue(self, mock_redis_cls):
        mock_client = MagicMock()
        mock_redis_cls.return_value = mock_client
        return RedisQueue(), mock_client

    def test_push_calls_lpush(self):
        with patch("src.messaging.redis_queue.redis.Redis") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            q = RedisQueue()
            q.push("test-queue", {"claim_id": "CLM-001"})

            mock_client.lpush.assert_called_once()
            args = mock_client.lpush.call_args.args
            assert args[0] == "test-queue"
            assert "CLM-001" in args[1]

    def test_pop_calls_brpop(self):
        with patch("src.messaging.redis_queue.redis.Redis") as mock_cls:
            mock_client = MagicMock()
            mock_client.brpop.return_value = (
                "test-queue",
                '{"claim_id": "CLM-002"}'
            )
            mock_cls.return_value = mock_client

            q = RedisQueue()
            result = q.pop("test-queue", timeout=5)

            mock_client.brpop.assert_called_once_with("test-queue", timeout=5)
            assert result == {"claim_id": "CLM-002"}

    def test_pop_returns_none_on_timeout(self):
        with patch("src.messaging.redis_queue.redis.Redis") as mock_cls:
            mock_client = MagicMock()
            mock_client.brpop.return_value = None  # timeout
            mock_cls.return_value = mock_client

            q = RedisQueue()
            result = q.pop("test-queue", timeout=1)
            assert result is None

    def test_enqueue_intake_uses_correct_queue(self):
        with patch("src.messaging.redis_queue.redis.Redis") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            q = RedisQueue()
            q.enqueue_intake("CLM-003", {"policy_number": "POL-001"})

            args = mock_client.lpush.call_args.args
            assert args[0] == RedisQueue.INTAKE_QUEUE

    def test_enqueue_settlement_includes_approved_flag(self):
        with patch("src.messaging.redis_queue.redis.Redis") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client

            q = RedisQueue()
            q.enqueue_settlement("CLM-004", approved=True, adjuster_id="ADJ-001")

            args = mock_client.lpush.call_args.args
            import json
            payload = json.loads(args[1])
            assert payload["approved"] is True
            assert payload["adjuster_id"] == "ADJ-001"

    def test_ping_returns_false_when_redis_unreachable(self):
        with patch("src.messaging.redis_queue.redis.Redis") as mock_cls:
            mock_client = MagicMock()
            mock_client.ping.side_effect = Exception("Connection refused")
            mock_cls.return_value = mock_client

            q = RedisQueue()
            assert q.ping() is False


# ── ChromaDB RAG Pipeline ──────────────────────────────────────────────────────

class TestFraudRAGPipeline:

    def test_search_returns_results_list(self):
        """search() should always return a list even if ChromaDB is empty."""
        with patch("src.rag.chroma_rag.chromadb.HttpClient") as mock_chroma, \
             patch("src.rag.chroma_rag.AzureOpenAIEmbeddings") as mock_emb:

            mock_collection = MagicMock()
            mock_collection.query.return_value = {
                "documents": [["Third claim in 18 months, same repair shop."]],
                "metadatas": [[{"id": "FRAUD-001", "fraud_type": "provider_ring",
                                "outcome": "denied"}]],
                "distances": [[0.13]],
            }
            mock_collection.count.return_value = 5
            mock_chroma.return_value.get_or_create_collection.return_value = mock_collection
            mock_emb.return_value.embed_query.return_value = [0.1] * 1536

            rag = FraudRAGPipeline()
            results = rag.search("third auto claim same body shop", top_k=1)

            assert isinstance(results, list)
            assert len(results) == 1
            assert results[0]["fraud_type"] == "provider_ring"
            assert results[0]["similarity"] == pytest.approx(0.87, abs=0.01)

    def test_search_returns_empty_list_on_error(self):
        """search() returns [] gracefully when ChromaDB is unreachable."""
        with patch("src.rag.chroma_rag.chromadb.HttpClient") as mock_chroma, \
             patch("src.rag.chroma_rag.AzureOpenAIEmbeddings") as mock_emb:

            mock_chroma.return_value.get_or_create_collection.side_effect = \
                Exception("ChromaDB unreachable")

            rag = FraudRAGPipeline()
            results = rag.search("test query")

            assert results == []

    def test_ingest_calls_collection_add(self):
        """ingest_fraud_case() calls ChromaDB collection.add."""
        with patch("src.rag.chroma_rag.chromadb.HttpClient") as mock_chroma, \
             patch("src.rag.chroma_rag.AzureOpenAIEmbeddings") as mock_emb:

            mock_collection = MagicMock()
            mock_chroma.return_value.get_or_create_collection.return_value = mock_collection
            mock_emb.return_value.embed_documents.return_value = [[0.1] * 1536]

            rag = FraudRAGPipeline()
            rag.ingest_fraud_case(
                case_id="FRAUD-TEST-001",
                text="Test fraud pattern description.",
                fraud_type="staged_collision",
                outcome="denied",
            )

            mock_collection.add.assert_called_once()
            call_args = mock_collection.add.call_args.kwargs
            assert "FRAUD-TEST-001" in call_args["ids"]

    def test_count_returns_integer(self):
        """count() returns the number of indexed documents."""
        with patch("src.rag.chroma_rag.chromadb.HttpClient") as mock_chroma, \
             patch("src.rag.chroma_rag.AzureOpenAIEmbeddings"):

            mock_collection = MagicMock()
            mock_collection.count.return_value = 42
            mock_chroma.return_value.get_or_create_collection.return_value = mock_collection

            rag = FraudRAGPipeline()
            assert rag.count() == 42
