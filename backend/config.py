"""
Configuration loaded from environment variables.
Supports both Azure OpenAI and direct OpenAI as fallback.
"""

import os
from dotenv import load_dotenv

# Load .env file (no-op in production if .env doesn't exist)
load_dotenv()


class Config:
    """Central configuration — reads from environment variables with safe defaults."""

    # ─── Environment ──────────────────────────────────────
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")

    # ─── Azure OpenAI ─────────────────────────────────────
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_KEY: str = os.getenv("AZURE_OPENAI_KEY", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-06")

    # ─── Fallback: Direct OpenAI ──────────────────────────
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # ─── Auto-detect which AI provider to use ─────────────
    # Explicit override via env var, otherwise auto-detect from keys
    @staticmethod
    def _resolve_use_azure() -> bool:
        explicit = os.getenv("USE_AZURE", "").lower()
        if explicit in ("true", "1", "yes"):
            return True
        if explicit in ("false", "0", "no"):
            return False
        # Auto-detect: use Azure if endpoint and key are both set
        return bool(
            os.getenv("AZURE_OPENAI_ENDPOINT", "")
            and os.getenv("AZURE_OPENAI_KEY", "")
        )

    USE_AZURE: bool = _resolve_use_azure.__func__()

    # ─── Target App ───────────────────────────────────────
    TARGET_APP_URL: str = os.getenv("TARGET_APP_URL", "http://localhost:8000")

    # ─── Server ───────────────────────────────────────────
    PORT: int = int(os.getenv("PORT", "8080"))
    CORS_ORIGINS: list = [
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS",
            "http://localhost:3000,http://localhost:3001,https://incidentzero-frontend.azurewebsites.net",
        ).split(",")
        if origin.strip()
    ]

    # ─── GitHub ───────────────────────────────────────────
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")

    # Supports both formats:
    #   GITHUB_REPO="username/incidentzero"  (single var)
    #   GITHUB_REPO_OWNER="username" + GITHUB_REPO_NAME="incidentzero"  (split)
    @staticmethod
    def _resolve_github_owner() -> str:
        full_repo = os.getenv("GITHUB_REPO", "")
        if "/" in full_repo:
            return full_repo.split("/")[0]
        return os.getenv("GITHUB_REPO_OWNER", "")

    @staticmethod
    def _resolve_github_name() -> str:
        full_repo = os.getenv("GITHUB_REPO", "")
        if "/" in full_repo:
            return full_repo.split("/")[1]
        return os.getenv("GITHUB_REPO_NAME", "incidentzero")

    GITHUB_REPO: str = os.getenv("GITHUB_REPO", "")
    GITHUB_REPO_OWNER: str = _resolve_github_owner.__func__()
    GITHUB_REPO_NAME: str = _resolve_github_name.__func__()

    # ─── Monitoring ───────────────────────────────────────
    POLLING_INTERVAL_SECONDS: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "5"))

    # ─── Convenience Properties ───────────────────────────
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def has_azure_openai(self) -> bool:
        return bool(self.AZURE_OPENAI_ENDPOINT and self.AZURE_OPENAI_KEY)

    @property
    def has_openai(self) -> bool:
        return bool(self.OPENAI_API_KEY)

    @property
    def has_github(self) -> bool:
        return bool(self.GITHUB_TOKEN and self.GITHUB_REPO_OWNER)

    @property
    def ai_provider(self) -> str:
        if self.USE_AZURE and self.has_azure_openai:
            return "azure_openai"
        if self.has_openai:
            return "openai"
        return "mock"

    def summary(self) -> dict:
        return {
            "environment": self.ENVIRONMENT,
            "ai_provider": self.ai_provider,
            "azure_openai_configured": self.has_azure_openai,
            "openai_configured": self.has_openai,
            "github_configured": self.has_github,
            "target_app_url": self.TARGET_APP_URL,
            "port": self.PORT,
            "polling_interval": self.POLLING_INTERVAL_SECONDS,
            "github_repo": self.GITHUB_REPO_OWNER + "/" + self.GITHUB_REPO_NAME if self.GITHUB_REPO_OWNER else "not configured",
        }


config = Config()