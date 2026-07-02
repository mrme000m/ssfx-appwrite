"""Generate intermittent insight reports from signal experience tables."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import ExperienceAuthor, ExperiencePattern, ExperienceSession
from .store import SignalExperienceStore

logger = logging.getLogger(__name__)

DEFAULT_REPORT_DIR = Path(__file__).parent / "reports"


def _safe_round(value: float | None, digits: int = 2) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def _author_insights(authors: list[ExperienceAuthor], min_samples: int = 10) -> list[dict[str, Any]]:
    filtered = [a for a in authors if a.total_signals >= min_samples]
    filtered.sort(key=lambda a: (a.expectancy or -9999, a.win_rate), reverse=True)
    return [
        {
            "author": a.author,
            "total_signals": a.total_signals,
            "win_rate": _safe_round(a.win_rate),
            "avg_profit_pips": _safe_round(a.avg_profit_pips),
            "avg_loss_pips": _safe_round(a.avg_loss_pips),
            "profit_factor": _safe_round(a.profit_factor),
            "expectancy": _safe_round(a.expectancy),
            "current_streak": a.current_streak,
            "recommendation": _author_recommendation(a),
        }
        for a in filtered
    ]


def _author_recommendation(author: ExperienceAuthor) -> str:
    if author.expectancy is not None and author.expectancy > 5 and author.win_rate >= 0.5:
        return "allow"
    if author.win_rate < 0.35 and author.total_signals >= 20:
        return "block"
    if author.current_streak < -3:
        return "reduce"
    return "allow"


def _session_insights(sessions: list[ExperienceSession], min_samples: int = 10) -> list[dict[str, Any]]:
    filtered = [s for s in sessions if s.total_signals >= min_samples]
    filtered.sort(key=lambda s: s.win_rate, reverse=True)
    return [
        {
            "hour_utc": s.hour_utc,
            "total_signals": s.total_signals,
            "win_rate": _safe_round(s.win_rate),
            "avg_rr": _safe_round(s.avg_rr),
            "noise_ratio": _safe_round(s.noise_ratio),
            "recommendation": "allow" if s.win_rate >= 0.5 else "reduce",
        }
        for s in filtered
    ]


def _pattern_insights(patterns: list[ExperiencePattern], min_samples: int = 5) -> list[dict[str, Any]]:
    filtered = [p for p in patterns if p.total_signals >= min_samples]
    filtered.sort(key=lambda p: (p.expectancy or -9999, p.win_rate), reverse=True)
    return [
        {
            "pattern_key": p.pattern_key,
            "total_signals": p.total_signals,
            "win_rate": _safe_round(p.win_rate),
            "expectancy": _safe_round(p.expectancy),
            "confidence_score": _safe_round(p.confidence_score),
            "recommendation": "allow" if (p.expectancy or 0) > 0 else "reduce",
        }
        for p in filtered
    ]


def build_insights(store: SignalExperienceStore) -> dict[str, Any]:
    authors = store.list_authors()
    sessions = store.list_sessions()
    patterns = store.list_patterns()
    overall = store.get_overall("global")

    top_authors = _author_insights(authors)
    top_sessions = _session_insights(sessions)
    top_patterns = _pattern_insights(patterns)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "overall": {
            "rolling_30d_win_rate": _safe_round(overall.rolling_30d_win_rate) if overall else None,
            "signals_today": overall.signals_today if overall else 0,
            "good_vs_bad_ratio": _safe_round(overall.good_vs_bad_ratio) if overall else None,
        },
        "top_authors": top_authors,
        "bottom_authors": list(reversed(top_authors[-5:])) if len(top_authors) >= 5 else [],
        "best_sessions": top_sessions[:5],
        "worst_sessions": list(reversed(top_sessions[-5:])) if len(top_sessions) >= 5 else [],
        "top_patterns": top_patterns[:10],
        "routing_rules": _build_routing_rules(top_authors, top_sessions),
    }


def _build_routing_rules(
    authors: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
) -> list[str]:
    rules: list[str] = []
    allow_authors = [a["author"] for a in authors if a["recommendation"] == "allow"]
    block_authors = [a["author"] for a in authors if a["recommendation"] == "block"]
    if allow_authors:
        rules.append(f"Allow normal execution for authors: {', '.join(allow_authors)}.")
    if block_authors:
        rules.append(f"Block signals from authors: {', '.join(block_authors)}.")
    best_hours = [str(s["hour_utc"]) for s in sessions if s["recommendation"] == "allow"][:3]
    if best_hours:
        rules.append(f"Highest-confidence UTC hours: {', '.join(best_hours)}.")
    rules.append("Reduce size or block signals with corrupted SL/TP, missing author, or promotional text.")
    return rules


def build_llm_context(insights: dict[str, Any]) -> str:
    lines = [
        "# Signal Experience Context",
        "",
        f"Generated: {insights['generated_at']}",
        "",
        "## Routing Rules",
    ]
    for rule in insights.get("routing_rules", []):
        lines.append(f"- {rule}")

    lines.extend(["", "## Top Authors"])
    for a in insights.get("top_authors", [])[:5]:
        lines.append(
            f"- {a['author']}: win_rate={a['win_rate']}, expectancy={a['expectancy']} pips, "
            f"signals={a['total_signals']}, streak={a['current_streak']} → {a['recommendation']}"
        )

    lines.extend(["", "## Best Sessions (UTC hour)"])
    for s in insights.get("best_sessions", []):
        lines.append(
            f"- hour {s['hour_utc']}: win_rate={s['win_rate']}, signals={s['total_signals']}"
        )

    lines.extend(["", "## Top Patterns"])
    for p in insights.get("top_patterns", [])[:5]:
        lines.append(
            f"- {p['pattern_key']}: win_rate={p['win_rate']}, expectancy={p['expectancy']}"
        )

    lines.extend(["", "## Bad-Signal Heuristics"])
    lines.extend([
        "- Missing SL or TP on a NEW signal.",
        "- Corrupted SL/TP (e.g. SL above entry for BUY).",
        "- Messages containing 'GOLD' without clear XAUUSD context.",
        "- Promotional or chat messages (copier, discount, invalid parameters).",
    ])

    return "\n".join(lines) + "\n"


def generate_report(
    store: SignalExperienceStore | None = None,
    output_dir: Path = DEFAULT_REPORT_DIR,
) -> dict[str, Path]:
    """Generate insight JSON, LLM context markdown, and CSV companions."""
    logging.basicConfig(level=logging.INFO)
    store = store or SignalExperienceStore()
    output_dir.mkdir(parents=True, exist_ok=True)

    insights = build_insights(store)
    llm_md = build_llm_context(insights)

    insights_path = output_dir / "signal_experience_insights.json"
    insights_path.write_text(json.dumps(insights, indent=2), encoding="utf-8")

    context_path = output_dir / "llm_context.md"
    context_path.write_text(llm_md, encoding="utf-8")

    overall = store.get_overall("global")
    # Update overall row with insights JSON for runtime/agent access
    from .models import ExperienceOverall

    overall_row = overall or ExperienceOverall()
    overall_row.insights_json = json.dumps(insights)
    overall_row.updated_at = datetime.now(UTC).isoformat()
    store.save_overall(overall_row)

    logger.info("Report written to %s", output_dir)

    return {
        "insights_json": insights_path,
        "llm_context_md": context_path,
    }
