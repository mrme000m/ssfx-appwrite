"""Signal parsers — pure parsing with no persistence side effects."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import aiohttp

from .enums import Direction, OrderType, SignalType
from .models import TradeSignal
from .parser import parse_signal

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a trading signal parser for the SureShot GOLD VIP Telegram channel.

## Your Role
Analyze raw Telegram messages from a forex/gold/crypto trading signal channel
and extract structured trading signals. You understand signal formats, reply
chains, and ongoing trade context.

## Signal Types
- NEW: A new trade entry signal (BUY/SELL with entry, SL, TP)
- TP_HIT: A take profit target was hit (set tp_hit_number: 1, 2, or 3)
- SL_HIT: Stop loss was hit
- CLOSE_HALF: Close 50% of the position
- CLOSE_PARTIAL: Close a partial amount of the position (set close_percentage)
- CLOSE: Close the entire position
- SL_TO_ENTRY: Move stop loss to entry price (breakeven)
- CANCEL: Cancel the previous signal/order
- RUNNING: Status update about a running trade (floating profit)
- ENTRY_UPDATE: Update the entry price of an existing signal
- IGNORE: Not a trading signal (promotional, chat, news, etc.)

## Direction
- BUY or SELL for NEW signals
- null for non-directional signals (TP_HIT, CLOSE, etc.)

## Symbol Mapping
- gold/xau -> XAUUSD
- bitcoin/btc -> BTCUSD
- ethereum/eth -> ETHUSD
- forex pairs: EURUSD, GBPUSD, USDJPY, etc.

## Output Format
Respond with ONLY a JSON object (no markdown, no explanation outside JSON):
{
  "signal_type": "NEW|TP_HIT|SL_HIT|CLOSE_HALF|CLOSE_PARTIAL|CLOSE|SL_TO_ENTRY|CANCEL|RUNNING|ENTRY_UPDATE|IGNORE",
  "direction": "BUY|SELL|null",
  "symbol": "XAUUSD|BTCUSD|null",
  "order_type": "MARKET|LIMIT|STOP|STOP_LIMIT|null",
  "entry_price": <float or null>,
  "tp1": <float or null>,
  "tp2": <float or null>,
  "tp3": <float or null>,
  "sl": <float or "BREAKEVEN" or null>,
  "profit_pips": <int or null>,
  "close_percentage": <float or null>,
  "follow_up_action": "SL_TO_ENTRY" or null,
  "tp_hit_number": <int 1-3 or null>,
  "parse_confidence": <float 0.0-0.99>,
  "reasoning": "<one sentence explanation>"
}

## Rules
1. If a message is promotional, a copier ad, or not about a trade, use IGNORE.
2. For reply messages, check if it references an earlier signal (TP hit, close, etc.).
3. "MOVE SL TO ENTRY" combined with "CLOSE HALF" means follow_up_action = "SL_TO_ENTRY".
4. "FULL TP HIT" or "All TARGET HIT" means tp_hit_number = 3 and close_percentage = 100.
5. "TP 1 HIT … CLOSE UP HALF PROFIT" means signal_type = "TP_HIT", tp_hit_number = 1, close_percentage = 50, follow_up_action = "CLOSE_HALF".
6. Entry price after BUY/SELL (e.g. "XAUUSD BUY 4030") means entry_price = 4030.
7. "Take Entry Active 4430" is an ENTRY_UPDATE with entry_price = 4430.
8. If the message has no clear trading signal, return IGNORE with low confidence.
9. parse_confidence should reflect how certain you are (0.9+ for clear signals, 0.3-0.5 for ambiguous).
"""


@dataclass
class AgentConfig:
    base_url: str
    model: str
    temperature: float
    max_tokens: int
    timeout_seconds: int
    min_confidence: float
    api_key: str


@runtime_checkable
class SignalParser(Protocol):
    async def parse(
        self,
        raw_text: str,
        *,
        message_id: int | None,
        chat_id: str | None,
        reply_to_message_id: int | None = None,
        timestamp_ms: int | None = None,
        context: list[str] | None = None,
    ) -> TradeSignal | None: ...

    async def close(self) -> None: ...


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


