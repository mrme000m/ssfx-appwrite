# SSFX v2 Resource Configuration Documentation

## Table of Contents

1. [Host and Domain Configuration](#host-and-domain-configuration)
2. [API Endpoints and Paths](#api-endpoints-and-paths)
3. [Database Schema](#database-schema)
4. [Infrastructure Configuration](#infrastructure-configuration)
5. [Environment Variables](#environment-variables)
6. [Naming Convention Review](#naming-convention-review)
7. [Code Duplication Analysis](#code-duplication-analysis)

## Host and Domain Configuration

### Public Hostnames

The system uses the following public hostnames under the `mrme.tech` domain:

| Hostname | Service | Port | Purpose |
|----------|---------|------|---------|
| `auth.mrme.tech` | ctrader-auth Function | N/A | OAuth2 authentication, session management |
| `pin.mrme.tech` | ctrader-pin-auth Function | N/A | PIN-based authentication |
| `ssfx-api.mrme.tech` | ssfx_server | 8000 | Telegram webhook, admin API |
| `dataservice.mrme.tech` | market_data_service | 9002 | Market data REST API |
| `agent.mrme.tech` | agent_harness | 9003 | AI decision making API |
| `pplx-agent.mrme.tech` | pplx_agent | 9004 | Perplexity research agent |
| `app.mrme.tech` | ssfx-hq Site | N/A | Main dashboard SPA |

### Cloudflare Tunnel Ingress

Source: `remote-services/config/tunnel-ingress.json`

```json
{
  "ingress": [
    {
      "hostname": "ssfx-api.mrme.tech",
      "service": "http://localhost:8000"
    },
    {
      "hostname": "dataservice.mrme.tech",
      "service": "http://localhost:9002"
    },
    {
      "hostname": "agent.mrme.tech",
      "service": "http://localhost:9003"
    },
    {
      "hostname": "pplx-agent.mrme.tech",
      "service": "http://localhost:9004"
    },
    {
      "service": "http_status:404"
    }
  ]
}
```

### Legacy Hostnames (Deprecated)

- `hq.mrme.tech` - Merged into `app.mrme.tech`
- `command.mrme.tech` - Merged into `app.mrme.tech`
- `auth-ctrader.mrme0.store` - Replaced by `auth.mrme.tech`

## API Endpoints and Paths

### Authentication Functions

#### ctrader-auth Function (`auth.mrme.tech`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/auth/ctrader/start` | Start OAuth2 flow with cTrader |
| GET | `/callback` | OAuth2 callback handler |
| GET | `/session` | Check current session |
| POST | `/logout` | Logout and clear session |
| GET | `/admin/slaves` | List all slave accounts (master only) |
| GET | `/echo` | Debug endpoint |
| GET | `/session-debug` | Session debugging |

#### ctrader-pin-auth Function (`pin.mrme.tech`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/pin-login` | Login with username + PIN |
| POST | `/set-credentials` | Set username + PIN for new slave |
| POST | `/pin-reset/request` | Request PIN reset email |
| POST | `/pin-reset/confirm` | Confirm PIN reset with token |

#### ctrader-internal Function

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/internal/ctrader/refresh` | Refresh cTrader access token |
| GET | `/internal/grant/latest` | Get latest grant for user |
| POST | `/internal/grant/:grant_id/accounts` | Persist discovered accounts |
| GET | `/internal/grant/:grant_id/accounts` | Get accounts for grant |

### Runtime Services

#### ssfx_server (`ssfx-api.mrme.tech`)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/webhook` | Telegram Bot API webhook |
| GET | `/health` | Health check |
| POST | `/api/signals/inject` | Direct signal injection |
| GET | `/api/accounts` | List configured accounts |
| GET | `/api/accounts/:name/state` | Get account state |
| PATCH | `/api/accounts/:name` | Update account |
| GET | `/api/signals` | List signals |
| GET | `/api/signals/:chatId/:messageId/executions` | Get signal executions |
| GET | `/api/executions` | List executions |
| GET | `/api/executions/stream` | SSE execution stream |
| GET | `/api/agent-logs` | Get agent logs |
| GET | `/api/signal-experience` | Get signal experience |

#### market_data_service (`dataservice.mrme.tech`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/health` | Health check |
| GET | `/api/v1/gold/quant` | Gold quant analysis |
| GET | `/api/v1/gold/mtf` | Multi-timeframe analysis |
| GET | `/api/v1/feed/status` | Feed status |
| GET | `/api/v1/symbols` | List symbols |
| GET | `/api/v1/quality/:symbol` | Symbol quality data |

#### agent_harness (`agent.mrme.tech`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | Health check |
| POST | `/agent/v1/signal/intent` | Signal intent classification |
| POST | `/agent/v1/entry/decision` | Entry decision |
| POST | `/agent/v1/lifecycle/plan` | Lifecycle planning |

### SPA Configuration

#### ssfx-hq (`app.mrme.tech`)

**config.js endpoints:**
```javascript
window.APP_CONFIG = {
  "endpoint": "https://sgp.cloud.appwrite.io/v1",
  "projectId": "6a22a362002b9ae880bb",
  "authDomain": "https://auth.mrme.tech",
  "pinDomain": "https://pin.mrme.tech",
  "v2ApiBase": "https://ssfx-api.mrme.tech",
  "dataserviceBase": "https://dataservice.mrme.tech",
  "agentHarnessBase": "https://agent.mrme.tech",
  "databaseId": "ctrader_auth",
  "marketDatabaseId": "market_data",
  "siteUrl": "https://app.mrme.tech"
};
```

## Database Schema

### Appwrite TablesDB

#### Database: `ctrader_auth`

**Tables:**

1. **slave_accounts**
   - Primary user identity table
   - Stores Appwrite user mappings, grant IDs, encrypted tokens
   - Row-level security enabled

2. **trade_configs**
   - Per-slave trading configuration
   - Lot size, multipliers, risk settings
   - Row-level security enabled

3. **accounts**
   - Discovered cTrader trading accounts
   - Account IDs, broker info, balance data
   - Row-level security disabled

4. **account_events**
   - Real-time account state events
   - Positions, orders, balance changes
   - Row-level security disabled

5. **ctrader_trading_events**
   - Trading operation event log
   - Order fills, position changes, errors

6. **master_signals**
   - Master-to-slave signal broadcast

7. **ssfx_accounts**
   - Telegram signal follower configuration
   - Symbol filters, SL/TP settings

8. **ssfx_executions**
   - Signal execution history
   - Per-follower execution status

9. **ephemeral_tokens**
   - Short-lived tokens (OAuth state, PIN reset)

10. **grant_locks**
    - Distributed locks for token refresh

11. **service_config**
    - System configuration (OAuth, master auth)

#### Database: `market_data`

**Tables:**
- Market data storage
- Time-series data for analysis
- Symbol quality metrics

### Detailed Table Schemas

#### slave_accounts

```json
{
  "appwrite_user_id": "varchar(64)",
  "grant_id": "varchar(64)",
  "username": "varchar(32)",
  "email": "varchar(255)",
  "role": "varchar(16)",
  "pin_hash": "varchar(255)",
  "access_token_enc": "text",
  "refresh_token_enc": "text",
  "access_token_expires_at": "datetime",
  "ctrader_account_ids": "text",
  "selected_account_id": "varchar(64)",
  "status": "varchar(32)",
  "active": "boolean",
  "last_heartbeat_at": "datetime"
}
```

#### accounts

```json
{
  "grant_id": "varchar(255)",
  "ctidTraderAccountId": "integer",
  "isLive": "boolean",
  "traderLogin": "varchar(64)",
  "brokerTitleShort": "varchar(255)",
  "brokerName": "varchar(255)",
  "lastClosingDealTimestamp": "datetime",
  "lastBalanceUpdateTimestamp": "datetime",
  "balance": "double",
  "moneyDigits": "integer",
  "accountType": "varchar(64)",
  "depositAssetId": "varchar(64)",
  "leverageInCents": "integer",
  "registrationTimestamp": "datetime",
  "selected": "boolean"
}
```

#### account_events

```json
{
  "grant_id": "varchar(255)",
  "ctid_trader_account_id": "integer",
  "is_live": "boolean",
  "event_type": "varchar(100)",
  "event_data": "json",
  "timestamp": "datetime"
}
```

## Infrastructure Configuration

### Cloud Provider

- **Primary**: Azure VM
- **Backup**: Local development with Docker Compose
- **Public Access**: Cloudflare Tunnel

### Cloudflare Configuration

- **Account ID**: `4f6d43db5dbe773f750a2c8f941d0cdc`
- **Zone ID**: `5290d99f626b08c46c1eca6cc7cfa090`
- **Tunnel Name**: `ssfx_azurue`
- **Tunnel ID**: `d1e96e86-a44a-457a-a60c-e7d5d5d675bd`

### Docker Services

**docker-compose.yml services:**

```yaml
services:
  ssfx-server:
    image: ssfx-server
    ports:
      - "8000:8000"
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - WEBHOOK_HOST=https://ssfx-api.mrme.tech
      - WEBHOOK_PATH=/webhook

  dataservice-api:
    image: dataservice
    ports:
      - "9002:9002"
    environment:
      - DATA_SERVICE_API_KEY=${DATA_SERVICE_API_KEY}

  agent-harness:
    image: agent-harness
    ports:
      - "9003:9003"

  pplx-agent:
    image: pplx-agent
    ports:
      - "9004:9004"
```

### CI/CD Pipeline

**GitHub Actions Workflow:** `.github/workflows/deploy.yml`

**Deployment Steps:**
1. Push TablesDB schema
2. Deploy Appwrite Functions
3. Deploy Appwrite Site
4. Verify custom domains
5. Smoke test endpoints
6. Cleanup old deployments

## Environment Variables

### Shared Configuration

**`.env` (project root):**
```bash
APPWRITE_PROJECT_ID=6a22a362002b9ae880bb
APPWRITE_API_KEY=your_api_key
APPWRITE_ENDPOINT=https://sgp.cloud.appwrite.io/v1
CTRADER_AUTH_DATABASE_ID=ctrader_auth
TOKEN_ENCRYPTION_KEY=your_encryption_key
SESSION_HMAC_KEY=your_hmac_key
INTERNAL_API_KEY=your_internal_key
```

### Function-Specific Variables

**ctrader-auth:**
```bash
CTRADER_CLIENT_ID=your_client_id
CTRADER_CLIENT_SECRET=your_client_secret
CTRADER_REDIRECT_URI=https://auth.mrme.tech/callback
SITES_URL=https://app.mrme.tech
```

**ctrader-pin-auth:**
```bash
BCRYPT_SALT_ROUNDS=12
PIN_RESET_BASE_URL=https://app.mrme.tech
```

**ctrader-internal:**
```bash
INTERNAL_API_KEY=your_internal_key
```

**ctrader-token-refresh-worker:**
```bash
REFRESH_BUFFER_HOURS=2
```

### Runtime Services Configuration

**remote-services/config/v2.env:**
```bash
# Telegram
TELEGRAM_BOT_TOKEN=your_bot_token
SOURCE_CHAT_ID=-1001661400724
WEBHOOK_HOST=https://ssfx-api.mrme.tech
WEBHOOK_PATH=/webhook
TELEGRAM_WEBHOOK_SECRET_TOKEN=your_secret

# Appwrite
APPWRITE_API_KEY=your_api_key
APPWRITE_PROJECT_ID=6a22a362002b9ae880bb
APPWRITE_ENDPOINT=https://sgp.cloud.appwrite.io/v1
CTRADER_AUTH_DATABASE_ID=ctrader_auth

# Data Service
DATA_SERVICE_API_KEY=your_key
DATA_SERVICE_URL=https://dataservice.mrme.tech

# Agent Harness
AGENT_HARNESS_URL=https://agent.mrme.tech
AGENT_INTENT_ENABLED=true
AGENT_ENTRY_ENABLED=true
AGENT_LIFECYCLE_ENABLED=true

# cTrader
CTRADER_CLIENT_ID=your_client_id
CTRADER_CLIENT_SECRET=your_client_secret
CTRADER_AUTH_BROKER_URL=https://auth.mrme.tech
CTRADER_AUTH_INTERNAL_KEY=your_internal_key
```

## Naming Convention Review

### Current Naming Issues

1. **Inconsistent Hostname Patterns**
   - `auth.mrme.tech` vs `pin.mrme.tech` (separate functions)
   - Could be unified under `auth.mrme.tech` with paths

2. **Function Name Redundancy**
   - `ctrader-auth`, `ctrader-pin-auth`, `ctrader-internal` all have "ctrader" prefix
   - Internal function name doesn't clearly indicate it's for server-to-server

3. **Database Table Naming**
   - `slave_accounts` vs `accounts` (confusing relationship)
   - `trade_configs` vs `ssfx_accounts` (overlap in purpose)

4. **Endpoint Path Inconsistency**
   - `/auth/ctrader/start` vs `/pin-login` (different patterns)
   - `/internal/ctrader/refresh` vs `/internal/grant/latest` (mixed naming)

### Suggested Improvements

#### Hostname Improvements

| Current | Suggested | Reason |
|---------|-----------|--------|
| `auth.mrme.tech` | `auth.mrme.tech` | Keep (primary auth) |
| `pin.mrme.tech` | `auth.mrme.tech` | Merge under auth domain |
| `ssfx-api.mrme.tech` | `api.mrme.tech` | More generic |
| `dataservice.mrme.tech` | `market.mrme.tech` | More descriptive |
| `agent.mrme.tech` | `ai.mrme.tech` | Shorter |
| `pplx-agent.mrme.tech` | `research.mrme.tech` | More descriptive |

#### Function Name Improvements

| Current | Suggested | Reason |
|---------|-----------|--------|
| `ctrader-auth` | `auth-oauth` | Clarify OAuth purpose |
| `ctrader-pin-auth` | `auth-pin` | Simpler, under auth |
| `ctrader-internal` | `api-internal` | Clarify server-to-server |
| `ctrader-token-refresh-worker` | `token-refresh` | Remove redundancy |

#### Database Table Improvements

| Current | Suggested | Reason |
|---------|-----------|--------|
| `slave_accounts` | `users` | More generic, less pejorative |
| `accounts` | `ctrader_accounts` | Clarify relationship |
| `trade_configs` | `user_trade_settings` | More descriptive |
| `ssfx_accounts` | `signal_followers` | Clarify purpose |
| `account_events` | `account_state_history` | More descriptive |

#### Endpoint Path Improvements

**Current:**
- `/auth/ctrader/start`
- `/pin-login`
- `/internal/ctrader/refresh`

**Suggested:**
- `/oauth/start`
- `/auth/pin/login`
- `/api/internal/token/refresh`

## Code Duplication Analysis

### Identified Duplications

1. **CORS Header Functions**
   - Location: `functions/_shared/index.js`
   - Issue: Multiple functions define similar CORS handling
   - Solution: Consolidate into single utility function

2. **Database Client Creation**
   - Location: Multiple function files
   - Issue: Each function creates admin DB client separately
   - Solution: Centralize in shared module

3. **Configuration Loading**
   - Location: `ctrader-auth/src/main.js` and `ctrader-internal/src/main.js`
   - Issue: Both have identical `getOAuthConfig()` functions
   - Solution: Move to shared module

4. **Error Handling**
   - Location: Across all functions
   - Issue: Inconsistent error response formats
   - Solution: Standardize error handling utility

5. **Session Management**
   - Location: `ctrader-auth/src/main.js` and SPA code
   - Issue: Session cookie handling duplicated
   - Solution: Centralize session utilities

### Specific Code Overlaps

#### 1. OAuth Configuration Loading

**ctrader-auth/src/main.js (lines 32-49):**
```javascript
async function getOAuthConfig() {
  const db = makeAdminDb();
  const svc = await getServiceConfig(db, 'ctrader_oauth', 'CTRADER_OAUTH_JSON');
  if (svc && typeof svc === 'object' && svc.client_id) {
    return {
      clientId: svc.client_id,
      clientSecret: svc.client_secret,
      redirectUri: svc.redirect_uri,
      environment: svc.environment,
    };
  }
  return {
    clientId: process.env.CTRADER_CLIENT_ID,
    clientSecret: process.env.CTRADER_CLIENT_SECRET,
    redirectUri: process.env.CTRADER_REDIRECT_URI || `${process.env.SITES_URL}/callback`,
    environment: process.env.CTRADER_ENVIRONMENT || 'demo',
  };
}
```

**ctrader-internal/src/main.js (lines 28-45):**
```javascript
async function getOAuthConfig() {
  const db = makeAdminDb();
  const svc = await getServiceConfig(db, 'ctrader_oauth', 'CTRADER_OAUTH_JSON');
  if (svc && typeof svc === 'object' && svc.client_id) {
    return {
      clientId: svc.client_id,
      clientSecret: svc.client_secret,
      redirectUri: svc.redirect_uri,
      environment: svc.environment,
    };
  }
  return {
    clientId: process.env.CTRADER_CLIENT_ID,
    clientSecret: process.env.CTRADER_CLIENT_SECRET,
    redirectUri: process.env.CTRADER_REDIRECT_URI,
    environment: process.env.CTRADER_ENVIRONMENT || 'demo',
  };
}
```

**Solution:** Move to `functions/_shared/index.js` and import

#### 2. CORS Header Functions

**functions/_shared/index.js (lines 285-300):**
```javascript
function corsHeaders(origin) {
  const allowed = CORS_ORIGINS.includes(origin) ? origin : CORS_ORIGINS[0] || 'https://app.mrme.tech';
  return {
    'Access-Control-Allow-Origin': allowed,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization, x-internal-key',
    'Access-Control-Allow-Credentials': 'true',
    'Vary': 'Origin'
  };
}
```

**Issue:** Each function imports and uses this, but some have slight variations

**Solution:** Standardize and ensure all functions use the same implementation

#### 3. Token Encryption/Decryption

**functions/_shared/index.js:**
```javascript
function encrypt(text) {
  // AES-GCM-256 encryption
}

function decrypt(encrypted) {
  // AES-GCM-256 decryption
}
```

**Issue:** Used across multiple functions but not consistently imported

**Solution:** Ensure all functions use the shared utilities

### Recommended Refactoring

1. **Create Shared Configuration Module**
   - Move OAuth config, CORS headers, encryption to shared module
   - Standardize error handling
   - Centralize database client creation

2. **Consolidate Authentication Logic**
   - Merge OAuth and PIN auth under single domain
   - Standardize session management
   - Unify error responses

3. **Database Schema Cleanup**
   - Rename tables for clarity
   - Consolidate overlapping functionality
   - Standardize column naming

4. **API Endpoint Standardization**
   - Use consistent path patterns
   - Standardize response formats
   - Document all endpoints uniformly

## Conclusion

The current resource configuration shows a well-structured system but with opportunities for improvement in naming consistency and code organization. The identified duplications primarily revolve around shared utilities that should be centralized. The suggested improvements would enhance maintainability and reduce potential inconsistencies across the codebase.

Key recommendations:
1. Standardize naming conventions across hosts, functions, and database tables
2. Centralize shared utilities (OAuth config, CORS, encryption)
3. Consolidate authentication under a single domain
4. Clean up database schema for clarity
5. Document all endpoints consistently

These changes would make the system more maintainable and easier to understand for new developers while preserving all existing functionality.