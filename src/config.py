"""
config.py
─────────
Single source of truth for all configuration.

HOW IT WORKS:
  - Reads environment variables from the .env file
  - Every setting is typed and validated by Pydantic
  - All other modules import `settings` from here
  - Never call os.environ directly anywhere else

WHY THIS WAY:
  - If a required env var is missing, the app crashes at startup
    with a clear error message — not halfway through processing a claim
  - Settings are documented in one place
  - Easy to test with different configurations
"""

from functools import lru_cache
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AzureOpenAISettings(BaseSettings):
    """Azure OpenAI — the LLM provider."""
    endpoint: str         = Field(..., alias="AZURE_OPENAI_ENDPOINT")
    api_key: str          = Field(..., alias="AZURE_OPENAI_API_KEY")
    api_version: str      = Field("2024-08-01-preview", alias="AZURE_OPENAI_API_VERSION")
    deployment_pro: str   = Field("gpt-4o",            alias="AZURE_OPENAI_DEPLOYMENT_PRO")
    deployment_mini: str  = Field("gpt-4o-mini",       alias="AZURE_OPENAI_DEPLOYMENT_MINI")
    deployment_emb: str   = Field("text-embedding-3-small", alias="AZURE_OPENAI_DEPLOYMENT_EMBEDDING")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class CosmosSettings(BaseSettings):
    """Azure Cosmos DB — stores ClaimState documents."""
    endpoint: str   = Field(..., alias="COSMOS_ENDPOINT")
    key: str        = Field(..., alias="COSMOS_KEY")
    database: str   = Field("claims-db",    alias="COSMOS_DATABASE")
    container: str  = Field("claim-state",  alias="COSMOS_CONTAINER")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class BlobSettings(BaseSettings):
    """Azure Blob Storage — stores claim photos and PDFs."""
    connection_string: str = Field(..., alias="AZURE_STORAGE_CONNECTION_STRING")
    container: str         = Field("claim-documents", alias="AZURE_BLOB_CONTAINER")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class RedisSettings(BaseSettings):
    """Azure Cache for Redis — message queue between workers."""
    host: str     = Field(..., alias="REDIS_HOST")
    port: int     = Field(6380,  alias="REDIS_PORT")
    password: str = Field(...,   alias="REDIS_PASSWORD")
    ssl: bool     = Field(True,  alias="REDIS_SSL")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class ChromaSettings(BaseSettings):
    """ChromaDB — RAG vector store (open source, hosted on Container App)."""
    host: str              = Field("http://localhost", alias="CHROMA_HOST")
    port: int              = Field(8000,               alias="CHROMA_PORT")
    collection_fraud: str  = Field("fraud-patterns",  alias="CHROMA_COLLECTION_FRAUD")
    collection_policy: str = Field("policy-docs",     alias="CHROMA_COLLECTION_POLICY")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def base_url(self) -> str:
        return f"{self.host}:{self.port}"


class MCPSettings(BaseSettings):
    """URLs for the five MCP servers."""
    claims_url:    str = Field("http://localhost:8001", alias="MCP_CLAIMS_URL")
    policy_url:    str = Field("http://localhost:8002", alias="MCP_POLICY_URL")
    fraud_url:     str = Field("http://localhost:8003", alias="MCP_FRAUD_URL")
    workforce_url: str = Field("http://localhost:8004", alias="MCP_WORKFORCE_URL")
    slack_url:     str = Field("http://localhost:8005", alias="MCP_SLACK_URL")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class SlackSettings(BaseSettings):
    """Slack — for human-in-the-loop adjuster notifications."""
    bot_token:       str = Field("",                alias="SLACK_BOT_TOKEN")
    signing_secret:  str = Field("",                alias="SLACK_SIGNING_SECRET")
    review_channel:  str = Field("#claims-review",  alias="SLACK_REVIEW_CHANNEL")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class MLflowSettings(BaseSettings):
    """MLflow — experiment tracking and model registry."""
    tracking_uri: str  = Field("./data/mlruns",          alias="MLFLOW_TRACKING_URI")
    experiment:   str  = Field("claims-orchestrator-b",  alias="MLFLOW_EXPERIMENT_NAME")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class CrewSettings(BaseSettings):
    """Crew runtime limits."""
    max_iterations: int   = Field(15,   alias="CREW_MAX_ITERATIONS")
    timeout_seconds: int  = Field(300,  alias="CREW_TIMEOUT_SECONDS")
    max_cost_usd: float   = Field(2.00, alias="MAX_COST_PER_CLAIM_USD")
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


class Settings(BaseSettings):
    """Top-level settings. Import `settings` from this module everywhere."""
    environment: Literal["development", "staging", "production"] = Field(
        "development", alias="ENVIRONMENT"
    )
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    appinsights_connection_string: str = Field("", alias="APPLICATIONINSIGHTS_CONNECTION_STRING")

    azure_openai: AzureOpenAISettings = Field(default_factory=AzureOpenAISettings)
    cosmos:       CosmosSettings      = Field(default_factory=CosmosSettings)
    blob:         BlobSettings        = Field(default_factory=BlobSettings)
    redis:        RedisSettings       = Field(default_factory=RedisSettings)
    chroma:       ChromaSettings      = Field(default_factory=ChromaSettings)
    mcp:          MCPSettings         = Field(default_factory=MCPSettings)
    slack:        SlackSettings       = Field(default_factory=SlackSettings)
    mlflow:       MLflowSettings      = Field(default_factory=MLflowSettings)
    crew:         CrewSettings        = Field(default_factory=CrewSettings)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    """Returns a cached singleton. Called once at startup."""
    return Settings()


# This is what every other module imports:
#   from src.config import settings
settings = get_settings()