class LlmSignalParser:
    """LLM-based signal parser using any OpenAI-compatible chat endpoint."""

    def __init__(self, config: AgentConfig):
        self._config = config
        self._session: aiohttp.ClientSession | None = None

    @property
    def _http(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=self._config.timeout_seconds),
            )
        return self._session

    async def parse(
        self,
        raw_text: str,
        *,
        message_id: int | None,
        chat_id: str | None,
        reply_to_message_id: int | None = None,
        timestamp_ms: int | None = None,
        context: list[str] | None = None,
    ) -> TradeSignal | None:
        if not self._config.api_key:
            logger.warning("No LLM API key set, skipping LLM parser")
            return None

        ts = timestamp_ms or int(datetime.now(UTC).timestamp() * 1000)
        context_text = self._build_context(context)
        user_prompt = self._build_user_prompt(raw_text, context_text, reply_to_message_id)

        try:
            response_text = await self._call_llm(user_prompt)
            if response_text is None:
                return None

            parsed = self._parse_llm_response(response_text)
            if parsed is None:
                return None

            signal = self._dict_to_signal(
                parsed, raw_text, message_id, chat_id, reply_to_message_id, ts
            )
            if signal is None:
                return None

            if signal.parse_confidence < self._config.min_confidence:
                logger.info(
                    "LLM confidence %.2f below threshold %.2f",
                    signal.parse_confidence,
                    self._config.min_confidence,
                )
                return None

            return signal

        except asyncio.TimeoutError:
            logger.warning("LLM call timed out")
            return None
        except Exception as exc:
            logger.warning("LLM parsing error: %s", exc)
            return None

    def _build_context(self, context_lines: list[str] | None) -> str:
        if not context_lines:
            return "(no prior messages today)"
        return "\n".join(context_lines)

    def _build_user_prompt(
        self, raw_text: str, context: str, reply_to: int | None
    ) -> str:
        reply_note = (
            f"\n(This message is a reply to message #{reply_to})" if reply_to else ""
        )
        return (
            f"## Today's Messages (context)\n{context}\n\n"
            f"## New Message to Analyze{reply_note}\n{raw_text}"
        )

    async def _call_llm(self, user_prompt: str) -> str | None:
        payload: dict[str, Any] = {
            "model": self._config.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": self._config.temperature,
            "max_tokens": self._config.max_tokens,
        }

        url = f"{self._config.base_url}/chat/completions"
        try:
            async with self._http.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    logger.warning("LLM API returned %d: %s", resp.status, body[:200])
                    return None

                data = await resp.json()
                choices = data.get("choices", [])
                if not choices:
                    return None

                content = choices[0].get("message", {}).get("content", "")
                return content or None
        except aiohttp.ClientError as exc:
            logger.warning("LLM HTTP error: %s", exc)
            return None

    def _parse_llm_response(self, text: str) -> dict[str, Any] | None:
        text = text.strip()

        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            logger.warning("Could not parse LLM response as JSON: %s", text[:200])
            return None

    def _dict_to_signal(
        self,
        data: dict[str, Any],
        raw_text: str,
        message_id: int | None,
        chat_id: str | None,
        reply_to_message_id: int | None,
        timestamp_ms: int,
    ) -> TradeSignal | None:
        try:
            signal_type_str = str(data.get("signal_type", "IGNORE")).upper()
            try:
                signal_type = SignalType(signal_type_str)
            except ValueError:
                signal_type = SignalType.IGNORE

            if signal_type == SignalType.IGNORE:
                confidence = float(data.get("parse_confidence", 0.3))
                if confidence < 0.50:
                    return None

            direction = None
            dir_str = data.get("direction")
            if dir_str and dir_str.upper() in ("BUY", "SELL"):
                direction = Direction(dir_str.upper())

            order_type = None
            ot_str = data.get("order_type")
            if ot_str:
                try:
                    order_type = OrderType(str(ot_str).upper())
                except ValueError:
                    order_type = None

            sl = data.get("sl")
            if sl is not None and not isinstance(sl, (int, float, str)):
                sl = None

            confidence = float(data.get("parse_confidence", 0.5))
            confidence = max(0.0, min(0.99, confidence))

            return TradeSignal(
                raw_text=raw_text,
                direction=direction,
                symbol=data.get("symbol"),
                signal_type=signal_type,
                order_type=order_type,
                entry_price=_safe_float(data.get("entry_price")),
                tp1=_safe_float(data.get("tp1")),
                tp2=_safe_float(data.get("tp2")),
                tp3=_safe_float(data.get("tp3")),
                sl=sl,
                profit_pips=_safe_int(data.get("profit_pips")),
                close_percentage=_safe_float(data.get("close_percentage")),
                follow_up_action=data.get("follow_up_action"),
                tp_hit_number=_safe_int(data.get("tp_hit_number")),
                parse_confidence=confidence,
                message_id=message_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                timestamp_ms=timestamp_ms,
                parser_used="llm",
                llm_reasoning=data.get("reasoning"),
            )
        except Exception as exc:
            logger.warning("Error converting LLM response to signal: %s", exc)
            return None

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


class RegexSignalParser:
    """Regex-based fallback parser. Stateless, no external dependencies."""

    async def parse(
        self,
        raw_text: str,
        *,
        message_id: int | None,
        chat_id: str | None,
        reply_to_message_id: int | None = None,
        timestamp_ms: int | None = None,
        context: list[str] | None = None,
    ) -> TradeSignal | None:
        ts = timestamp_ms or int(datetime.now(UTC).timestamp() * 1000)
        return parse_signal(
            raw_text=raw_text,
            message_id=message_id,
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            timestamp_ms=ts,
        )

    async def close(self) -> None:
        pass


class ChainedParser:
    """Tries primary parser, falls back to secondary on failure/None."""

    def __init__(self, primary: SignalParser, fallback: SignalParser):
        self._primary = primary
        self._fallback = fallback

    async def parse(
        self,
        raw_text: str,
        *,
        message_id: int | None,
        chat_id: str | None,
        reply_to_message_id: int | None = None,
        timestamp_ms: int | None = None,
        context: list[str] | None = None,
    ) -> TradeSignal | None:
        try:
            signal = await self._primary.parse(
                raw_text,
                message_id=message_id,
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                timestamp_ms=timestamp_ms,
                context=context,
            )
            if signal is not None:
                signal.parser_used = "llm"
                return signal
        except Exception:
            pass

        signal = await self._fallback.parse(
            raw_text,
            message_id=message_id,
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            timestamp_ms=timestamp_ms,
            context=context,
        )
        if signal:
            signal.parser_used = "regex"
        return signal

    async def close(self) -> None:
        await self._primary.close()
        await self._fallback.close()
