"""Configuration loaded from environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
ANTHROPIC_EXTRACTOR_MODEL = os.getenv(
    "ANTHROPIC_EXTRACTOR_MODEL",
    "claude-haiku-4-5-20251001",  # 5x cheaper than Sonnet
)

BFO_PATH = ROOT / os.getenv("BFO_PATH", "ontology/bfo.owl")
WORKING_PATH = ROOT / os.getenv("WORKING_PATH", "ontology/working.owl")
SEED_PATH = ROOT / os.getenv("SEED_PATH", "ontology/seed/legal_seed.ttl")
SESSIONS_DIR = ROOT / os.getenv("SESSIONS_DIR", "sessions")

FLASK_HOST = os.getenv("FLASK_HOST", "127.0.0.1")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

ENABLE_GIT_COMMITS = os.getenv("ENABLE_GIT_COMMITS", "true").lower() == "true"

# Namespace for newly minted individuals
WORKING_NS = "http://davidkoepsell.com/bfo-agent/working#"

SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


def require_api_key():
    if not ANTHROPIC_API_KEY or ANTHROPIC_API_KEY.startswith("sk-ant-..."):
        raise RuntimeError(
            "ANTHROPIC_API_KEY not set. Copy .env.example to .env and fill it in."
        )
