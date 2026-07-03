# Remote-Services Redundancy & Discrepancy Audit

Date: 2026-07-03
Scope: `/Volumes/ExMac/code/ssfx/appwrite-auth-consolidated/remote-services/`

## Summary

After the consolidation phases (A–F) + this audit pass, the codebase is significantly cleaner.

**Fixed in audit pass:**
- 4 inline `Client()` constructions → `shared.appwrite_client.create_appwrite_client()`
- `datetime.utcnow()` → `datetime.now(UTC)` in signal_experience
- 2 local `AppwriteException` imports → moved to top-level

**Remaining:**
- `market_data_service/appwrite_config.py:_get_client()` — intentionally kept (lazy-import
  safety for dev scripts + MARKET_DATA_* env var handling)
- Package-specific `_env()`, `logging.basicConfig()`, dotenv loading — acceptable variation

---

## 🔴 Safe to Fix Now

### 1. Inline `Client()` constructions bypassing `shared.appwrite_client`

**5 files** still build `appwrite.Client` inline instead of using the shared factory.

| File | Lines | Pattern |
|------|-------|---------|
| `market_data_service/appwrite_database.py` | 359–362 | `Client().set_endpoint(...).set_project(...).set_key(...)` inside async `_init()` |
| `market_data_service/feed_manager_appwrite.py` | 97–100 | Same chained pattern |
| `market_data_service/signal_experience/store.py` | 48–51 | Same pattern in `_connect()` |
| `market_data_service/appwrite_config.py` | 86–87 | `_get_client()` helper duplicates shared factory |
| `ctrader/account_hub_server.py` | 35–38 | Separate `set_*` calls |

**Impact:** Token endpoint/project/key configured in 6+ places (including `shared/appwrite_client.py`). Changing one env var mapping requires hunting across files.

**Fix:** Replace with `create_appwrite_client(endpoint, project_id, api_key)` from `shared.appwrite_client`. Note: `appwrite_config.py` lazy-imports appwrite for dev-script usage — keep that safety but delegate client construction.

---

### 2. Deprecated `datetime.utcnow()` in signal_experience

**File:** `market_data_service/signal_experience/models.py:259`

```python
def _iso_now() -> str:
    return datetime.utcnow().isoformat() + "Z"   # Deprecated in Python 3.12
```

**Impact:** Will emit `DeprecationWarning` on Python 3.12+.

**Fix:** `return datetime.now(UTC).isoformat()`

---

### 3. Local imports of `AppwriteException` inside functions

**File:** `market_data_service/appwrite_database.py`

```python
def _ensure_database(self) -> None:
    from appwrite.exception import AppwriteException  # ← imported locally, line 390
    ...

def _ensure_tables(self) -> None:
    from appwrite.exception import AppwriteException  # ← imported locally, line 411
    ...
```

**Why it exists:** Likely left over from a time when the appwrite SDK was optional. It is now a hard dependency.

**Fix:** Move to top-level import. `signal_experience/store.py` and `ctrader/account_events_persister.py` already do this correctly.

---

### 4. `market_data_service/appwrite_config.py` duplicates shared factory logic

**File:** `market_data_service/appwrite_config.py`

- `_require_appwrite()` lazy-imports `Client, TablesDB` — **justified** (dev scripts may not have appwrite installed)
- `_get_client()` builds a Client from env/settings — **redundant** with `shared.appwrite_client.create_appwrite_client()`
- `_get_tables()` wraps `_get_client()` — **redundant**

**Fix:** Make `_get_client()` delegate to `create_appwrite_client()` while keeping `_require_appwrite()` for lazy-import safety.

---

## 🟡 Acceptable Variation (Do Not Change)

### 5. Multiple `_env()` helper functions

- `ssfx_server/config_loader.py:_env()` — internal helper
- `ctrader_cli/config.py:_env()` — CLI-specific with different defaults

**Verdict:** Each package owns its config. Unifying adds coupling for marginal gain.

### 6. Multiple `logging.basicConfig()` calls

11 files call `logging.basicConfig()`. Each service has its own startup entry point and needs independent log configuration.

**Verdict:** Correct — each service is independently runnable.

### 7. Multiple `_row_to_dict` / `_strip` helpers

- `market_data_service/database.py:_row_to_dict()` — converts `sqlite3.Row` to dict
- `market_data_service/appwrite_database.py:_strip_appwrite_meta()` — strips Appwrite `$id`, `$collectionId`, etc.
- `market_data_service/signal_experience/store.py:_strip()` — same as above
- `market_data_service/appwrite_config.py:_row_to_dict()` — same as above

**Verdict:** Two distinct purposes (SQLite vs Appwrite). The Appwrite ones could theoretically be unified, but they live in different architectural layers (database backend vs config sync). Low value.

### 8. Multiple dotenv loading patterns

- `ssfx_server/config_loader.py` — loads `.env` then `default.env`
- `ctrader/config.py` — loads single `ENV_FILE`
- `ctrader_cli/config.py` — searches parent dirs for `.env`

**Verdict:** Each package has different deployment context. CLI searches upwards for convenience; services load explicit paths.

### 9. Service-specific `HealthResponse` models

- `agent_harness/models.py:HealthResponse`
- `market_data_service/api_models.py:HealthResponse`

**Verdict:** Each service owns its API contract. Unifying would create an artificial cross-service dependency.

---

## Status

✅ **FIXED** — All safe items (1–4) resolved in commit following this audit.

🟡 **ACCEPTED** — Items 5–9 are package-specific variations with legitimate reasons to exist.
