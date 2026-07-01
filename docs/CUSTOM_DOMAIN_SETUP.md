# Stable Callback URL Architecture

## Problem

Appwrite Function auto-generated URLs (`*.appwrite.run`) change with every redeployment. The cTrader OAuth callback URL must remain constant because it is registered once in the cTrader Open API portal and cannot be changed per-deployment.

Current URL (changes on redeploy):
```
https://ctrader-auth.sgp.appwrite.run/callback
```

## Solution: Cloudflare Worker Proxy + Custom Domain

We introduce a **stable custom domain** (`auth.mrme.tech`) that proxies OAuth callback requests to the currently active Appwrite Function deployment. The proxy is a lightweight Cloudflare Worker that forwards requests to the current function URL stored in a Worker environment variable.

```
┌─────────────────┐      ┌──────────────────────┐      ┌─────────────────────────┐
│  cTrader OAuth  │      │  Cloudflare Worker   │      │  Appwrite Function      │
│  Portal         │      │  auth.mrme.tech      │      │  ctrader-auth           │
│                 │      │                      │      │  (changes per deploy)   │
└────────┬────────┘      └──────────┬───────────┘      └────────────┬────────────┘
         │                          │                                │
         │  Redirect to:            │  Forward to:                   │
         │  https://auth.mrme.tech  │  CTRADER_AUTH_FUNCTION_URL     │
         │  /callback?code=...      │  (env var, updated by CI/CD)   │
         └──────────────────────────►────────────────────────────────┘
```

### Architecture Components

1. **Stable Custom Domain** (`auth.mrme.tech`)
   - DNS CNAME managed in Cloudflare
   - Always resolves to Cloudflare Worker
   - Never changes regardless of Appwrite deployments

2. **Cloudflare Worker Proxy** (`cloudflare/worker-callback-proxy/`)
   - Receives OAuth callback at `/callback`
   - Forwards to current Appwrite Function URL via environment variable
   - Also intercepts `/pin/*`, `/internal/*`, `/session`, `/logout` for other function endpoints
   - Minimal code, zero business logic (thin proxy)

3. **CI/CD GitHub Actions** (`.github/workflows/deploy.yml`)
   - Deploys Appwrite Functions from `main`/`functions` branch
   - After successful function deploy, extracts the new function URL
   - Updates Cloudflare Worker environment variable with new URL
   - Deploys the Cloudflare Worker (if code changed)

4. **GitHub Repository Branches**
   - `main`: Production-ready code, triggers full deployment
   - `functions`: Function code development, triggers function-only deploy
   - `site`: Static site (SPA) development, triggers site-only deploy
   - `develop`: Integration branch, deploys to staging environment

### DNS Configuration

In Cloudflare DNS for `mrme.tech`:

| Type | Name | Target | Proxy Status |
|------|------|--------|--------------|
| CNAME | auth | worker-callback-proxy.mrme000m.workers.dev | Proxied |

Or use a Cloudflare Worker Custom Domain for `auth.mrme.tech` directly.

### Environment Variables

Cloudflare Worker secrets (set via `wrangler secret put` or GitHub Actions):

| Variable | Purpose |
|----------|---------|
| `CTRADER_AUTH_FUNCTION_URL` | Current Appwrite Function URL for ctrader-auth |
| `CTRADER_PIN_FUNCTION_URL` | Current Appwrite Function URL for ctrader-pin-auth |
| `CTRADER_INTERNAL_FUNCTION_URL` | Current Appwrite Function URL for ctrader-internal |

### Appwrite Custom Domain (Alternative/Complementary)

You can also add `appwrite.mrme.tech` as a custom domain in the Appwrite Console:
1. Go to Project Settings → Custom Domains
2. Add domain `appwrite.mrme.tech`
3. Add CNAME in Cloudflare pointing to Appwrite verification target
4. This gives a stable API endpoint for all Appwrite services

However, per-function execution URLs under the custom domain still require the function execution path (`/v1/functions/{id}/executions`), which is not as clean for OAuth callbacks. The Worker proxy provides cleaner URLs (`/callback`, `/pin-login`, etc.).

## Workflow

### 1. Developer pushes to `functions` branch
```
git checkout functions
git add functions/ctrader-auth/src/main.js
git commit -m "fix: OAuth state validation"
git push origin functions
```

### 2. GitHub Actions triggers
- Builds and deploys Appwrite Functions
- Extracts new auto-generated function URLs
- Updates Cloudflare Worker environment variables
- Deploys updated Worker (if proxy code changed)

### 3. cTrader callback remains stable
The cTrader portal always calls `https://auth.mrme.tech/callback`.
The Worker forwards to the new function URL automatically.

## Files

| File | Purpose |
|------|---------|
| `cloudflare/worker-callback-proxy/src/index.js` | Worker proxy code |
| `cloudflare/worker-callback-proxy/wrangler.toml` | Worker deployment config |
| `.github/workflows/deploy.yml` | CI/CD pipeline |
| `dev/scripts/setup-callback-domain.py` | One-time domain setup script |
| `init-scripts/config.yml` | cTrader config with stable redirect_uri |
| `sites/ctrader-auth-site/config.js` | Site config using stable domain |

## Initial Setup

Run the setup script:
```bash
./dev.sh setup-callback-domain
```

This will:
1. Create Cloudflare DNS records for `auth.mrme.tech`
2. Deploy the initial Worker proxy
3. Configure the Worker environment variables
4. Update `init-scripts/config.yml` with the stable callback URL
5. Print instructions for registering the callback in cTrader Open API portal

## cTrader Open API Portal Registration

After setup, register this exact callback URL in https://openapi.ctrader.com:

```
https://auth.mrme.tech/callback
```

This URL will never change, regardless of how many times you redeploy the Appwrite Functions.
