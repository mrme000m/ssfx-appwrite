"""cTrader client import helper — simplified since all packages are sibling packages.

In the unified container layout, ctrader_client is always available as a sibling
package at /app/ctrader_client/. This module provides the setup function for
backward compatibility but is now a no-op import wrapper.
"""

import logging

logger = logging.getLogger(__name__)


def setup_ctrader_import_path() -> None:
    """Set up cTrader client import.

    In the unified container, ctrader_client is always available as a
    sibling package. This function is retained for backward compatibility
    but is effectively a no-op.
    """
    try:
        import ctrader_client  # noqa: F401
        logger.debug("ctrader_client available as sibling package")
    except ImportError:
        logger.error(
            "ctrader_client not found — this should not happen in the unified layout. "
            "Check that /app/ctrader_client/ is present and PYTHONPATH includes /app."
        )
