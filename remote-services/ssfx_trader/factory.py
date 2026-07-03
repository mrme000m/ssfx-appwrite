"""Factory for building followers and executors from configuration."""
from __future__ import annotations

import typing

from pymongo import MongoClient

from ssfx_parser import AgentConfig, ChainedParser, LlmSignalParser, RegexSignalParser

from .backends.ctrader import CTraderBackend
from .backends.simulated import SimulatedBackend
from .config import AccountConfig
from .executor import TradeExecutor
from .follower import AccountFollower
from .market_context import DataServiceClient
from .risk_monitor import RiskLimits, RiskMonitor
from .stores.base import AccountStore
from .stores.mongo_store import MongoAccountStore, MongoSignalStore
from .symbol_resolver import SymbolResolver
from .volume_resolver import VolumeResolver

if typing.TYPE_CHECKING:
    from market_data_service.signal_experience.updater import SignalExperienceUpdater


def create_parser(agent_config: AgentConfig | None = None) -> ChainedParser:
    """Create the default LLM → regex chained parser."""
    # Check if we have a valid API key for LLM parsing
    default_config = AgentConfig(
        base_url="https://openrouter.ai/api/v1",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        temperature=0.1,
        max_tokens=1000,
        timeout_seconds=30,
        min_confidence=0.75,
        api_key="",
    )
    config = agent_config or default_config
    api_key = getattr(config, 'api_key', '').strip()

    if not api_key:
        # No API key configured - log warning and use regex parser only
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            "No LLM API key configured for signal parsing. "
            "Using regex parser only. Configure LLM_API_KEY to enable LLM parsing."
        )
        # Return regex parser only (no LLM parsing)
        return ChainedParser(primary=RegexSignalParser(), fallback=RegexSignalParser())

    # API key is available - create LLM parser
    primary = LlmSignalParser(config)
    fallback = RegexSignalParser()
    return ChainedParser(primary=primary, fallback=fallback)


def _create_backend(account_config: AccountConfig):
    """Select execution backend based on account credentials."""
    if account_config.has_grant_id:
        logger = __import__("logging").getLogger(__name__)
        logger.info(
            "Using real CTraderBackend for account %s (%s)",
            account_config.name,
            account_config.ctrader.host_type,
        )
        return CTraderBackend(account_config.name, account_config.ctrader)
    return SimulatedBackend(account_config.name)


def create_follower(
    account_config: AccountConfig,
    mongo_uri: str,
    mongo_database: str,
    *,
    client: MongoClient | None = None,
    signal_store: MongoSignalStore | None = None,
    account_store: AccountStore | None = None,
    data_service_base_url: str | None = None,
    data_service_api_key: str | None = None,
    data_service_client: DataServiceClient | None = None,
    experience_updater: "SignalExperienceUpdater | None" = None,
    autonomy_enabled: bool = False,
) -> AccountFollower:
    """Build an AccountFollower with the appropriate execution backend."""
    signal_store = signal_store or MongoSignalStore(mongo_uri, mongo_database, client=client)
    account_store = account_store or MongoAccountStore(mongo_uri, mongo_database, account_config.name, client=client)

    backend = _create_backend(account_config)
    resolver = SymbolResolver()
    volume_resolver = VolumeResolver(account_config.trading, backend, resolver)
    market_context_client = data_service_client or (
        DataServiceClient(data_service_base_url, data_service_api_key)
        if data_service_base_url
        else None
    )
    risk_monitor = RiskMonitor(
        account_name=account_config.name,
        limits=RiskLimits(
            max_daily_loss_pct=account_config.trading.max_daily_loss_pct,
            max_drawdown_pct=account_config.trading.max_drawdown_pct,
            max_open_risk_pct=account_config.trading.max_open_risk_pct,
            panic_stop=account_config.trading.panic_stop,
            risk_reset_utc_hour=account_config.trading.risk_reset_utc_hour,
        ),
        store=account_store,
    )

    executor = TradeExecutor(
        follower_id=account_config.name,
        backend=backend,
        resolver=resolver,
        signal_store=signal_store,
        account_store=account_store,
        trading=account_config.trading,
        volume_resolver=volume_resolver,
        market_context_client=market_context_client,
        experience_updater=experience_updater,
        risk_monitor=risk_monitor,
    )

    def config_provider() -> AccountConfig:
        doc = account_store.get_account(account_config.name)
        if doc:
            return AccountConfig.from_mongo(doc)
        return account_config

    return AccountFollower(
        config=account_config,
        signal_store=signal_store,
        account_store=account_store,
        executor=executor,
        config_provider=config_provider,
        autonomy_enabled=autonomy_enabled,
    )
