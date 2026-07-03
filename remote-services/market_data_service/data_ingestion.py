"""Multi-layered data ingestion — ticks, bars, order book processing and storage."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
from typing import Any

from .config import get_settings
from .database import db_manager
from .models import (
    FeedSource,
    OHLCVBar,
    OrderBookLevel,
    OrderBookSnapshot,
    SymbolConfig,
    TickData,
    TimeFrame,
)
from .symbol_registry import SymbolRegistry

logger = logging.getLogger(__name__)

# ── cTrader domain types import ─────────────────────────────────────────────-

CTRADER_TYPES = False
_SpotTick: Any = None
_BarClose: Any = None
_DepthUpdate: Any = None

try:
    from ctrader_client import BarClose, DepthUpdate, SpotTick

    _SpotTick = SpotTick
    _BarClose = BarClose
    _DepthUpdate = DepthUpdate
    CTRADER_TYPES = True
    logger.debug("cTrader types imported successfully")
except ImportError as exc:
    logger.warning("cTrader types not available — ingestion will operate in degraded mode: %s", exc)


class DataIngestionEngine:
    """Processes real-time market data and persists to MongoDB."""

    def __init__(self, symbol_registry: SymbolRegistry) -> None:
        self._registry = symbol_registry
        settings = get_settings()
        self._tick_queue: asyncio.Queue = asyncio.Queue(
            maxsize=settings.tick_buffer_size
        )
        self._bar_queue: asyncio.Queue = asyncio.Queue(
            maxsize=settings.bar_buffer_size
        )
        self._depth_queue: asyncio.Queue = asyncio.Queue(
            maxsize=settings.depth_buffer_size
        )
        self._tasks: list[asyncio.Task] = []
        self._running = False

        # Aggregation state for tick→bar
        self._bar_aggregators: dict[tuple[int, TimeFrame], dict[str, Any]] = {}
        self._last_bar_time: dict[tuple[int, TimeFrame], int] = {}

        # Batch buffers
        self._tick_batch: list[TickData] = []
        self._bar_batch: list[OHLCVBar] = []
        self._last_flush = time.time()

        # Configurable flush thresholds (tuned for InfluxDB free tier by default)
        self._tick_flush_batch_size = max(1, settings.tick_flush_batch_size)
        self._bar_flush_batch_size = max(1, settings.bar_flush_batch_size)
        self._flush_interval_seconds = max(1.0, settings.flush_interval_seconds)

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks.append(asyncio.create_task(self._tick_processor()))
        self._tasks.append(asyncio.create_task(self._bar_processor()))
        self._tasks.append(asyncio.create_task(self._depth_processor()))
        self._tasks.append(asyncio.create_task(self._batch_flusher()))
        logger.info("Data ingestion engine started")

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        await self._flush_all()
        logger.info("Data ingestion engine stopped")

    def get_tick_queue(self) -> asyncio.Queue:
        return self._tick_queue

    def get_bar_queue(self) -> asyncio.Queue:
        return self._bar_queue

    def get_depth_queue(self) -> asyncio.Queue:
        return self._depth_queue

    # ── Processors ───────────────────────────────────────────────────────────

    async def _tick_processor(self) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self._tick_queue.get(), timeout=1.0)
                self._tick_queue.task_done()
                tick = self._normalize_tick(item)
                if tick:
                    self._tick_batch.append(tick)
                    self._aggregate_tick_to_bars(tick)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Tick processor error: %s", exc)

    async def _bar_processor(self) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self._bar_queue.get(), timeout=1.0)
                self._bar_queue.task_done()
                bar = self._normalize_bar(item)
                if bar:
                    self._bar_batch.append(bar)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Bar processor error: %s", exc)

    async def _depth_processor(self) -> None:
        while self._running:
            try:
                item = await asyncio.wait_for(self._depth_queue.get(), timeout=1.0)
                self._depth_queue.task_done()
                delta = self._normalize_depth_delta(item)
                if delta:
                    await self._merge_and_store_depth(delta)
            except TimeoutError:
                continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Depth processor error: %s", exc)

    async def _batch_flusher(self) -> None:
        while self._running:
            await asyncio.sleep(1.0)
            now = time.time()
            if (
                now - self._last_flush >= self._flush_interval_seconds
                or len(self._tick_batch) >= self._tick_flush_batch_size
                or len(self._bar_batch) >= self._bar_flush_batch_size
            ):
                await self._flush_all()
                self._last_flush = now

    async def _flush_all(self) -> None:
        if self._tick_batch:
            try:
                count = await db_manager.store_ticks(self._tick_batch)
                logger.debug("Flushed %d ticks", count)
            except Exception as exc:
                logger.warning("Tick flush failed: %s", exc)
            self._tick_batch.clear()

        if self._bar_batch:
            # Deduplicate bars by (symbol_id, timeframe, timestamp_ms) within the batch.
            # This prevents storing the same cTrader embedded trendbar dozens of times
            # because cTrader sends it repeatedly in multiple spot events.
            seen: set[tuple[int, str, int]] = set()
            unique_bars: list[OHLCVBar] = []
            for bar in self._bar_batch:
                key = (bar.symbol_id, bar.timeframe.value, bar.timestamp_ms)
                if key not in seen:
                    seen.add(key)
                    unique_bars.append(bar)
            self._bar_batch = unique_bars
            try:
                count = await db_manager.store_bars(self._bar_batch)
                logger.debug("Flushed %d bars", count)
            except Exception as exc:
                logger.warning("Bar flush failed: %s", exc)
            self._bar_batch.clear()

    # ── Normalization ────────────────────────────────────────────────────────

    def _normalize_tick(self, item: Any) -> TickData | None:
        if isinstance(item, TickData):
            return item
        if CTRADER_TYPES and _SpotTick is not None and isinstance(item, _SpotTick):
            sym = self._registry.get(item.symbol_id)
            digits = sym.digits if sym else 5
            return TickData(
                symbol_id=item.symbol_id,
                symbol_name=item.symbol_name,
                bid=item.bid,
                ask=item.ask,
                timestamp_ms=item.timestamp_ms,
                source=FeedSource.CTRADER,
                digits=digits,
            )
        if isinstance(item, dict):
            try:
                return TickData(**item)
            except Exception:
                pass
        return None

    def _normalize_bar(self, item: Any) -> OHLCVBar | None:
        if isinstance(item, OHLCVBar):
            return item
        if CTRADER_TYPES and _BarClose is not None and isinstance(item, _BarClose):
            # cTrader period format is 'M1', 'H1', etc. — convert to internal TimeFrame
            try:
                tf = TimeFrame.from_ctrader_period(item.period)
            except ValueError:
                tf = TimeFrame(item.period)
            # cTrader client now uses tb.utcTimestampInMinutes (actual bar time)
            # for embedded trendbars, so no timestamp fixup is needed.
            # Just align to the bar boundary for clean deduplication.
            aligned_ts = (item.timestamp_ms // tf.milliseconds) * tf.milliseconds
            return OHLCVBar(
                symbol_id=item.symbol_id,
                symbol_name=item.symbol_name,
                timeframe=tf,
                open=item.open,
                high=item.high,
                low=item.low,
                close=item.close,
                volume=item.volume,
                timestamp_ms=aligned_ts,
                source=FeedSource.CTRADER,
            )
        if isinstance(item, dict):
            try:
                return OHLCVBar(**item)
            except Exception:
                pass
        return None

    def _normalize_depth_delta(self, item: Any) -> dict[str, Any] | None:
        """Convert a cTrader DepthUpdate (delta) into normalized components.

        Returns a dict with symbol_id, symbol_name, bids, asks, deleted_ids,
        and digits so that _merge_and_store_depth can apply it to the latest
        cumulative snapshot.
        """
        if isinstance(item, OrderBookSnapshot):
            return {
                "symbol_id": item.symbol_id,
                "symbol_name": item.symbol_name,
                "bids": item.bids,
                "asks": item.asks,
                "deleted_ids": [],
                "digits": item.digits,
            }
        if CTRADER_TYPES and _DepthUpdate is not None and isinstance(item, _DepthUpdate):
            sym = self._registry.get(item.symbol_id)
            name = sym.name if sym else str(item.symbol_id)
            digits = sym.digits if sym else 5
            price_scale = 100_000
            bids = []
            asks = []
            for q in getattr(item, "new_quotes", []):
                # ProtoOADepthQuote fields: id (req#1), size (req#3), bid (opt#4), ask (opt#5)
                level_num = getattr(q, "id", getattr(q, "quote_id", 0))
                size_raw = getattr(q, "size", getattr(q, "volume", 0))
                bid_raw = getattr(q, "bid", 0)
                ask_raw = getattr(q, "ask", 0)
                volume = size_raw / 100.0 if size_raw else 0.0
                bid_price = round(bid_raw / price_scale, digits) if bid_raw else 0.0
                ask_price = round(ask_raw / price_scale, digits) if ask_raw else 0.0
                if bid_price > 0 and volume > 0:
                    bids.append(
                        OrderBookLevel(
                            price=bid_price,
                            volume=volume,
                            side="bid",
                            level=level_num,
                        )
                    )
                if ask_price > 0 and volume > 0:
                    asks.append(
                        OrderBookLevel(
                            price=ask_price,
                            volume=volume,
                            side="ask",
                            level=level_num,
                        )
                    )
            # Track all quote IDs that were in new_quotes so we can remove
            # stale sides (e.g. a quote that was bid+ask and is now bid-only)
            updated_ids = [getattr(q, "id", getattr(q, "quote_id", 0)) for q in getattr(item, "new_quotes", [])]
            return {
                "symbol_id": item.symbol_id,
                "symbol_name": name,
                "bids": bids,
                "asks": asks,
                "deleted_ids": list(getattr(item, "deleted_quote_ids", getattr(item, "deleted_ids", []))),
                "updated_ids": updated_ids,
                "digits": digits,
            }
        if isinstance(item, dict):
            try:
                snap = OrderBookSnapshot(**item)
                return {
                    "symbol_id": snap.symbol_id,
                    "symbol_name": snap.symbol_name,
                    "bids": snap.bids,
                    "asks": snap.asks,
                    "deleted_ids": [],
                    "updated_ids": [],
                    "digits": snap.digits,
                }
            except Exception:
                pass
        return None

    async def _merge_and_store_depth(self, delta: dict[str, Any]) -> None:
        """Apply a depth delta to the latest snapshot and persist the result.

        cTrader sends delta events (new quotes + deleted IDs). We maintain a
        cumulative order book by loading the latest stored snapshot, removing
        deleted levels, updating changed levels, and storing the merged result.
        """
        symbol_id = delta["symbol_id"]
        latest = await db_manager.get_latest_orderbook(symbol_id)
        if latest:
            deleted_ids = set(delta["deleted_ids"])
            bids = [b for b in latest.bids if b.level not in deleted_ids]
            asks = [a for a in latest.asks if a.level not in deleted_ids]
        else:
            bids = []
            asks = []

        # Remove stale sides: if a quote ID was updated but the delta doesn't
        # include a bid/ask for it, the old side is no longer valid.
        updated_ids = set(delta.get("updated_ids", []))
        new_bid_ids = {nb.level for nb in delta["bids"]}
        new_ask_ids = {na.level for na in delta["asks"]}
        bids = [b for b in bids if not (b.level in updated_ids and b.level not in new_bid_ids)]
        asks = [a for a in asks if not (a.level in updated_ids and a.level not in new_ask_ids)]

        # Overwrite existing levels with the same ID, or add new ones
        bid_map = {b.level: b for b in bids}
        ask_map = {a.level: a for a in asks}
        for nb in delta["bids"]:
            bid_map[nb.level] = nb
        for na in delta["asks"]:
            ask_map[na.level] = na

        bids = sorted(bid_map.values(), key=lambda x: x.price, reverse=True)
        asks = sorted(ask_map.values(), key=lambda x: x.price)

        snapshot = OrderBookSnapshot(
            symbol_id=symbol_id,
            symbol_name=delta["symbol_name"],
            bids=bids,
            asks=asks,
            timestamp_ms=int(time.time() * 1000),
            digits=delta["digits"],
        )
        await db_manager.store_orderbook(snapshot)

    # ── Tick aggregation to bars ─────────────────────────────────────────────

    def _aggregate_tick_to_bars(self, tick: TickData) -> None:
        cfg = self._registry.get_config(tick.symbol_id)
        if cfg is None:
            cfg = SymbolConfig(symbol_id=tick.symbol_id, name=tick.symbol_name)

        timeframes = cfg.bar_timeframes or [TimeFrame.M1]
        for tf in timeframes:
            self._update_bar_aggregator(tick, tf)

    def _update_bar_aggregator(self, tick: TickData, tf: TimeFrame) -> None:
        key = (tick.symbol_id, tf)
        bar_start = (tick.timestamp_ms // tf.milliseconds) * tf.milliseconds

        agg = self._bar_aggregators.get(key)
        if agg is None or agg["start_ms"] != bar_start:
            # Close previous bar
            if agg is not None:
                self._finalize_bar(key, agg)
            # Start new bar
            price = (tick.bid + tick.ask) / 2
            self._bar_aggregators[key] = {
                "symbol_id": tick.symbol_id,
                "symbol_name": tick.symbol_name,
                "timeframe": tf,
                "start_ms": bar_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": tick.bid_volume + tick.ask_volume,
            }
        else:
            price = (tick.bid + tick.ask) / 2
            agg["high"] = max(agg["high"], price)
            agg["low"] = min(agg["low"], price)
            agg["close"] = price
            agg["volume"] += tick.bid_volume + tick.ask_volume

    def _finalize_bar(self, key: tuple[int, TimeFrame], agg: dict[str, Any]) -> None:
        bar = OHLCVBar(
            symbol_id=agg["symbol_id"],
            symbol_name=agg["symbol_name"],
            timeframe=agg["timeframe"],
            open=agg["open"],
            high=agg["high"],
            low=agg["low"],
            close=agg["close"],
            volume=agg["volume"],
            timestamp_ms=agg["start_ms"],
        )
        self._bar_batch.append(bar)
        self._last_bar_time[key] = agg["start_ms"]
