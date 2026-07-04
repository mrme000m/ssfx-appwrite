# Upstream/Downstream Alignment Report

**Date:** 2026-07-04  
**Upstream:** `/Volumes/ExMac/code/ssfx/alwaydata` (alwaysdata free tier)  
**Downstream:** `/Volumes/ExMac/code/ssfx/appwrite-auth-consolidated` (AWS VM + Appwrite)

---

## ✅ Aligned Components

### 1. Signal Webhook Payload
| Aspect | Upstream (`signal_processor.py`) | Downstream (`admin_api.py`) | Status |
|--------|----------------------------------|----------------------------|--------|
| Content-Type | `application/json` | Expected JSON | ✅ |
| Payload wrapper | `{"event": "signal", "snapshot": {...}}` | Reads `body["snapshot"]` | ✅ |
| Signal text | `snapshot.signal_text` | `snapshot.get("signal_text")` or `"raw_text"` | ✅ |
| Chat ID | `snapshot.source_chat_id` | `snapshot.get("source_chat_id")` or `"chat_id"` | ✅ |
| Message ID | `snapshot.source_message_id` | `snapshot.get("source_message_id")` or `"message_id"` | ✅ |
| Reply-to | `snapshot.reply_to_message_id` | `snapshot.get("reply_to_message_id")` | ✅ |
| Symbol | `snapshot.symbol` | Extracts from snapshot or regex-falls back to text | ✅ |

### 2. HMAC Signature Verification
| Aspect | Upstream | Downstream | Status |
|--------|----------|------------|--------|
| Header | `X-Signal-Signature` | `X-Signal-Signature` | ✅ |
| Format | `sha256=<hex>` | `sha256=<hex>` | ✅ |
| Algorithm | HMAC-SHA256 over JSON body | HMAC-SHA256 over JSON body | ✅ |
| Comparison | urllib request | `secrets.compare_digest()` | ✅ |

### 3. cTrader Auth Broker Protocol
| Aspect | Upstream (`ctrader_spot_client.py`) | Downstream (`api-internal` function) | Status |
|--------|--------------------------------------|-------------------------------------|--------|
| Endpoint | `POST /internal/ctrader/refresh` | `POST /internal/ctrader/refresh` | ✅ |
| Body | `{"grantId": "..."}` | Accepts `grantId` or `grant_id` | ✅ |
| Auth header | `x-internal-key` | `x-internal-key` | ✅ |
| Response | `{access_token, expires_at, refreshed}` | `{access_token, expires_at, refreshed}` | ✅ |
| Fallback chain | broker → refresh_token → web-login | N/A (broker is downstream) | ✅ |

### 4. Signal Classification
| Aspect | Upstream (`signal_parser.py`) | Downstream (`ssfx_parser`) | Status |
|--------|-------------------------------|---------------------------|--------|
| Entry filter | Only forwards `ENTRY`, `ENTRY_PENDING` | Parser handles `NEW`, `UPDATE`, `CLOSE` | ✅ |
| Promo filter | `is_promo()` blocks before forwarding | Intent agent classifies as `noise` | ✅ |
| SL/TP requirement |Required for `is_high_confidence_entry()`| Parsed into signal model | ✅ |

---

## 🔧 Fixes Applied This Session

### 1. `v2.env` — `SLAVE_ACCOUNTS_TABLE` corrected
- **File:** `remote-services/config/v2.env`
- **Change:** `slave_accounts` → `users`
- **Reason:** Appwrite table ID is `users` (display name "Slave Accounts"). The Python runtime defaults to `"users"`, but `v2.env` was overriding it with a non-existent table name.
- **Impact:** Account Hub v2 and Account Discovery can now find active slaves.

### 2. Appwrite Functions — redeployed current repo code to legacy function IDs
- **Problem:** Custom domains (`auth.mrme.tech`, `pin.mrme.tech`, `internal.mrme.tech`, `refresh.mrme.tech`) were pointing to old functions (`ctrader-*`) with stale code, not the current repo functions (`auth-oauth`, `auth-pin`, `api-internal`, `token-refresh`).
- **Fix:** Deployed current `functions/` code directly to the legacy Appwrite function IDs that own the custom domains:
  - `functions/auth-oauth/` → `ctrader-auth`
  - `functions/auth-pin/` → `ctrader-pin-auth`
  - `functions/api-internal/` → `ctrader-internal`
  - `functions/token-refresh/` → `ctrader-token-refresh-worker`
