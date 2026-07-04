# Signal Pipeline Review - 2026-07-04

## Executive Summary

The signal pipeline from alwaydata Telegram forwarder to SSFX trading runtime has been reviewed. The architecture is sound with robust filtering, multiple reception paths, and graceful degradation. However, several configuration gaps and classification issues were identified.

## Architecture Overview

### Upstream (alwaydata)
- **SignalParser**: Classifies messages into 7 categories (ENTRY, ENTRY_PENDING, MANAGE, RESULT, PROMO, NOISE, UNKNOWN)
- **Forwarding logic**: Only forwards ENTRY and ENTRY_PENDING signals
- **Price augmentation**: Only for high-confidence entry signals
- **Webhook support**: Optional direct HTTP push to bypass Telegram

### Downstream (SSFX Server)
- **Dual reception**: Telegram Bot API webhook + direct HTTP webhook
- **Storage**: Appwrite TablesDB primary with SQLite fallback
- **Signal experience**: Quality scoring, author tracking, LLM context
- **Factory pattern**: Automatic fallback between storage backends

## Identified Issues

### 1. Signal Classification Gaps
- **ENTRY_PENDING detection**: Regex pattern doesn't handle dashes or special characters (e.g., "XAUUSD SELL LIMIT - 4700")
- **Promo detection**: Requires 2+ advert words, may miss single-word promos
- **MANAGE regex**: Possibly too broad, catching some NOISE signals

### 2. Configuration Gaps
- **Missing SIGNAL_WEBHOOK_SECRET**: Not configured in `remote-services/config/v2.env`
- **Missing SIGNAL_WEBHOOK_URL**: Not configured in alwaydata `.env`
- **Direct webhook path**: Not fully tested end-to-end

### 3. Testing Gaps
- No comprehensive integration tests for complete pipeline
- Limited testing of fallback scenarios (Appwrite → SQLite)
- No monitoring of classification accuracy in production

## Pipeline Strengths

1. **Robust filtering**: Promos filtered upstream, reducing downstream noise
2. **Multiple reception paths**: Telegram webhook + direct HTTP push
3. **Graceful degradation**: SQLite fallback when Appwrite unavailable
4. **Security**: HMAC verification, admin API key protection
5. **Signal intelligence**: Experience scoring, LLM context building

## Recommendations

### Immediate Actions (High Priority)
1. **Fix signal parser regex** to handle edge cases in ENTRY_PENDING detection
2. **Configure webhook secrets** in both alwaydata and SSFX server configs
3. **Add integration tests** for complete pipeline flow

### Medium-term Improvements
4. **Monitor classification**: Log and analyze signal classification results
5. **Enhance testing**: Add more edge case tests for signal parsing
6. **Document edge cases**: Update documentation with real-world examples

### Long-term Enhancements
7. **Add metrics**: Track pipeline latency, classification accuracy, delivery rates
8. **Improve resilience**: Add retry logic for failed webhook deliveries
9. **Enhance monitoring**: Dashboard for pipeline health and signal flow

## Test Results

### Signal Store Tests
- ✅ Signal store fallback tests pass (Appwrite → SQLite → NoOp)
- ✅ Signal experience factory tests pass
- ✅ Admin API authentication tests pass

### Signal Classification Tests
- ⚠️ ENTRY detection: Working correctly
- ⚠️ ENTRY_PENDING detection: Issues with special characters
- ⚠️ PROMO detection: May be too strict
- ⚠️ MANAGE/NOISE classification: Some misclassifications

### Webhook Security Tests
- ✅ HMAC signature verification logic works correctly
- ✅ Wrong secrets correctly rejected

## Configuration Checklist

### alwaydata (.env)
- [ ] `SIGNAL_WEBHOOK_URL=https://ssfx-api.mrme.tech/api/signals/webhook`
- [ ] `SIGNAL_WEBHOOK_SECRET=<shared-secret>`

### SSFX Server (v2.env)
- [ ] `SIGNAL_WEBHOOK_SECRET=<shared-secret>`
- [ ] `TELEGRAM_WEBHOOK_SECRET_TOKEN` ✓ (configured)
- [ ] `ADMIN_API_KEY` ✓ (configured)

## Risk Assessment

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Signal misclassification | Medium | Medium | Fix regex patterns, add monitoring |
| Webhook delivery failure | High | Low | Retry logic, fallback to Telegram |
| Storage backend failure | Low | Low | SQLite fallback already implemented |
| Security breach | High | Low | HMAC verification, API key protection |

## Conclusion

The signal pipeline architecture is well-designed with multiple layers of resilience. The main issues are configuration gaps and some edge cases in signal classification. Addressing these will make the pipeline production-ready.

**Next steps**: Fix signal parser regex, configure webhook secrets, add integration tests.