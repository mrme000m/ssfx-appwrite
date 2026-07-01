# cTrader + Appwrite: Master-Slave Trading Auth Layer Architecture

## Overview

This document designs a complete master-slave trading platform where a single master operator controls trade execution across multiple slave cTrader accounts. Appwrite — available free via the GitHub Student Developer Pack — serves as the full backend: hosting the web UI via Sites, handling the OAuth callback via Functions, storing slave tokens and trade configs in Databases, and managing master login via Auth.[^1][^2][^3][^4][^5]

The architecture separates two distinct identities:
- **Master**: The operator/developer who runs the platform, logs in with a username + PIN, and controls or monitors all slave accounts.
- **Slave**: Any cTrader account holder who grants your app permission once via OAuth, after which your backend can trade their account autonomously.

***

## Infrastructure: Appwrite Education Plan (GitHub Student Pack)

The GitHub Student Developer Pack provides **free access to Appwrite Pro** for the duration of your student enrollment. As of 2026, the Education plan supports up to **2 projects** per organization, each with Pro-equivalent resource limits.[^6][^7][^1]

| Resource | Appwrite Education Plan |
|---|---|
| Bandwidth | 300 GB[^8] |
| Storage | 150 GB[^8] |
| Function Executions | 3.5M[^8] |
| Monthly Active Users | 200K[^8] |
| Databases / Functions / Buckets | Unlimited[^8] |
| Backups | Daily, 7-day retention[^8] |
| Projects | 2 (Pro-equivalent each)[^7] |

To activate: verify with GitHub Education → connect GitHub account to Appwrite Cloud → Education plan auto-applies.[^1][^6]

> ⚠️ **Non-commercial restriction**: The Education plan may not be used for commercial purposes. Suitable for development, personal use, and non-commercial copy-trading among friends/family.[^1]

***

## System Components

### 1. Appwrite Sites — Web Interface

Appwrite Sites deploys your frontend directly from source control with a dedicated URL, DDoS protection, WAF, TLS, and global CDN edge distribution. Deploy a lightweight SPA (React, Vue, SvelteKit, or plain HTML) that serves:[^5]

- **Slave onboarding page**: Landing page that generates a cTrader OAuth consent URL and redirects the slave user to authorize your app
- **Slave dashboard**: PIN-based login for slaves to configure their trade settings (lot size, risk %, symbol filters, max drawdown)
- **Master dashboard**: Username + PIN login to view all connected slave accounts, push trade configs, and monitor positions

### 2. Appwrite Functions — OAuth Callback Handler

Appwrite Functions are the backbone of the OAuth flow. Each Function has its own HTTP domain (e.g., `https://64d4d22db370ae41a32e.fra.appwrite.run`), which is what you register as the **cTrader redirect URI** in your app settings at `openapi.ctrader.com`.[^5]

Two key Functions are needed:

**`ctrader-oauth-callback` (GET)** — Handles the code exchange:
```
GET /callback?code=<AUTH_CODE>&state=<USER_ID>
```
This function:
1. Receives `code` and `state` (Appwrite `userId` passed in the OAuth redirect)
2. POSTs to `https://id.ctrader.com/Apps/token` with `client_id`, `client_secret`, `code`, `grant_type=authorization_code`
3. Receives `accessToken` + `refreshToken` from cTrader
4. Calls cTrader Open API `ProtoOAGetAccountListByAccessTokenReq` to enumerate all trading account IDs
5. Persists tokens + account IDs to Appwrite Database (server-side, using API key — never exposed to client)
6. Redirects the slave's browser back to the Sites dashboard with a success indicator

**`token-refresh-worker` (scheduled cron)** — Keeps tokens alive:
- Runs every 30 days (or on demand)
- Reads all slave records from DB
- Uses each stored `refreshToken` to fetch a fresh `accessToken`
- Updates the DB — slaves never need to re-authorize

### 3. Appwrite Auth — Master & Slave Account Management

Appwrite Auth manages two types of users on the platform itself (distinct from their cTrader identity):[^3]

