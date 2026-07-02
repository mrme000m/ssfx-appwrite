# PPLX Agent — Gold Market Intelligence

A Perplexity AI–powered agent harness that builds and maintains a **long-term
picture of the gold market** and updates it every day. It uses:

- **Perplexity Pro / Reasoning / Deep Research** for macro, technical,
  fundamental, and long-term synthesis.
- **TradingView free-tier scanner** for multi-timeframe recommendation data
  (M1 through MN1).
- **Data Service `/api/v1/gold/quant`** snapshot for local quantitative context.
- **Perplexity Spaces** as a persistent knowledge base for reports and the
  rolling long-term summary.

## Quick Start

```bash
# 1. Ensure the shared environment has dependencies. The agent reuses the
#    remote-services virtual environment; curl-cffi is installed automatically
#    when the services image is built.

# 2. Copy the example env (only needed for local development without Appwrite)
cd /Volumes/ExMac/code/ssfx/appwrite-auth/pplx-agent
cp config/example.env .env

# 3. Create the Perplexity Space
./dev.sh pplx-agent setup

# 4. Persist the returned UUID to Appwrite service_config (preferred) or .env
#    GOLD_MARKET_SPACE_UUID=<uuid>

# 5. Run the daily update manually
./dev.sh pplx-agent update

# 6. Query the knowledge base
./dev.sh pplx-agent query "What is the current long-term gold bias?"

# 7. Start the API server
./dev.sh pplx-agent server
```

## Configuration Source of Truth

Per project conventions, runtime settings should be stored in the Appwrite
`service_config` table under `config_key = pplx_agent`. Use:

```bash
python init-scripts/pplx-agent.py
```

For local development the same keys can be set in `pplx-agent/.env`.

## Daily Pipeline

```
./dev.sh pplx-agent update
  |
  ├─ Fetch latest XAUUSD price & quant snapshot from Data Service
  ├─ Fetch TradingView multi-timeframe technical summary
  ├─ Perplexity Deep Research (macro)
  ├─ Perplexity Pro (technical + fundamental)
  ├─ Perplexity Reasoning (long-term synthesis)
  ├─ Generate Markdown report
  └─ Upload to Perplexity Space + update LONGTERM_pinned_summary.md
```

## API Endpoints

When running the server (`./dev.sh pplx-agent server`):

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/health` | Service health and configured symbol/space |
| POST | `/api/v1/gold/update` | Trigger the daily update |
| POST | `/api/v1/gold/query` | Ask the knowledge base a question |
| GET | `/api/v1/gold/rate-limits` | Perplexity usage limits |

## Integration with agent_harness

`remote-services/agent_harness` exposes its own research endpoint that calls the
PPLX Agent API. This lets the entry/lifecycle decision agents enrich their
context with the latest long-term picture stored in Perplexity Spaces.

## Deployment

The service is packaged into the `ctrader-services` Docker image and started by
supervisord on port `9004`. It is also added as a public hostname on the
Cloudflare tunnel (e.g. `pplx-agent.mrme.tech`) after the tunnel ingress rules
are updated.

### Local

```bash
cd /Volumes/ExMac/code/ssfx/appwrite-auth/remote-services
docker compose up -d
```

### Azure VM (primary remote)

```bash
cd /Volumes/ExMac/code/ssfx/appwrite-auth
./dev.sh deploy
```

This syncs both `remote-services/` and `pplx-agent/` to the VM, uploads Appwrite
bootstrap secrets, mounts Perplexity cookies from `~/.config/perplexity/cookies.json`,
and starts the container.

Use `remote-services/setup-cf-tunnel.sh` to add/update the Cloudflare ingress rule
and CNAME for `pplx-agent.mrme.tech`.

### Perplexity Cookies

Cookies must be available inside the container at
`/root/.config/perplexity/cookies.json`. On the dev machine they are kept at
`~/.config/perplexity/cookies.json` and synced/mounted by the deploy scripts.
Do not commit cookies.
