# SSFX / slwp Identity & Access Reference

> **Consolidation note:** The full architecture — including identity as a plane, data flows, deployment topology, and naming overhaul — is now in `docs/ARCHITECTURE.md`. This document is a concise operational reference for authentication components, endpoints, and tokens.

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
| `ctrader-auth` | `auth.mrme.tech` | GET | `/auth/ctrader/start` | Start OAuth2 flow |
| `ctrader-auth` | `auth.mrme.tech` | GET | `/callback` | OAuth2 callback |
| `ctrader-auth` | `auth.mrme.tech` | GET | `/session` | Check current session |
| `ctrader-auth` | `auth.mrme.tech` | POST | `/logout` | Clear session |
| `ctrader-auth` | `auth.mrme.tech` | GET | `/admin/slaves` | List all slaves (master only) |
| `ctrader-pin-auth` | `pin.mrme.tech` | POST | `/pin-login` | Login with username + PIN |
| `ctrader-pin-auth` | `pin.mrme.tech` | POST | `/set-credentials` | Set username + PIN |
| `ctrader-pin-auth` | `pin.mrme.tech` | POST | `/pin-reset/request` | Request PIN reset email |
| `ctrader-pin-auth` | `pin.mrme.tech` | POST | `/pin-reset/confirm` | Confirm PIN reset |
| `ctrader-internal` | internal | POST | `/internal/ctrader/refresh` | Refresh cTrader access token |
| `ctrader-internal` | internal | GET | `/internal/grant/latest` | Latest grant for user |
| `ctrader-internal` | internal | POST | `/internal/grant/:grant_id/accounts` | Persist discovered accounts |
| `ctrader-internal` | internal | GET | `/internal/grant/:grant_id/accounts` | Get accounts for grant |
| `ctrader-token-refresh-worker` | scheduled | — | `/` (HTTP) / cron | Sweep near-expiry tokens + stale ephemerals |

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

- [ ] OAuth state tokens are HMAC-signed, single-use, and TTL-limited.
- [ ] CTrader tokens are encrypted at rest with `TOKEN_ENCRYPTION_KEY`.
- [ ] Session cookies are HTTP-only, Secure, and SameSite=Lax.
- [ ] PIN hashes use BCrypt (`BCRYPT_SALT_ROUNDS` ≥ 12).
- [ ] Internal endpoints require `x-internal-key`.
- [ ] Admin endpoints require `x-admin-key` or master role validation.
- [ ] Token refresh uses row-level `grant_locks` to prevent races.
- [ ] Sensitive tokens/PINs are never logged.

---

## Proposed Renames

The canonical rename matrix lives in `docs/NAMING_AND_CONSOLIDATION_OVERHAUL.md`. Identity-specific highlights:

| Current | Proposed | Note |
|---|---|---|
| `slave_accounts` | `users` | Appwrite `users` is the real identity; this table becomes the cTrader grant/profile extension. |
| `pin.mrme.tech` | merge into `auth.mrme.tech` | Paths such as `/auth/pin/login`, `/oauth/callback`. |
| `ctrader-auth` | `auth-oauth` | Clearer function responsibility. |
| `ctrader-pin-auth` | `auth-pin` | Sibling to `auth-oauth`. |
| `ctrader-internal` | `api-internal` | Generic server-to-server API. |

---

## See Also

- `docs/ARCHITECTURE.md` — full platform architecture and Appwrite Cloud optimization patterns.
- `docs/NAMING_AND_CONSOLIDATION_OVERHAUL.md` — rename matrix, consolidation decisions, and migration order.
- `AGENTS.md` — operations, conventions, and project context.