**Slave users**: Register with **email + password** (Argon2-hashed by Appwrite). After OAuth consent completes, their cTrader token data is linked to their Appwrite `userId`. They log in to the web dashboard using their chosen username (stored in prefs) and a 4–6 digit PIN (stored as a bcrypt hash in their Appwrite document).[^3]

**Master user**: Single privileged account identified by an Appwrite **label** of `master`. Labels enable collection-level permission scoping so master-only database records (like global trade signal configs) are only readable by the master label role.[^9]

#### Custom Token Flow for PIN Login

Since Appwrite's built-in auth uses email/password, implementing a PIN-only login uses the **Custom Token** method:[^3]

1. Slave enters username + PIN on the Sites frontend
2. Frontend calls a Function `pin-login` with `{username, pin}`
3. Function looks up the user by username from DB, verifies the hashed PIN
4. If valid, server calls `users.createToken(userId)` using Server SDK[^3]
5. Returns `{userId, secret}` to the client
6. Client calls `account.createSession({userId, secret})` → full Appwrite session established[^3]

This gives slaves a proper Appwrite session (with JWT) without ever exposing email/password, and the session can be used to gate further API calls via `x-appwrite-user-jwt` headers on Functions.[^5]

***

## Database Schema

All data lives in a single Appwrite Database. Collections are structured as follows:[^2][^10]

### Collection: `slave_accounts`

Stores the core identity and cTrader token data for each onboarded slave.

| Attribute | Type | Notes |
|---|---|---|
| `appwrite_user_id` | String | Appwrite Auth UID — document owner |
| `username` | String | Chosen display name (unique index) |
| `pin_hash` | String | bcrypt hash of 4–6 digit PIN |
| `ctrader_access_token` | String | Current short-lived token |
| `ctrader_refresh_token` | String | Long-lived, stored server-side only |
| `ctrader_account_ids` | String[] | Array of `ctidTraderAccountId` values |
| `active` | Boolean | Whether account is currently being traded |
| `created_at` | DateTime | ISO timestamp |

**Permissions**: `read("user:<appwrite_user_id>")`, write granted only to server API key via Functions. The slave can read their own record but cannot write `ctrader_refresh_token` directly — only the server-side Function can.[^9]

### Collection: `trade_configs`

Per-slave trade configuration persisted and editable from the dashboard.

| Attribute | Type | Notes |
|---|---|---|
| `slave_user_id` | String | FK → `slave_accounts.$id` |
| `lot_size` | Float | Default lot size for copied trades |
| `lot_multiplier` | Float | Scale factor vs master lot (e.g., 0.5 = half size) |
| `max_daily_drawdown_pct` | Float | Kill-switch threshold (%) |
| `allowed_symbols` | String[] | Whitelist of symbols to trade (empty = all) |
| `copy_enabled` | Boolean | Master kill-switch per slave |
| `updated_at` | DateTime | Tracks last config change |

**Permissions**: `read("user:<slave_user_id>")`, `update("user:<slave_user_id>")`, `delete("user:<slave_user_id>")`. Slaves own and can edit their own config. Master uses server SDK (bypasses permissions).[^2]

### Collection: `master_signals` (Optional)

If building a signal broadcast model rather than direct copy-trading:

| Attribute | Type | Notes |
|---|---|---|
| `signal_id` | String | Unique ID |
| `symbol` | String | e.g., `EURUSD` |
| `direction` | Enum | `BUY` / `SELL` |
| `lot_size` | Float | Master's base lot |
| `sl_pips` | Integer | Stop loss in pips |
| `tp_pips` | Integer | Take profit in pips |
| `status` | Enum | `PENDING` / `EXECUTED` / `CANCELLED` |
| `created_at` | DateTime | Signal broadcast time |

**Permissions**: `read(Role.label("master"))` for create/delete, `read(Role.users("verified"))` for read — all verified slaves can subscribe to signals via Appwrite Realtime.[^3]

***

## Complete Auth & Onboarding Flow

### Step 1: Slave Registration

