# Account Hub v2 + Data Service Architecture

This document describes the Appwrite-native cTrader runtime architecture in `remote-services/`. It covers the persistent account hub, the one-transport-per-environment connection model, real-time account-event persistence, and the market-data service integration.

## Table of Contents

- [High-level goals](#high-level-goals)
- [Connection model: one transport per environment](#connection-model-one-transport-per-environment)
- [Account discovery](#account-discovery)
- [Account Hub v2](#account-hub-v2)
- [Account events persistence](#account-events-persistence)
- [WebSocket fan-out](#websocket-fan-out)
- [Market data service integration](#market-data-service-integration)
- [Configuration](#configuration)
- [Migration notes](#migration-notes)
- [Key files](#key-files)

## High-level goals

1. **Authenticate through Appwrite Functions** rather than holding decrypted cTrader tokens in Python. Tokens are AES-GCM-256 encrypted in Appwrite TablesDB; Python calls `ctrader-internal` to refresh short-lived access tokens.
2. **One cTrader transport per environment** (live + demo) shared across all slave accounts, following cTrader Open API best practice.
3. **Discover accounts dynamically** from Appwrite `slave_accounts` and `accounts` tables.
4. **Persist real-time account state/events** (execution, trader, margin, positions, orders) into Appwrite TablesDB (`account_events`).
5. **Run the market-data service against Appwrite-native auth** with **InfluxDB Cloud Serverless** as the default time-series store and SQLite as an explicit fallback.

## Connection model: one transport per environment

`remote-services/ctrader/env_connection.py` defines `EnvironmentConnection`.

- Exactly one transport is maintained per environment (`is_live=True/False`).
- Application auth (`ProtoOAApplicationAuthReq`) happens once per transport.
- Multiple accounts are authorized on the same transport via `ProtoOAAccountAuthReq`.
- Reconnect logic re-auths the application, re-authorizes every known account, and replays market-data subscriptions.
- Token refresh is centralized through `AppwriteMultiTokenClient` with per-grant caching and `asyncio.Lock` to avoid refresh thundering herds.
- Events are published to a typed `AsyncEventBus` so consumers (persister, WebSocket fan-out) can subscribe without tight coupling.

```python
from ctrader.env_connection import EnvironmentConnection

env = EnvironmentConnection(
    is_live=False,
    client_id="<cTrader client id>",
    client_secret="<cTrader client secret>",
    internal_url="https://internal.mrme.tech",
    internal_api_key="<x-internal-key>",
    transport_type="tcp",  # or "ws"
)
await env.start()
await env.authorize_account(grant_id="<grant_id>", ctid_trader_account_id=12345678)
```

## Account discovery

`remote-services/ctrader/account_discovery.py` defines `AccountDiscovery`.

- Polls `slave_accounts` with `status=active`.
- For each active `grant_id`, reads the `accounts` table to obtain `ctidTraderAccountId` and `isLive`.
- Falls back to `slave_accounts.ctrader_account_ids` only when the `accounts` table has no rows for the grant.
- Emits `AccountRef` objects keyed by `(grant_id, ctid_trader_account_id, is_live)`.

## Account Hub v2

`remote-services/ctrader/account_hub_v2.py` defines `AccountHubV2`.

- Orchestrates `AccountDiscovery` + two `EnvironmentConnection`s (live + demo).
- On each poll diff, authorizes new accounts and removes dropped accounts.
- Maintains hot `AccountSnapshot` state per account (balance, equity, margin, positions, orders).
- Fans out normalized events to WebSocket subscribers via `AccountWebSocketServer`.
- Persists events to Appwrite via `AccountEventsPersister`.
- Activated when `config.account_hub_environment_mode` is true in `ctrader/config.py`.

```python
from appwrite.client import Client
from ctrader.account_hub_v2 import AccountHubV2

client = (
    Client()
    .set_endpoint("https://sgp.cloud.appwrite.io/v1")
    .set_project("<project_id>")
    .set_key("<api_key>")
)

hub = AccountHubV2(
    appwrite_client=client,
    database_id="ctrader_auth",
    slave_accounts_table="slave_accounts",
    internal_url="https://internal.mrme.tech",
    internal_api_key="<x-internal-key>",
    client_id="<cTrader client id>",
    client_secret="<cTrader client secret>",
    account_events_table="account_events",
)
await hub.start()
```

## Account events persistence

`remote-services/ctrader/account_events_persister.py` defines `AccountEventsPersister`.

- Batches events (50 events or 5 seconds, whichever comes first).
- Normalizes protobuf payloads to JSON using `MessageToDict`.
- Writes rows into the `account_events` TablesDB table in the `ctrader_auth` database.
- Columns: `grant_id`, `ctid_trader_account_id`, `is_live`, `event_type`, `event_json`, `timestamp_ms`, `received_at`.

## WebSocket fan-out

`remote-services/ctrader/ws_server.py` defines `AccountWebSocketServer`.

- Accepts `AccountHub` or `AccountHubV2`.
- Endpoints:
  - `GET /health` — connection counts
  - `GET /accounts` — list managed accounts
  - `GET /accounts/{grant_id}/{ctid}` — get specific account state
  - `WS /ws` — subscribe to real-time account events; optional filter by `grant_id`, `ctid`, `live_only`

## Market data service integration

`remote-services/market_data_service/feed_manager_appwrite.py` defines `AppwriteFeedManager`.

- Enabled when `CTRADER_USE_APPWRITE_AUTH=true`.
- Discovers active slave accounts from Appwrite.
- Picks a representative account for the configured environment (live/demo); prefer `CTRADER_APPWRITE_USERNAME` if set.
- Opens an `EnvironmentConnection` and forwards ticks, bars, and depth into the existing ingestion pipeline.
- `FeedManager.connect()` branches to `AppwriteFeedManager` when `settings.auth_mode == "appwrite"`; otherwise the legacy path remains unchanged.

### Default persistence: InfluxDB Cloud Serverless

`remote-services/market_data_service/config.py` defaults `db_backend` to `influxdb`.

- Ticks, bars, order book, indicators, signals, and data-quality reports are written to InfluxDB Cloud Serverless.
- SQLite is used only when `MARKET_DATA_DB_BACKEND=sqlite` or when InfluxDB is unavailable.
- Rate limiting and batching are configured via `influxdb_max_write_bytes_per_sec`, `influxdb_write_burst_bytes`, and `influxdb_write_batch_size`.
- A local SQLite sidecar stores metadata when InfluxDB is active (`MARKET_DATA_INFLUXDB_SIDECAR_PATH`).

## Configuration

### Environment variables

| Variable | Purpose |
|----------|---------|
| `CTRADER_USE_APPWRITE_AUTH` | Enable Appwrite-native auth for the data service (`true`/`false`) |
| `CTRADER_AUTH_BROKER_URL` | `ctrader-internal` Appwrite Function domain |
| `INTERNAL_API_KEY` | `x-internal-key` for `ctrader-internal` |
| `CTRADER_AUTH_GRANT_ID` | Grant ID for single-account broker-backed access |
| `CTRADER_CLIENT_ID` | cTrader OAuth client ID |
| `CTRADER_CLIENT_SECRET` | cTrader OAuth client secret |
| `CTRADER_APPWRITE_USERNAME` | Optional preferred slave username for market-data subscriptions |
| `CTRADER_AUTH_DATABASE_ID` | Appwrite database holding `slave_accounts`/`account_events` (default: `ctrader_auth`) |
| `SLAVE_ACCOUNTS_TABLE` | Default: `slave_accounts` |
| `ACCOUNT_EVENTS_TABLE` | Default: `account_events` |
| `MARKET_DATA_DB_BACKEND` | Default: `influxdb` (`sqlite`, `mongodb`, `appwrite`, `influxdb`) |
| `MARKET_DATA_INFLUXDB_*` | InfluxDB Cloud Serverless credentials |

### YAML config

`remote-services/config/dataservice-config.yml.example` documents the same keys under nested `database`, `ctrader`, and `service` sections.

### cTrader hub config

`remote-services/ctrader/config.py` adds:

- `account_hub_environment_mode` — set `true` to use `AccountHubV2`.
- `account_events_table` — target table for event persistence.
- `account_hub_poll_interval`, `account_hub_reconnect_base`, `account_hub_reconnect_max`.

## cTrader CLI

`remote-services/ctrader_cli/` provides a command-line interface for trading and market-data queries inside the container (or locally).

```bash
# Inside the container
python -m ctrader_cli --grant-id <grant_id> account info
python -m ctrader_cli --grant-id <grant_id> spot EURUSD
python -m ctrader_cli --grant-id <grant_id> order create --symbol EURUSD --side buy --type MARKET --volume 0.01
python -m ctrader_cli --grant-id <grant_id> position close --position-id <id>
```

- Auto-discovers the account when `account_id` is omitted and only one account is available.
- Supports `--broker-url` and `--grant-id` overrides for multi-grant environments.

## Migration notes

- **Opt-in**: set `CTRADER_USE_APPWRITE_AUTH=true` for Appwrite-native data service mode. Without it, the legacy `CTRADER_ACCOUNT_ID` / `CTRADER_AUTH_GRANT_ID` path is still evaluated.
- **Account hub**: set `ACCOUNT_HUB_ENVIRONMENT_MODE=true` in `ctrader/config.py` to switch `ctrader/account_hub_server.py` from legacy `AccountHub` to `AccountHubV2`.
- **TablesDB schema**: ensure `account_events` exists in `ctrader_auth` with the columns listed above.
- **InfluxDB**: configure `MARKET_DATA_INFLUXDB_TOKEN`, `MARKET_DATA_INFLUXDB_ORG`, and `MARKET_DATA_INFLUXDB_HOST`. Set `MARKET_DATA_DB_BACKEND=sqlite` to force the legacy SQLite backend.

## Key files

| File | Purpose |
|------|---------|
| `remote-services/ctrader/env_connection.py` | One-transport-per-environment cTrader connection |
| `remote-services/ctrader/account_discovery.py` | Appwrite TablesDB account discovery |
| `remote-services/ctrader/account_events_persister.py` | Batched event persistence to Appwrite |
| `remote-services/ctrader/account_hub_v2.py` | Persistent multi-account hub orchestrator |
| `remote-services/ctrader/account_hub_server.py` | FastAPI/WebSocket entry point (switches hub implementation) |
| `remote-services/ctrader/ws_server.py` | WebSocket + REST fan-out server |
| `remote-services/ctrader/config.py` | Hub configuration |
| `remote-services/market_data_service/feed_manager_appwrite.py` | Appwrite-native market-data feed manager |
| `remote-services/market_data_service/feed_manager.py` | Delegates to `AppwriteFeedManager` in Appwrite mode |
| `remote-services/market_data_service/config.py` | Data service settings (InfluxDB default + Appwrite-native keys) |
| `remote-services/market_data_service/influxdb_database.py` | InfluxDB Cloud Serverless backend |
| `remote-services/ctrader_cli/` | cTrader Open API CLI (market data + trading, Appwrite-auth aware) |
| `remote-services/config/dataservice-config.yml.example` | Example YAML config |
| `remote-services/config/dataservice.env.example` | Example environment config |

## References

- [cTrader Open API connection guide](https://help.ctrader.com/open-api/connection/)
- [cTrader Open API account authentication](https://help.ctrader.com/open-api/account-authentication/)
- [Appwrite TablesDB documentation](https://appwrite.io/docs/products/databases/tables)
- [InfluxDB Cloud Serverless Python client](https://github.com/InfluxCommunity/influxdb3-python)
