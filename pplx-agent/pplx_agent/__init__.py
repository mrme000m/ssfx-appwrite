"""PPLX Agent - Perplexity-powered gold market intelligence harness.

Exposes the high-level GoldMarketAgent used by scripts, the API server, and the
agent_harness integration. Configuration is read from Appwrite ``service_config``
when available, falling back to the local ``.env`` file for development.
"""

from __future__ import annotations

__version__ = "1.0.0"

from .config import PplxAgentSettings, get_settings
from .gold_market_agent import GoldMarketAgent

__all__ = ["PplxAgentSettings", "get_settings", "GoldMarketAgent", "__version__"]
