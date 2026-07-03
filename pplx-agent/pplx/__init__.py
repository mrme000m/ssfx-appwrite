"""PPLX — Perplexity AI Web Client."""

from .client import PerplexityClient, Client
from .bw_cookies import load_cookies
from .config import load_project_env

__all__ = ["PerplexityClient", "Client", "load_cookies", "load_project_env"]

__version__ = "0.1.0"
