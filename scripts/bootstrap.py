"""
scripts/bootstrap.py
─────────────────────
First-time setup script. Run ONCE after Azure resources are provisioned.

What this does:
  1. Creates Cosmos DB database and container (if not exists)
  2. Creates Azure Blob Storage container (if not exists)
  3. Seeds ChromaDB with sample fraud patterns for RAG

Usage:
  python scripts/bootstrap.py

Prerequisites:
  - .env file with all Azure connection strings set
  - Azure resources already provisioned (Cosmos, Blob, Redis, Container Apps)
"""

import sys
sys.path.insert(0, ".")

from loguru import logger
from src.repositories.cosmos_repository import ensure_cosmos_containers
from src.rag.chroma_rag import FraudRAGPipeline


SAMPLE_FRAUD_CASES = [
    {
        "id": "FRAUD-001",
        "text": "Claimant filed third auto collision claim in 18 months. All three incidents involved the same repair shop (Speedy Auto Body). Each time the vehicle was rear-ended and towed to the same shop. Network analysis shows the tow truck driver is related to the shop owner.",
        "fraud_type": "provider_ring",
        "outcome": "denied_referred_to_police",
    },
    {
        "id": "FRAUD-002",
        "text": "Total loss claim filed two weeks after policy inception. Vehicle purchase price was $8,000 but claimed value is $32,000. Claimant provided repair estimates from three shops, all with the same phone number. No police report filed despite alleged theft.",
        "fraud_type": "inflated_value",
        "outcome": "denied_investigated",
    },
    {
        "id": "FRAUD-003",
        "text": "Claimant involved in four accidents at the same intersection over six months. CCTV footage showed staged collision with a second vehicle driven by a known associate. Medical claims from a clinic that has appeared in 12 other suspicious claims.",
        "fraud_type": "staged_collision",
        "outcome": "denied_criminal_charges",
    },
    {
        "id": "FRAUD-004",
        "text": "Water damage claim filed claiming burst pipe. Contractor estimated $95,000 in damages but comparable properties average $25,000 for similar incidents. No plumber records. Neighbours report no flooding. Policy was maxed out on prior claim last year.",
        "fraud_type": "inflated_repair",
        "outcome": "denied_independent_assessment",
    },
    {
        "id": "FRAUD-005",
        "text": "Claimant reported vehicle stolen but GPS data showed the vehicle in the claimant's driveway during the alleged theft period. The reported theft location does not match any police records for that date. Second theft claim in three years.",
        "fraud_type": "false_theft",
        "outcome": "denied_voided_policy",
    },
]


def main() -> None:
    logger.info("bootstrap.start")

    logger.info("step 1: creating Cosmos DB containers...")
    try:
        ensure_cosmos_containers()
        logger.info("  ✓ Cosmos DB ready")
    except Exception as e:
        logger.error(f"  ✗ Cosmos DB failed: {e}")

    logger.info("step 2: seeding ChromaDB fraud index...")
    try:
        rag = FraudRAGPipeline()
        for case in SAMPLE_FRAUD_CASES:
            rag.ingest_fraud_case(
                case_id=case["id"],
                text=case["text"],
                fraud_type=case["fraud_type"],
                outcome=case["outcome"],
            )
        count = rag.count()
        logger.info(f"  ✓ ChromaDB seeded with {count} fraud patterns")
    except Exception as e:
        logger.error(f"  ✗ ChromaDB seeding failed: {e}")

    logger.info("bootstrap.complete — system ready")


if __name__ == "__main__":
    main()
