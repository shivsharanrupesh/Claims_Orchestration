"""
repositories/blob_repository.py
────────────────────────────────
Azure Blob Storage — stores claim photos, PDFs, voice transcripts.

WHY BLOB IN OPTION B:
  ✓ Claim photos can be 5-20MB — Cosmos DB only stores up to 2MB per doc
  ✓ Blob is cheap ($0.018/GB/month vs Cosmos transaction costs)
  ✓ Direct SAS URLs — agents download files without going through our API
  ✓ Lifecycle policies — auto-archive settled claim docs after 90 days
  ✓ Geo-redundant — files survive datacenter failure

FLOW:
  1. Claimant submits photos via the FNOL API
  2. API uploads to Blob, gets back a URL
  3. URL stored in FNOLPayload.photo_urls
  4. Document Specialist downloads from that URL via ChromaDB Blob loader
"""

from __future__ import annotations
import io
from pathlib import Path
from typing import Optional
from loguru import logger
from azure.storage.blob import BlobServiceClient, generate_blob_sas, BlobSasPermissions
from datetime import datetime, timedelta, timezone

from src.config import settings


class BlobRepository:
    """
    Upload, download, and manage claim documents in Azure Blob Storage.
    """

    def __init__(self) -> None:
        self._client = BlobServiceClient.from_connection_string(
            settings.blob.connection_string
        )
        self._container = settings.blob.container

    def upload_file(self, file_content: bytes, blob_name: str,
                    content_type: str = "application/octet-stream") -> str:
        """
        Upload a file and return its URL.

        blob_name should be: {claim_id}/{filename}
        e.g. "CLM-001/photo_rear_damage.jpg"
        Returns the full https:// URL.
        """
        container_client = self._client.get_container_client(self._container)
        blob_client = container_client.get_blob_client(blob_name)
        blob_client.upload_blob(
            data=file_content,
            content_settings={"content_type": content_type},
            overwrite=True,
        )
        url = blob_client.url
        logger.info(f"blob.upload | name={blob_name} size={len(file_content)}")
        return url

    def download_file(self, blob_name: str) -> bytes:
        """Download a blob and return its bytes."""
        container_client = self._client.get_container_client(self._container)
        blob_client = container_client.get_blob_client(blob_name)
        data = blob_client.download_blob().readall()
        logger.info(f"blob.download | name={blob_name} size={len(data)}")
        return data

    def get_sas_url(self, blob_name: str, expiry_hours: int = 1) -> str:
        """
        Generate a short-lived SAS URL for a blob.
        Used when passing photo URLs to the Document Specialist:
        the agent downloads directly from Azure, not through our API.

        SAS URLs expire after expiry_hours (default 1 hour).
        """
        account_name = self._client.account_name
        account_key = self._client.credential.account_key
        sas_token = generate_blob_sas(
            account_name=account_name,
            container_name=self._container,
            blob_name=blob_name,
            account_key=account_key,
            permission=BlobSasPermissions(read=True),
            expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
        )
        return f"https://{account_name}.blob.core.windows.net/{self._container}/{blob_name}?{sas_token}"

    def list_claim_docs(self, claim_id: str) -> list[str]:
        """List all documents for a specific claim."""
        container_client = self._client.get_container_client(self._container)
        blobs = container_client.list_blobs(name_starts_with=f"{claim_id}/")
        return [b.name for b in blobs]

    def delete_claim_docs(self, claim_id: str) -> int:
        """
        Delete all documents for a claim.
        Called for PIPEDA right-to-erasure requests.
        Returns the count of deleted blobs.
        """
        container_client = self._client.get_container_client(self._container)
        blobs = list(container_client.list_blobs(name_starts_with=f"{claim_id}/"))
        for blob in blobs:
            container_client.delete_blob(blob.name)
        logger.info(f"blob.delete | claim={claim_id} count={len(blobs)}")
        return len(blobs)
