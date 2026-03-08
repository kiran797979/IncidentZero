"""
Configuration loaded from environment variables.
Supports both Azure OpenAI and direct OpenAI as fallback.
"""

import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()


class Config:
    # Azure OpenAI
    AZURE_OPENAI_ENDPOINT: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    AZURE_OPENAI_KEY: str = os.getenv("AZURE_OPENAI_KEY", "")
    AZURE_OPENAI_DEPLOYMENT: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-06")

    # Fallback: Direct OpenAI
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    USE_AZURE: bool = os.getenv("USE_AZURE", "true").lower() == "true"

    # Target App
    TARGET_APP_URL: str = os.getenv("TARGET_APP_URL", "http://localhost:8000")

    # GitHub
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GITHUB_REPO_OWNER: str = os.getenv("GITHUB_REPO_OWNER", "")
    GITHUB_REPO_NAME: str = os.getenv("GITHUB_REPO_NAME", "incidentzero")

    # Monitoring
    POLLING_INTERVAL_SECONDS: int = int(os.getenv("POLLING_INTERVAL_SECONDS", "5"))


config = Config()