- **Verification:**
  - `https://auth.mrme.tech/health` → `{"status":"ok","service":"auth-oauth"}`
  - `https://pin.mrme.tech/health` → `{"status":"ok","service":"auth-pin"}`
  - `https://internal.mrme.tech/health` → `{"status":"ok","service":"api-internal"}`
  - `https://refresh.mrme.tech/health` → `200 OK`

### 3. `api-internal` function variables — secrets populated
- **Problem:** `ctrader-internal` had empty `APPWRITE_API_KEY`, `INTERNAL_API_KEY`, `CTRADER_CLIENT_ID`, `CTRADER_CLIENT_SECRET`, `TOKEN_ENCRYPTION_KEY`.
- **Fix:** Upserted all required secrets from `.env`.
- **Result:** Token refresh endpoint now correctly queries `slwp_platform.users` table.

### 4. Alwaydata `.env` — grant_id and fallback credentials updated
- **File:** `/Volumes/ExMac/code/ssfx/alwaydata/.env`
- **Changes:**
  - `CTRADER_AUTH_GRANT_ID` → `6aa46e48c4e59b5ab98770a00af10271` (valid `user00` grant in `users` table)
  - `CTRADER_WEB_USERNAME` → `1mjkaiden`
  - `CTRADER_WEB_PASSWORD` → `#1Mbugua`
- **Note:** The grant has no cTrader refresh token yet (user hasn't completed OAuth). The upstream spot client will fall back to web-login with the demo credentials.

---

## ⚠️ Remaining Items

### 1. Complete cTrader OAuth for `user00`
- **Status:** `user00` exists in `users` table with grant `6aa46e48...` but `refresh_token_enc` is empty.
- **Impact:** `POST /internal/ctrader/refresh` returns `{"error":"No refresh token available"}`.
- **Fix:** Run the web auth flow for demo account:
  ```bash
  cd /Volumes/ExMac/code/ssfx/appwrite-auth-consolidated
  python3 dev/scripts/ctrader_web_auth.py --store-appwrite user00
  ```
  Or complete OAuth through the browser at `https://auth.mrme.tech/auth/ctrader/start`.

### 2. Redeploy downstream VM runtime
- **Status:** `v2.env` was updated locally.
- **Action:** The updated `v2.env` needs to be rsynced to the AWS VM and the container restarted.
  ```bash
  cd /Volumes/ExMac/code/ssfx/appwrite-auth-consolidated
  ./dev.sh deploy-remote
  # or manually:
  rsync -avz --delete remote-services/config/v2.env aws-ssfx:/home/ec2-user/ssfx-remote-services/config/
  ssh aws-ssfx 'cd /home/ec2-user/ssfx-remote-services && docker compose up -d --force-recreate'
  ```

### 3. Restart alwaydata upstream
- **Status:** `.env` updated locally.
- **Action:** Push the updated `.env` to alwaysdata and restart the service:
  ```bash
  cd /Volumes/ExMac/code/ssfx/alwaydata
  ./deploy.sh
  # or ssh cpr00@cpr00.alwaysdata.net && supervisorctl restart tg-forwarder
  ```

---

## Architecture Note: Table Naming

The Appwrite schema uses table ID `users` with display name "Slave Accounts". All function code references `tableId: 'users'`, which is correct. The `slave_accounts` table does **not** exist in `slwp_platform`. Some Python init scripts and `v2.env` historically used `slave_accounts`, but the actual runtime code defaults to `"users"` which is the correct value.

| Name in Appwrite Console | Table ID | Used By |
|--------------------------|----------|---------|
| Slave Accounts | `users` | Auth functions, api-internal, token-refresh |
| SSFX Accounts | `signal_slaves` | ssfx-server account store, trading config |
| cTrader Accounts | `ctrader_accounts` | Account Hub v2 account discovery |
| SSFX Executions | `ssfx_executions` | Execution history |
| Account Events | `account_state_history` | Real-time account state |