```
Slave visits Sites URL
  → Clicks "Connect cTrader Account"
  → Frontend: account.create(ID.unique(), email, password)
  → Appwrite creates user → returns userId
  → Frontend: calls Function `generate-ctrader-oauth-url`
     with { appwrite_userId }
  → Function returns:
     https://id.ctrader.com/my/settings/openapi/grantingaccess/
       ?client_id=YOUR_CLIENT_ID
       &redirect_uri=https://YOUR_FUNCTION_DOMAIN/callback
       &scope=trading
       &state=<appwrite_userId>   ← ties OAuth result back to Appwrite user
  → Slave is redirected to cTrader consent page
  → Slave logs in with THEIR cTID and clicks "Allow"
  → cTrader redirects to: YOUR_FUNCTION_DOMAIN/callback?code=XXX&state=<userId>
```

### Step 2: OAuth Callback (Function)

```
Function `ctrader-oauth-callback` receives GET request:
  1. Extract code + state (= appwrite_userId)
  2. POST to https://id.ctrader.com/Apps/token:
       { client_id, client_secret, code, grant_type: "authorization_code",
         redirect_uri: FUNCTION_DOMAIN }
  3. Receive { access_token, refresh_token, token_type }
  4. Connect to cTrader Open API TCP/gRPC:
       ProtoOAApplicationAuthReq(clientId, clientSecret)
       ProtoOAAccountAuthReq(access_token)
       ProtoOAGetAccountListByAccessTokenReq(access_token)
  5. Save to DB (server SDK, API key):
       databases.createDocument("slave_accounts", {
         appwrite_user_id: state,
         ctrader_access_token: access_token,
         ctrader_refresh_token: refresh_token,
         ctrader_account_ids: [...accountIds],
         active: false
       })
  6. Redirect to: SITES_URL/onboarding?success=true
```

### Step 3: Slave Sets Username + PIN

```
Slave is prompted to choose username + PIN:
  → Frontend sends { username, pin } to Function `set-credentials`
  → Function:
       1. Validates username uniqueness against DB
       2. bcrypt.hash(pin, 10) → pin_hash
       3. databases.updateDocument("slave_accounts", docId, { username, pin_hash })
       4. account.updateName(appwrite_userId, username)  [via server SDK]
  → Done — slave now has username + PIN for future logins
```

### Step 4: Subsequent Slave Login (PIN Auth)

```
Slave visits Sites → enters username + PIN
  → Frontend calls Function `pin-login`:
       { username, pin }
  → Function:
       1. Query: databases.listDocuments("slave_accounts",
            [Query.equal("username", username)])
       2. bcrypt.compare(pin, doc.pin_hash) → valid?
       3. If valid: users.createToken(doc.appwrite_user_id)
          → returns { secret, userId }
  → Frontend receives { secret, userId }
  → Frontend: account.createSession({ userId, secret })
  → Full Appwrite session established — JWT available
  → Slave can now read/write their own trade_configs document
```

### Step 5: Slave Configures Trade Settings

```
Authenticated slave (has JWT session):
  → Frontend calls databases.getDocument("trade_configs", ...)
  → Displays: lot_size, lot_multiplier, max_daily_drawdown_pct,
              allowed_symbols, copy_enabled
  → Slave edits and saves
  → Frontend calls databases.updateDocument(...)
     (permitted because doc has user-level write permission)
  → Changes are persisted immediately to Appwrite DB
```

### Step 6: Master Reads Configs & Executes Trades

```
Master's backend (running Python/Node on your VPS or Appwrite Function):
  1. Queries all slave_accounts where active=true
  2. For each slave, refreshes accessToken if needed
  3. Reads their trade_config (lot_multiplier, allowed_symbols, etc.)
  4. On master signal / trigger:
       a. ProtoOAApplicationAuthReq (your app credentials)
       b. For each slave:
            ProtoOAAccountAuthReq(slave.ctrader_access_token)
            ProtoOANewOrderReq(scaled by lot_multiplier)
  5. Updates master_signals doc status to EXECUTED
```

***

## Security Design Considerations

