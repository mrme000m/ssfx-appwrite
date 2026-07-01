"""Automated data quality assurance — gap detection, anomaly checking, backfill."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

from .config import get_settings
from .database import db_manager
from .models import DataQualityReport, FeedSource, OHLCVBar, TimeFrame

logger = logging.getLogger(__name__)

class DataQualityEngine:
    """Monitors data freshness, detects gaps, and triggers backfill."""

    def __init__(self, feed_manager: Any | None = None) -> None:
        self._feed = feed_manager
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._last_check: dict[tuple[int, TimeFrame | None], float] = {}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._tasks.append(asyncio.create_task(self._monitor_loop()))
        logger.info("Data quality engine started")

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("Data quality engine stopped")

    async def _monitor_loop(self) -> None:
        while self._running:
            await asyncio.sleep(30.0)
            try:
                await self.run_full_check()
            except Exception as exc:
                logger.warning("Data quality check failed: %s", exc)

    async def run_full_check(self) -> list[DataQualityReport]:
        symbols = await db_manager.list_symbols(status="active")
        reports: list[DataQualityReport] = []
        for sym in symbols:
            # Check tick freshness
            tick_report = await self.check_tick_quality(sym.symbol_id, sym.name)
            reports.append(tick_report)
            # Check bar quality per timeframe
            for tf in [TimeFrame.M1, TimeFrame.H1, TimeFrame.D1]:
                bar_report = await self.check_bar_quality(sym.symbol_id, sym.name, tf)
                reports.append(bar_report)
        return reports

    async def check_tick_quality(self, symbol_id: int, symbol_name: str) -> DataQualityReport:
        latest = await db_manager.get_latest_tick(symbol_id)
        total = await db_manager.count_ticks(symbol_id)

        issues: list[str] = []
        freshness: float | None = None
        last_ms: int | None = None
        score = 1.0

        if latest:
            last_ms = latest.timestamp_ms
            freshness = time.time() - (last_ms / 1000.0)
            settings = get_settings()
            if freshness > settings.stale_threshold_seconds:
                issues.append(f"Stale ticks: {freshness:.1f}s since last tick")
                score -= 0.3
        else:
            issues.append("No tick data found")
            score -= 0.5

        report = DataQualityReport(
            symbol_id=symbol_id,
            symbol_name=symbol_name,
            total_records=total,
            last_tick_ms=last_ms,
            freshness_seconds=freshness,
            score=max(score, 0.0),
            issues=issues,
        )
        with contextlib.suppress(Exception):
            await db_manager.store_quality_report(report)
        return report

    async def check_bar_quality(
        self, symbol_id: int, symbol_name: str, timeframe: TimeFrame
    ) -> DataQualityReport:
        bars = await db_manager.get_bars(symbol_id, timeframe, limit=1000)
        total = await db_manager.count_bars(symbol_id)

        issues: list[str] = []
        gap_count = 0
        anomaly_count = 0
        last_ms = bars[0].timestamp_ms if bars else None
        freshness = None
        score = 1.0

        if bars:
            freshness = time.time() - (last_ms / 1000.0) if last_ms else None
            # Deduplicate by aligned timestamp — cTrader may send multiple updates
            # for the same bar with slightly different timestamps. Keep the latest.
            seen: dict[int, OHLCVBar] = {}
            for bar in bars:
                aligned = (bar.timestamp_ms // timeframe.milliseconds) * timeframe.milliseconds
                if aligned not in seen or bar.timestamp_ms > seen[aligned].timestamp_ms:
                    seen[aligned] = bar
            # Sort by aligned timestamp (dict keys) descending, not original timestamp
            aligned_ts_list = sorted(seen.keys(), reverse=True)
            gap_ranges: list[tuple[int, int]] = []
            for i in range(1, len(aligned_ts_list)):
                prev_ts = aligned_ts_list[i]
                curr_ts = aligned_ts_list[i - 1]  # descending order
                expected = curr_ts - timeframe.milliseconds
                diff = abs(prev_ts - expected)
                if diff > 1:  # strict check after dedup+alignment
                    gap_start = min(prev_ts + timeframe.milliseconds, curr_ts - timeframe.milliseconds)
                    gap_end = max(prev_ts + timeframe.milliseconds, curr_ts - timeframe.milliseconds)
                    if gap_end > gap_start:
                        # Merge with previous gap range if contiguous
                        if gap_ranges and gap_start <= gap_ranges[-1][1] + timeframe.milliseconds:
                            gap_ranges[-1] = (gap_ranges[-1][0], max(gap_ranges[-1][1], gap_end))
                        else:
                            gap_ranges.append((gap_start, gap_end))
                # OHLC anomalies — use the deduped bar for this aligned timestamp
                prev_bar = seen[prev_ts]
                if prev_bar.high < prev_bar.low:
                    anomaly_count += 1
                    issues.append(f"High<Low at {prev_bar.timestamp_ms}")
                if prev_bar.high < max(prev_bar.open, prev_bar.close, prev_bar.low):
                    anomaly_count += 1
                    issues.append(f"High not max at {prev_bar.timestamp_ms}")
                if prev_bar.low > min(prev_bar.open, prev_bar.close, prev_bar.high):
                    anomaly_count += 1
                    issues.append(f"Low not min at {prev_bar.timestamp_ms}")

            gap_count = len(gap_ranges)
            if gap_count > 0:
                total_gap_ms = sum(end - start for start, end in gap_ranges)
                issues.append(f"{gap_count} gap range(s) in {timeframe.value} bars ({total_gap_ms // 1000}s total)")
                score -= min(gap_count * 0.05, 0.3)
            if anomaly_count > 0:
                issues.append(f"{anomaly_count} OHLC anomalies detected")
                score -= min(anomaly_count * 0.05, 0.3)
            if freshness and freshness > 3600:
                issues.append(f"Stale bars: {freshness/60:.0f}min since last bar")
                score -= 0.2
        else:
            issues.append(f"No {timeframe.value} bar data found")
            score -= 0.5

        report = DataQualityReport(
            symbol_id=symbol_id,
            symbol_name=symbol_name,
            timeframe=timeframe,
            total_records=total,
            gap_count=gap_count,
            anomaly_count=anomaly_count,
            last_bar_ms=last_ms,
            freshness_seconds=freshness,
            score=max(score, 0.0),
            issues=issues,
        )
        await db_manager.store_quality_report(report)
        return report

    async def backfill_gaps(
        self, symbol_id: int, timeframe: TimeFrame, from_ms: int, to_ms: int
    ) -> int:
        """Attempt to backfill missing bars using the feed manager."""
        if not self._feed or not self._feed.is_connected:
            logger.warning("Cannot backfill: feed not connected")
            return 0

        settings = get_settings()
        if not settings.gap_fill_enabled:
            return 0

        try:
            bars = await self._feed.fetch_historical_bars(
                symbol_id, timeframe.value, from_ms, to_ms
            )
            filled = 0
            for bar in bars:
                exists = await db_manager.bar_exists(symbol_id, timeframe, bar.timestamp_ms)
                if not exists:
                    ohlcv = OHLCVBar(
                        symbol_id=bar.symbol_id,
                        symbol_name=bar.symbol_name,
                        timeframe=timeframe,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        timestamp_ms=bar.timestamp_ms,
                        source=FeedSource.BACKFILL,
                    )
                    await db_manager.store_bars([ohlcv])
                    filled += 1
            logger.info("Backfilled %d bars for %s %s", filled, symbol_id, timeframe.value)
            return filled
        except Exception as exc:
            logger.warning("Backfill failed: %s", exc)
            return 0
