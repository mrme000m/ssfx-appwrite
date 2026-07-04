# remote-services — Linux VM Docker runtime

Self-contained cTrader services container. All Python packages live inside this
directory. Code is baked into the image at build time; configuration is mounted
at runtime.

## What runs inside the container

| Process | Port | Purpose | Public hostname |
|---------|------|---------|-----------------|
| `dataservice-daemon` | `9000` | Market-data ingestion / control API | `ds-control.mrme.tech` |
| `dataservice-sse` | `9001` | MCP SSE server (live prices/tools) | `ds-sse.mrme.tech` |
| `dataservice-api` | `9002` | OpenPI REST API + admin UI | `market.mrme.tech` |
| `ssfx-server` | `8000` | Telegram webhook + cTrader slave admin | `api.mrme.tech` |
| `agent-harness` | `9003` | AI decision layer (intent, entry, lifecycle) | `ai.mrme.tech` |
| `pplx-agent` | `9004` | Perplexity + TradingView gold market research | `research.mrme.tech` |
| `ctrader` | `9300` | Unified cTrader service (WS hub + trade exec) | `ctrader.mrme.tech` |
| `account-hub` | `9301` | Persistent cTrader connections for all slave accounts | `account-hub.mrme.tech` |

For the Appwrite-native account hub and data service architecture, see [`docs/account-hub-and-dataservice.md`](../docs/account-hub-and-dataservice.md).

## Package layout

```
remote-services/
├── ssfx_parser/             Signal parsing library (shared)
├── ctrader_client/          cTrader Open API client library (shared)
├── ctrader_cli/             cTrader Open API CLI (market data + trading)
├── ssfx_trader/             Trade execution engine (shared)
├── ssfx_server/             Telegram webhook server (service)
├── ctrader/                 Unified cTrader service (service)
├── market_data_service/     Market data MCP + daemon + REST API (service)
├── agent_harness/           AI decision layer for XAUUSD signals
├── pplx-agent/              Perplexity gold-market research (mounted via compose)
├── bin/                     Process runner scripts
├── config/                  Runtime configs (mounted, not committed)
└── logs/                    Persistent log output
```

## First-time setup on the VM

1. Copy and fill in the runtime configs:

   ```bash
   cd remote-services
   cp config/dataservice.env.example config/dataservice.env
   cp config/v2.env.example config/v2.env
   cp config/dataservice-config.yml.example config/dataservice-config.yml
   cp config/tunnel-ingress.json.example config/tunnel-ingress.json
   # Edit the files above with real credentials.
   ```

2. Deploy to the VM:

   ```bash
   ./dev.sh deploy-remote aws
   ```

3. Publish the services through the Cloudflare tunnel and verify DNS:

   ```bash
   ./dev.sh cf-tunnel-update
   ```

## Day-to-day iteration

After editing code in this directory:

```bash
./dev.sh remote-services-sync
```

This rsyncs the changed source, rebuilds the image, and restarts the container.

### Integration test (local Docker)

```bash
# Quick smoke test — builds image, starts stack, tests all endpoints
./dev.sh integration-test

# Force rebuild and keep container running after tests
./dev.sh integration-test --build --keep
```

### Full VM deployment (clean + deploy + tunnel)

```bash
# One-shot fresh-VM provision + deploy + tunnel sync
python3 dev/scripts/setup_vm.py

# Or step by step:
./dev.sh deploy-remote aws         # Deploy to primary VM
./dev.sh cf-tunnel-update          # Update Cloudflare tunnel ingress
```

## Manual commands on the VM

```bash
ssh <vm-user>@<vm-ip>
cd ~/ctrader-services

# Start / restart
docker compose up -d --build
docker compose restart

# View logs
docker compose logs -f
```

## cTrader CLI

The container includes `ctrader_cli` for manual trading and market-data queries:

```bash
docker exec ctrader-services bash -c "source /app/config/v2.env && python -m ctrader_cli --grant-id <grant_id> account info"
docker exec ctrader-services bash -c "source /app/config/v2.env && python -m ctrader_cli --grant-id <grant_id> spot EURUSD"
docker exec ctrader-services bash -c "source /app/config/v2.env && python -m ctrader_cli --grant-id <grant_id> order create --symbol EURUSD --side buy --type MARKET --volume 0.01"
```

When `account_id` is omitted, the CLI auto-discovers the only available account.

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Python 3.11 + supervisor image; installs deps from `pyproject.toml` |
| `docker-compose.yml` | Mounts configs and exposes ports on the VM host |
| `docker-compose.test.yml` | Test override with isolated ports (18000–19301) |
| `supervisord.conf` | Runs eight service processes inside one container |
| `pyproject.toml` | Unified Python project with all dependencies |
| `bin/run-*` | Thin wrappers that invoke each service |
| `integration_test.py` | Full-stack integration test (health + API + WS checks) |
| `setup_vm.py` | One-shot fresh-VM provision, deploy, and tunnel sync |
| `sync-and-restart.sh` | Manual rsync + restart helper (moved to dev/scripts/) |
| `init-tunnel.py` | Clears stale tunnel ingress, ensures DNS records, verifies public reachability |
| `sync-and-restart.sh` | Manual rsync + restart helper |
| `config/*.example` | Templates for runtime secrets and YAML config |

## Notes

- The container exposes ports on `localhost` of the VM; `cloudflared` on the
  VM forwards the public hostnames to those ports.
- Source code is baked into the image at build time; configs and logs are mounted.
- Market data persistence defaults to **SQLite** (`MARKET_DATA_DB_BACKEND=sqlite`);
  set it to `influxdb` to use InfluxDB Cloud Serverless.
- **MongoDB is legacy** — all configuration and state now lives in Appwrite TablesDB.
  The `ssfx-server` can run without MongoDB using the `NoOpSignalStore` fallback
  (signal history is skipped; slaves still process live signals via Appwrite).
- All required Appwrite tables are managed by `dev/scripts/init-ctrader-tables.py`.
