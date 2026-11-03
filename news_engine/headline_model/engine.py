"""Headline-to-position decision planning. No exchange requests or order execution."""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math
from pathlib import Path
import sqlite3

from .ridge_model import normalize


def utc(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Use timestamps with an explicit timezone.")
    return result.astimezone(timezone.utc)


def number(value, name, minimum=Decimal(0)):
    result = Decimal(str(value))
    if not result.is_finite() or result < minimum:
        raise ValueError(f"Invalid {name}.")
    return result


@dataclass(frozen=True)
class Position:
    id: str
    platform: str
    market_id: str
    outcome: str
    question: str
    thesis: str
    resolution_rules: str
    deadline: str
    # Entity and event aliases help candidate retrieval. Muse verifies relevance.
    relevance_groups: list
    quantity: Decimal
    mark_price: Decimal
    min_order_size: Decimal
    quantity_step: Decimal
    allow_partial: bool
    tradable: bool
    has_open_orders: bool

    @classmethod
    def parse(cls, item):
        groups = item["relevance_groups"]
        if not isinstance(groups, list) or len(groups) < 2 or any(
            not isinstance(group, list) or not group or any(not isinstance(alias, str) or not normalize(alias) for alias in group)
            for group in groups
        ):
            raise ValueError("Provide at least two nonempty relevance groups: subject/entity and event/topic.")
        if item["platform"] not in {"kalshi", "polymarket"} or item["outcome"] not in {"YES", "NO"}:
            raise ValueError("Supported platforms: kalshi/polymarket; outcomes: YES/NO.")
        for key in ["allow_partial", "tradable", "has_open_orders"]:
            if not isinstance(item[key], bool):
                raise ValueError(f"{key} must be a boolean.")
        for key in ["id", "market_id", "question", "thesis", "resolution_rules", "deadline"]:
            if not isinstance(item[key], str) or not item[key].strip():
                raise ValueError(f"{key} cannot be blank.")
        quantity = number(item["quantity"], "quantity")
        price = number(item["mark_price"], "mark_price", Decimal("0.000001"))
        step = number(item["quantity_step"], "quantity_step", Decimal("0.00000001"))
        minimum = number(item["min_order_size"], "min_order_size", step)
        if price >= 1 or quantity % step:
            raise ValueError("Price must lie between 0 and 1; quantity must match the quantity step.")
        utc(item['deadline'])
        return cls(item["id"], item["platform"], item["market_id"], item["outcome"], item["question"], item["thesis"], item['resolution_rules'], item['deadline'], groups, quantity, price, minimum, step, item["allow_partial"], item["tradable"], item["has_open_orders"])


class Journal:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("CREATE TABLE IF NOT EXISTS decisions (key TEXT PRIMARY KEY, position_id TEXT NOT NULL, created REAL NOT NULL, record TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS cursors (path TEXT PRIMARY KEY, signature TEXT NOT NULL, offset INTEGER NOT NULL)")
        self.db.commit()

    def seen(self, key):
        return bool(self.db.execute('SELECT 1 FROM decisions WHERE key=?', (key,)).fetchone())

    def offset(self, path, signature):
        row = self.db.execute('SELECT signature, offset FROM cursors WHERE path=?', (str(path),)).fetchone()
        return row[1] if row and row[0] == signature else 0

    def save_offset(self, path, signature, offset):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cursors VALUES (?,?,?)', (str(path), signature, offset))

    def close(self):
        self.db.close()

    def reserve(self, key, position_id, now, cooldown_seconds, record):
        # Serialize the duplicate/cooldown check with insertion across workers.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM decisions WHERE key=?", (key,)).fetchone():
                return "duplicate_headline"
            previous = self.db.execute("SELECT MAX(created) FROM decisions WHERE position_id=?", (position_id,)).fetchone()[0]
            if previous is not None and now.timestamp() - previous < cooldown_seconds:
                return "position_cooldown"
            self.db.execute("INSERT INTO decisions VALUES (?,?,?,?)", (key, position_id, now.timestamp(), json.dumps(record)))
        return None


class HeadlineEngine:
    def __init__(self, scorer, journal, reduce_fraction, matcher=None, negative_threshold=-0.5, max_age_seconds=3600, cooldown_seconds=1800):
        self.scorer = scorer
        self.journal = journal
        self.matcher = matcher
        self.reduce_fraction = number(reduce_fraction, "reduce_fraction", Decimal("0.000001"))
        if self.reduce_fraction >= 1:
            raise ValueError("Reduction fraction must be between 0 and 1; it is an explicit strategy input.")
        if not math.isfinite(negative_threshold) or negative_threshold >= 0:
            raise ValueError("The negative threshold must be finite and below zero.")
        if max_age_seconds <= 0 or cooldown_seconds < 0:
            raise ValueError("Invalid age/cooldown settings.")
        self.threshold = negative_threshold
        self.max_age = max_age_seconds
        self.cooldown = cooldown_seconds

    def process(self, article, portfolio, now=None):
        now = now or datetime.now(timezone.utc)
        text = article["headline"]
        if not isinstance(text, str) or not normalize(text):
            raise ValueError("Headline cannot be blank.")
        if not isinstance(article.get('summary', ''), str) or len(text) > 2000 or len(article.get('summary', '')) > 12000:
            raise ValueError('Supply a headline up to 2,000 characters and a summary up to 12,000 characters.')
        if not isinstance(portfolio.get('basket_id'), str) or not portfolio['basket_id'].strip():
            raise ValueError('Supply a basket_id.')
        published = utc(article["published_at"])
        snapshot_time = utc(portfolio["as_of"])
        nav = number(portfolio["nav"], "portfolio NAV", Decimal("0.000001"))
        positions = [Position.parse(p) for p in portfolio["positions"]]
        if len({p.id for p in positions}) != len(positions):
            raise ValueError("Position identifiers must be unique within the basket.")
        if sum((p.quantity*p.mark_price for p in positions), Decimal(0)) > nav:
            raise ValueError("Position values exceed basket NAV; supply one consistent snapshot.")
        results = []
        candidates = self.matcher.candidates(article, positions) if self.matcher else set()
        for position in positions:
            result = {"headline": text, "position_id": position.id, "platform": position.platform, "market_id": position.market_id, "outcome": position.outcome, "action": "HOLD", "execution": "NOT_SUBMITTED"}
            reason = None
            key = hashlib.sha256((portfolio["basket_id"]+"|"+position.id+"|"+position.outcome+"|"+normalize(text)).encode()).hexdigest()
            article_age = (now-published).total_seconds()
            snapshot_age = (now-snapshot_time).total_seconds()
            if not 0 <= article_age <= self.max_age:
                reason = "stale_or_future_headline"
            elif not 0 <= snapshot_age <= 60:
                reason = "stale_or_future_portfolio_snapshot"
            elif utc(position.deadline) <= now:
                reason = 'past_resolution_deadline'
            elif position.quantity <= 0 or not position.tradable:
                reason = "position_closed_or_not_tradable"
            elif position.has_open_orders:
                reason = "reconcile_existing_orders_first"
            elif self.journal.seen(key):
                reason = 'duplicate_headline'
            elif self.scorer is None:
                reason = "no_trained_model"
            elif self.matcher is None:
                reason = 'no_muse_relevance_verifier'
            elif position.id not in candidates:
                reason = 'not_in_candidate_set'
            if reason:
                results.append(dict(result, reason=reason))
                continue
            try:
                assessment = self.matcher.assess(article, position)
                result['muse_assessment'] = {
                    'related': assessment.related, 'confidence': assessment.confidence,
                    'reason': assessment.reason, 'evidence': assessment.evidence,
                    'features': assessment.features, 'model': assessment.model,
                    'prompt_version': assessment.prompt_version,
                }
                if not assessment.related:
                    results.append(dict(result, reason='unrelated_headline'))
                    continue
                if assessment.confidence < 0.8 or assessment.features['uncertainty'] >= 0.5 or assessment.features['event_specificity'] < 0.5 or assessment.features['time_alignment'] < 0.5:
                    results.append(dict(result, reason='uncertain_or_weak_relevance'))
                    continue
                score = float(self.scorer.score(assessment))
                if not math.isfinite(score):
                    raise ValueError("Non-finite score")
            except ValueError as error:
                results.append(dict(result, reason="model_abstained", detail=str(error)))
                continue
            result["ridge_score"] = score
            if score >= self.threshold:
                results.append(dict(result, reason="not_negative_for_held_bet"))
                continue
            sell = (position.quantity*self.reduce_fraction/position.quantity_step).to_integral_value(rounding=ROUND_FLOOR)*position.quantity_step
            can_partial = position.allow_partial and sell >= position.min_order_size and position.quantity-sell >= position.min_order_size
            if can_partial:
                action, reason = "REDUCE_WEIGHT", "related_negative_headline_partial_sale"
            else:
                action, reason, sell = "EXIT", "partial_sale_unavailable_or_below_minimum", position.quantity
            if sell < position.min_order_size:
                results.append(dict(result, reason="position_below_exchange_minimum"))
                continue
            result.update({
                "action": action, "reason": reason, "action_id": key,
                "intent": {"operation": "SELL_HELD_OUTCOME", "quantity": str(sell), "snapshot_quantity": str(position.quantity), "estimated_weight_before": str(position.quantity*position.mark_price/nav), "estimated_weight_after": str((position.quantity-sell)*position.mark_price/nav), "proceeds_destination": "basket_cash", "requires_fresh_quote_and_fill_reconciliation": True},
            })
            blocked = self.journal.reserve(key, portfolio["basket_id"]+"|"+position.id, now, self.cooldown, result)
            if blocked:
                result = dict(result, action="HOLD", reason=blocked)
                result.pop("intent", None)
            results.append(result)
        return results
