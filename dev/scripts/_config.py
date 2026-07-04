"""Shared configuration constants for slwp dev scripts."""

import os
from pathlib import Path

# Project root
PROJECT_ROOT = Path(__file__).parent.parent.parent.resolve()

# Cloudflare — read from environment (populated by .env via setup_gh_secrets.py)
CF_ACCOUNT_ID = os.getenv("CF_ACCOUNT_ID", "")
CF_ZONE_ID = os.getenv("CF_ZONE_ID", "")
CF_TUNNEL_ID = os.getenv("CF_TUNNEL_ID", "d1e96e86-a44a-457a-a60c-e7d5d5d675bd")

# Resend defaults
RESEND_FROM_DOMAIN = os.getenv("RESEND_FROM_DOMAIN", "email.mrme.tech")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "noreply@email.mrme.tech")

# GitHub repo for Actions deployment
GH_REPO = os.getenv("GH_REPO", "mrme000m/ssfx-appwrite")
GH_DEFAULT_BRANCH = "develop"

# Function -> custom domain mappings (native Appwrite Proxy Rules)
FUNCTION_DOMAINS = {
    "auth-oauth":   "auth.mrme.tech",
    "auth-pin":     "auth.mrme.tech",
    "api-internal": "internal.mrme.tech",
    "token-refresh": "refresh.mrme.tech",
}

# Site -> custom domain mappings
SITE_DOMAINS = {
    "ssfx-hq": "app.mrme.tech",
}

# Zone root domain
ZONE_DOMAIN = "mrme.tech"


def load_env():
    """Load .env file into os.environ if not already loaded."""
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        return
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())
