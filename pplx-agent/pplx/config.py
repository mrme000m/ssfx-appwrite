"""PPLX configuration utilities."""

import os
from pathlib import Path


def load_project_env():
    """Load .env from project root if present."""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists() and "PPLX_ENV_LOADED" not in os.environ:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_path)
        os.environ["PPLX_ENV_LOADED"] = "1"
