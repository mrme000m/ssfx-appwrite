"""Trading service request/response models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class SignalRequest:
    grant_id: str
    ctid_trader_account_id: int
    broker: str = "ctrader"
    symbol: str = ""
    direction: str = ""
    signal_type: str = "NEW"
    entry_price: float | None = None
    sl: float | None = None
    tp1: float | None = None
    tp2: float | None = None
    tp3: float | None = None
    raw_text: str = ""
    reply_to_message_id: int | None = None
    order_type: str = "MARKET"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SignalRequest":
        return cls(
            grant_id=data["grant_id"],
            ctid_trader_account_id=int(data["ctid_trader_account_id"]),
            broker=data.get("broker", "ctrader"),
            symbol=data.get("symbol", ""),
            direction=data.get("direction", ""),
            signal_type=data.get("signal_type", "NEW"),
            entry_price=float(v) if (v := data.get("entry_price")) is not None else None,
            sl=float(v) if (v := data.get("sl")) is not None else None,
            tp1=float(v) if (v := data.get("tp1")) is not None else None,
            tp2=float(v) if (v := data.get("tp2")) is not None else None,
            tp3=float(v) if (v := data.get("tp3")) is not None else None,
            raw_text=data.get("raw_text", ""),
            reply_to_message_id=int(v) if (v := data.get("reply_to_message_id")) is not None else None,
            order_type=data.get("order_type", "MARKET"),
        )


@dataclass
class ExecutionResponse:
    status: str
    follower_id: str
    order_id: int | None = None
    position_id: int | None = None
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "follower_id": self.follower_id,
            "order_id": self.order_id,
            "position_id": self.position_id,
            "message": self.message,
            "details": self.details,
        }
