# SSFX / slwp Identity & Access Reference

> **Date:** 2026-07-03 (post-consolidation)
>
> This is a concise operational reference for authentication components, endpoints,
> and tokens. The full architecture lives in `docs/ARCHITECTURE.md`.

---

## Authentication Paths

### 1. Primary: cTrader OAuth2

```
User → SPA → GET /auth/ctrader/start → cTrader OAuth
→ GET /callback → state verify → token exchange
→ Appwrite user creation → slave_accounts upsert
→ session cookie → dashboard
```

- State token: HMAC-signed, single-use, 10-minute TTL, stored in `ephemeral_tokens`.
- Tokens at rest: AES-GCM-256 encrypted in `slave_accounts`.
- Session cookie: `a_session_<PROJECT_ID>`, HTTP-only, Secure, SameSite=Lax.

### 2. Secondary: Username + PIN

```
User → SPA → POST /pin-login → verify username + BCrypt pin_hash
→ Appwrite session → cookie → dashboard
```

- PIN setup after OAuth: `POST /set-credentials`.
- PIN reset: `POST /pin-reset/request` and `POST /pin-reset/confirm` via `ephemeral_tokens` + Resend.
- Master access uses username `admin`; role is validated from `service_config` (`master_auth`).

### 3. Background: Server-to-server token refresh

```
Python service → POST /internal/ctrader/refresh (x-internal-key)
→ grant_locks → decrypt → cTrader refresh → encrypt → store
→ return access_token
```

---

## Functions & Endpoints

| Function | Domain | Method | Path | Purpose |
|---|---|---|---|---|
| `auth-oauth` | `auth.mrme.tech` | GET | `/auth/ctrader/start` | Start OAuth2 flow |
| `auth-oauth` | `auth.mrme.tech` | GET | `/callback` | OAuth2 callback |
| `auth-oauth` | `auth.mrme.tech` | GET | `/session` | Check current session |
| `auth-oauth` | `auth.mrme.tech` | POST | `/logout` | Clear session |
| `auth-oauth` | `auth.mrme.tech` | GET | `/admin/slaves` | List all slaves (master only) |
| `auth-pin` | `pin.mrme.tech` | POST | `/pin-login` | Login with username + PIN |
| `auth-pin` | `pin.mrme.tech` | POST | `/set-credentials` | Set username + PIN |
| `auth-pin` | `pin.mrme.tech` | POST | `/pin-reset/request` | Request PIN reset email |
| `auth-pin` | `pin.mrme.tech` | POST | `/pin-reset/confirm` | Confirm PIN reset |
| `api-internal` | internal | POST | `/internal/ctrader/refresh` | Refresh cTrader access token |
| `api-internal` | internal | GET | `/internal/grant/latest` | Latest grant for user |
| `api-internal` | internal | POST | `/internal/grant/:grant_id/accounts` | Persist discovered accounts |
| `api-internal` | internal | GET | `/internal/grant/:grant_id/accounts` | Get accounts for grant |
| `token-refresh` | scheduled | — | `/` (HTTP) / cron | Sweep near-expiry tokens + stale ephemerals |

> **Note:** `auth-oauth`, `auth-pin`, `api-internal`, and `token-refresh` are the
> canonical function names. Legacy deployed names (`ctrader-auth`, `ctrader-pin-auth`,
> `ctrader-internal`, `ctrader-token-refresh-worker`) will be retired on next deploy.

---

## Identity Tables

| Table | Purpose | Key Fields |
|---|---|---|
| `slave_accounts` | User identity + encrypted tokens | `appwrite_user_id`, `grant_id`, `username`, `pin_hash`, `access_token_enc`, `refresh_token_enc`, `access_token_expires_at` |
| `ephemeral_tokens` | Short-lived tokens | `token_type`, `token_data`, `expires_at` |
| `grant_locks` | Distributed refresh locks | `$id` (grant_id), `locked_until` |
| `service_config` | System config | `config_key`, `config_value` (`master_auth`, `ctrader_oauth`) |

---

## Token Reference

| Token | Purpose | Lifetime | Storage |
|---|---|---|---|
| cTrader access token | API calls | 1 hour | AES-GCM-256 encrypted in `slave_accounts` |
| cTrader refresh token | Renew access token | 30 days | AES-GCM-256 encrypted in `slave_accounts` |
| OAuth state | CSRF protection | 10 minutes | `ephemeral_tokens` |
| PIN reset token | Password recovery | 15 minutes | `ephemeral_tokens` |
| Session cookie | Browser session | 24 hours | Cookie (`a_session_<PROJECT_ID>`) |

---

## Security Checklist

- [x] OAuth state tokens are HMAC-signed, single-use, and TTL-limited.
- [x] CTrader tokens are encrypted at rest with `TOKEN_ENCRYPTION_KEY`.
- [x] Session cookies are HTTP-only, Secure, and SameSite=Lax.
- [x] PIN hashes use BCrypt (`BCRYPT_SALT_ROUNDS` ≥ 12).
- [x] Internal endpoints require `x-internal-key`.
- [x] Admin endpoints require `x-admin-key` or master role validation.
- [x] Token refresh uses row-level `grant_locks` to prevent races.
- [x] Sensitive tokens/PINs are never logged.

---

## Account Discovery & Sync Process

### AccountHub v2 Architecture

```
AccountHubV2 → AccountDiscovery → Appwrite slave_accounts
                    ↓
          EnvironmentConnection (live/demo)
                    ↓
          authorize_account() → sync_accounts_to_broker()
                    ↓
          POST /internal/grant/:grant_id/accounts
                    ↓
          Appwrite accounts table
```

### Key Components

1. **AccountDiscovery**: Polls `slave_accounts` table for active slaves and discovers their cTrader accounts.
2. **EnvironmentConnection**: Shared transport per environment (live/demo) with multi-account authorization.
3. **Account Sync**: After authorization, calls `sync_accounts_to_broker()` to persist account details.

### Troubleshooting

**Issue: Accounts not showing in dashboard**
- Check AccountHubV2 logs for sync errors.
- Verify `/internal/grant/:grant_id/accounts` endpoint is accessible.
- Confirm `accounts` table has data for the grant_id.
- Check that `ctrader_account_ids` field is populated in `slave_accounts`.

---

## See Also

- `docs/ARCHITECTURE.md` — full platform architecture.
- `docs/NAMING_AND_CONSOLIDATION_OVERHAUL.md` — rename decisions and migration order.
- `AGENTS.md` — operations, conventions, and project context.
- `docs/account-hub-and-dataservice.md` — AccountHub v2 and DataService integration details.
