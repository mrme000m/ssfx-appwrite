<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# how are reatime changes in slave accounts like orderfilled and position updates or reconciliations be received, would i need to run seperate instances with ws connected to ctrader for each ctid (ctrader account) for all trading accounts belonging to a ctrader accountor one ws connection using the master app creds will also be able receive eAch slave accounts reconsiliations

Now I have a complete picture. Here's the direct answer:

## Short Answer: **One connection handles all slave accounts — no separate instances needed**

The cTrader Open API uses a fully **multiplexed, single-connection model**. Every event coming back on the socket carries a `ctidTraderAccountId` field, so you demultiplex in your message handler, not at the connection level.

***

## The Two-Connection Rule

The official guidance is strict and simple:

> **At most 2 connections: one for demo accounts, one for live accounts.**

Each connection can hold an **unlimited number of authorized accounts**. This means your single VPS process authenticates your app once, then fires `ProtoOAAccountAuthReq` for every slave `ctidTraderAccountId` — all over the same socket. The connection is owned by your **app credentials**, not by any individual user.

***

## How Realtime Events Are Delivered

Every pushed event carries `ctidTraderAccountId` so you always know which slave it belongs to:


| Event | What triggers it | Has `ctidTraderAccountId`? |
| :-- | :-- | :-- |
| `ProtoOAExecutionEvent` | Order accepted, filled, amended, closed, deposit/withdrawal | ✅ Yes |
| `ProtoOAOrderErrorEvent` | Order rejection | ✅ Yes |
| `ProtoOAMarginChangedEvent` | Position margin change | ✅ Yes |
| `ProtoOAMarginCallTriggerEvent` | Margin level hits threshold | ✅ Yes |
| `ProtoOAAccountDisconnectEvent` | Server drops a specific account session | ✅ Yes |
| `ProtoOAAccountsTokenInvalidatedEvent` | Token refreshed/revoked — contains a **list** of account IDs affected | ✅ Repeated field |

For reconciliation specifically, you call `ProtoOAReconcileReq` per account after auth — the response `ProtoOAReconcileRes` returns all open positions and pending orders for that `ctidTraderAccountId`.

***

## Your Message Handler Pattern

Since everything comes over one socket, your dispatcher needs to route by `ctidTraderAccountId`:

```python
def on_message(msg):
    account_id = msg.ctidTraderAccountId
    msg_type   = msg.payloadType

    if msg_type == ProtoOAPayloadType.PROTO_OA_EXECUTION_EVENT:
        handle_execution(account_id, msg)   # order fill, SL hit, etc.
    elif msg_type == ProtoOAPayloadType.PROTO_OA_ORDER_ERROR_EVENT:
        handle_order_error(account_id, msg)
    elif msg_type == ProtoOAPayloadType.PROTO_OA_ACCOUNT_DISCONNECT_EVENT:
        reauth_account(account_id)          # re-send ProtoOAAccountAuthReq
```


***

## The Real Caveat: `ProtoOAAccountDisconnectEvent`

There is a known production issue: if **any one slave account** gets disconnected (e.g., the slave logs into cTrader Desktop and then logs out), the `ProtoOAAccountDisconnectEvent` fires for that account — but in some cases **the entire connection becomes unresponsive** (can still receive but not send). Spotware has confirmed this bug.

The community workaround is to **group accounts by cTID** (one connection per `accessToken`/user, not per account) rather than piling every slave onto a single socket. So the practical architecture for robustness is:

```
Live Connection Pool:
  ├── Connection A  → Slave User 1's accessToken → accounts [101, 102]
  ├── Connection B  → Slave User 2's accessToken → accounts [^201]
  ├── Connection C  → Slave User 3's accessToken → accounts [301, 302, 303]
  └── ...

Demo Connection Pool:
  └── Connection X  → all demo accounts (usually just yours for testing)
```

This way a disconnect bug for one user only kills that user's connection, not all slaves. You still stay well within the "at most 2 connections" guideline if all your slaves are live-only — that rule applies to demo vs. live, not per-user. Running one TCP connection per user's access token on the **live endpoint** is fine.

***

## Rate Limits Note

Rate limits apply **per connection**, not per account. So if you pile 50 slave accounts onto one connection and they all trade simultaneously, you share one rate-limit bucket. Splitting by user/cTID naturally distributes this load as well.
<span style="display:none">[^1][^10][^11][^12][^13][^14][^15][^16][^17][^18][^19][^2][^20][^21][^22][^23][^24][^25][^26][^3][^4][^5][^6][^7][^8][^9]</span>

<div align="center">⁂</div>

[^1]: https://help.ctrader.com/open-api/connection/

[^2]: https://community.ctrader.com/forum/connect-api-support/42973/

[^3]: https://help.ctrader.com/open-api/proxies-endpoints/

[^4]: https://pkg.go.dev/github.com/linuskuehnle/ctrader-openapi

[^5]: https://ctrader.jp/forum/connect-api-support/37539/

[^6]: https://community.ctrader.com/forum/connect-api-support/46599/

[^7]: https://help.ctrader.com/open-api/

[^8]: https://pkg.go.dev/github.com/fxnity/ctrader/openapi

[^9]: https://www.ctrader.jp/forum/connect-api-support/45671/

[^10]: https://github.com/spotware/OpenApiPy/blob/main/ctrader_open_api/messages/OpenApiMessages_pb2.py

[^11]: https://community.ctrader.com/forum/connect-api-support/24222/

[^12]: https://community.ctrader.com/forum/connect-api-support/46068/

[^13]: https://community.ctrader.com/forum/fix-api/21582/

[^14]: https://community.ctrader.com/forum/connect-api-support/13973/

[^15]: https://help.ctrader.com/open-api/model-messages/

[^16]: https://help.ctrader.com/open-api/messages/

[^17]: https://www.ctrader.jp/forum/connect-api-support/37849/

[^18]: https://community.ctrader.com/forum/ctrader-algo/23190/

[^19]: https://community.ctrader.com/forum/connect-api-support/37438/

[^20]: https://community.ctrader.com/forum/connect-api-support/41177/

[^21]: https://help.ctrader.com/ctrader-ai-agent-connect/api/reference/

[^22]: https://communityuat.ctrader.com/forum/connect-api-support/41839

[^23]: https://pkg.go.dev/github.com/diegobernardes/ctrader/openapi

[^24]: https://ctrader.jp/forum/connect-api-support/25196/

[^25]: https://help.ctrader.com/open-api/account-authentication/

[^26]: https://help.ctrader.com/ctrader-algo/how-tos/cbots/cbot-trading-operations/