| Concern | Mitigation |
|---|---|
| `refresh_token` exposure | Stored only in Appwrite DB server-side; never returned to client SDK; accessible only via API key-authenticated Functions[^2] |
| PIN brute force | Appwrite Functions can enforce rate-limiting; lock account after N failures (update `active: false` via server SDK) |
| Slave modifying another slave's config | Document-level permissions: each `trade_configs` doc is `write("user:<userId>")` — Appwrite enforces this[^9] |
| Master operations from browser | Master uses a separate admin Function endpoint authenticated by a long-lived API key (not a client session) |
| cTrader token expiry | Scheduled cron Function handles token refresh before the access token expires[^5] |
| Scope creep | OAuth scope set to `trading` — this is the minimum required; avoid storing broker passwords anywhere |

***

## Appwrite Function Environment Variables

Store all secrets as Function environment variables (not in DB or code):[^5]

```
CTRADER_CLIENT_ID=your_client_id
CTRADER_CLIENT_SECRET=your_client_secret
CTRADER_REDIRECT_URI=https://YOUR_FUNCTION_DOMAIN/callback
APPWRITE_PROJECT_ID=your_project_id
APPWRITE_API_KEY=your_server_api_key
APPWRITE_DATABASE_ID=main_db_id
BCRYPT_SALT_ROUNDS=10
```

***

## Deployment Checklist

1. **Activate Education Plan**: Verify GitHub Student Pack → connect to Appwrite Cloud → Education plan auto-applies[^6]
2. **Create Appwrite Project**: Single project (`ctrader-copytrade`) — counts as 1 of your 2 Education plan projects[^7]
3. **Deploy Sites**: Connect GitHub repo → Appwrite Sites auto-deploys on push[^5]
4. **Deploy Functions**: `ctrader-oauth-callback`, `pin-login`, `set-credentials`, `generate-oauth-url`, `token-refresh-worker` (scheduled cron)[^5]
5. **Register Function domain as cTrader redirect URI**: Copy the auto-generated Function domain from Appwrite Console → paste into `openapi.ctrader.com` app settings[^11]
6. **Create DB collections**: `slave_accounts`, `trade_configs`, `master_signals` with attributes and permissions as designed above[^10]
7. **Label master user**: After creating master account, use server SDK `users.updateLabels(masterId, ['master'])` to gate master-only routes[^9]
8. **Test OAuth flow end-to-end**: Register a slave test account → complete consent → verify token stored in DB → verify trade config persists

---

## References

1. [Education - Appwrite](https://appwrite.io/education) - Students, here's your chance to expand your skillset without spending a penny. Sign up for Appwrite ...

2. [Databases API Reference - Docs - Appwrite](https://appwrite.io/docs/references/cloud/client-rest/databases) - The Databases service allows you to create structured collection of documents, query and filter list...

3. [App and account authentication - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/account-authentication/) - The cTrader Open API authentication process is based on the OAuth 2.0 standard. This framework allow...

4. [Getting started - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/) - The documentation for cTrader Open API

5. [Plugin SDK vs Open API - cTrader Help Centre](https://help.ctrader.com/ctrader-algo/documentation/plugins/sdk-vs-open-api/) - The documentation for cTrader Algo

6. [The Appwrite Education ...](https://appwrite.io/blog/post/announcing-appwrite-education-program) - Appwrite partners up with GitHub for the Appwrite Education program. Together we enable future devel...

7. [Appwrite for Education - Threads](https://appwrite.io/threads/1490774476315558029) - This support thread seems to be related to using Appwrite for educational purposes. The user may hav...

8. [Welcome Appwrite to the Student Developer Pack! #144347](https://github.com/orgs/community/discussions/144347) - We're happy to share that Appwrite is now a part of the Student Developer Pack! Appwrite is an open-...

9. [Database permissions - Docs](https://appwrite.io/docs/products/databases/legacy/permissions) - Enhance data security and access control with Appwrite Database Permissions. Learn how to set permis...

10. [Collections - Docs - Appwrite](https://appwrite.io/docs/products/databases/collections) - Organize your data with Appwrite Collections. Explore how to create and configure collections to sto...

11. [Register an application - Open API - cTrader Help Centre](https://help.ctrader.com/open-api/api-application/) - The documentation for cTrader Open API

