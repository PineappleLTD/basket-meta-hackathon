"""Engineering replay of a cached TRAIN story in a disposable, paper-only ETF.

No model/provider calls, no persistent accounts, no final-label evaluation. Prices
and fills are synthetic. This is a pipeline demonstration, not a profit backtest.
"""
import argparse
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

from sqlalchemy.orm import Session
from app.brokers import PaperBroker
from app.config import Settings
from app.db import make_engine, init_db
from app.kalshi import OrderBook
from app.models import Account, AccountMode, Basket, BasketPosition, Holding, Market, Side, User
from app.news_models import NewsContract, NewsPolicy, NewsSignal
from app.services import news, news_execution
from headline_model.dataset import load_dataset


class Books:
    def fetch_orderbooks(self, tickers):
        return {t: OrderBook(((0.5, 10000),), ((0.5, 10000),)) for t in tickers}


class PartialPaper(PaperBroker):
    def __init__(self):
        super().__init__(0, Books(), cache_seconds=0)
        self.first_sale = True
        self.requests = []

    def execute(self, order):
        self.requests.append(order)
        if order.action.value == "sell" and self.first_sale:
            self.first_sale = False
            return super().execute(replace(order, count=min(40, order.count)))
        return super().execute(order)


def replay():
    rows, _, dataset = load_dataset()
    train = [r for r in rows if r["split"] == "train" and r["platform"] == "kalshi"]
    predictor = news.make_predictor(Settings(_env_file=None, meta_api_key=None))
    chosen = result = None
    try:
        for row in sorted(train, key=lambda r: r["pair_id"]):
            if predictor.cache.get(row, predictor.client.model) is None:
                continue
            anchor = news.utc(row["published_at"]) + timedelta(seconds=60)
            if news.utc(row["deadline"]) <= anchor + timedelta(hours=1):
                continue
            article = {"headline": row["headline"], "summary": row["summary"],
                       "published_at": row["published_at"], "url": row["source_url"], "source": row["publisher"]}
            contract = {"platform": row["platform"], "market_id": row["market_id"], "outcome": row["outcome"],
                        "question": row["market_question"], "resolution_rules": row["resolution_rules"],
                        "deadline": row["deadline"], "thesis": row["position_thesis"]}
            result = predictor.predict(article, contract, now=anchor)
            if result["classification"] == "ADVERSE":
                chosen = row
                break
        if chosen is None:
            raise ValueError("No cached accepted adverse Kalshi TRAIN example was found")
        assert predictor.calls == 0, "Replay must never request extraction"
        candidates, seen = [chosen], {chosen["market_id"]}
        for row in sorted(train, key=lambda r: r["pair_id"]):
            if row["market_id"] not in seen and news.utc(row["deadline"]) > anchor + timedelta(hours=1):
                if row.get("market_open_at") and news.utc(row["market_open_at"]) > anchor:
                    continue
                candidates.append(row)
                seen.add(row["market_id"])
                if len(candidates) == 3:
                    break
        if len(candidates) != 3:
            raise ValueError("Three contemporaneous archived contracts are needed")
        engine = make_engine("sqlite://", ":memory:")
        init_db(engine)
        try:
            with Session(engine, expire_on_commit=False) as session:
                user = User(email="replay@example.invalid", password_hash="DISABLED_OFFLINE_ACCOUNT")
                session.add(user)
                session.flush()
                account = Account(user_id=user.id, mode=AccountMode.PAPER, cash_cents=1000000, starting_cash_cents=1000000)
                basket = Basket(owner_id=user.id, name="Archived-data engineering replay", description=chosen["position_thesis"])
                session.add_all([account, basket])
                for row in candidates:
                    market = Market(external_id=row["market_id"], question=row["market_question"], yes_price=0.5,
                                    yes_bid=0.5, yes_ask=0.5, volume=10000, is_open=True,
                                    closes_at=news.utc(row["deadline"]), quoted_at=anchor)
                    session.add(market)
                    session.flush()
                    side = Side(row["outcome"].lower())
                    basket.positions.append(BasketPosition(market=market, side=side, target_weight=1/3, units=100/3/0.5))
                    account.holdings.append(Holding(basket=basket, market=market, side=side, contracts=200, cost_cents=10000))
                    session.add(NewsContract(market_id=market.id, rules=row["resolution_rules"], deadline=news.utc(row["deadline"])))
                session.add(NewsPolicy(basket_id=basket.id, mode="suggest", sensitivity=0.5, max_shift=1))
                session.flush()
                topic = news.watchlist(session, basket)["topics"][0]
                session.add(NewsSignal(id="offline-archived-story", basket_id=basket.id, market_id=topic["market_id"],
                                      side=chosen["outcome"].lower(), published_at=news.utc(chosen["published_at"]),
                                      created_at=anchor, payload={"article": article, "result": result,
                                          "contract_hash": news.digest(topic["contract"])}))
                session.commit()
                broker = PartialPaper()
                cycle = news_execution.start(session, account, basket, broker, now=anchor)
                session.commit()
                phases = []
                for offset in range(3):
                    news_execution.advance(session, account, basket, broker, cycle, now=anchor + timedelta(seconds=offset))
                    session.commit()
                    phases.append({"status": cycle.status,
                                   "sold": sum(x["filled"] for x in cycle.payload["sells"]),
                                   "planned_sale": sum(x["count"] for x in cycle.payload["sells"]),
                                   "bought": sum(x["filled"] for x in cycle.payload["buys"])})
                assert phases[0]["sold"] == 40 and phases[0]["bought"] == 0
                assert phases[1]["sold"] == 100 and phases[1]["bought"] == 0
                assert phases[2]["status"] == "COMPLETE"
                return {"status": "PASS", "kind": "engineering_replay_not_profit_backtest", "split": "train",
                        "pair_id": chosen["pair_id"], "headline": chosen["headline"], "market_id": chosen["market_id"],
                        "held_outcome": chosen["outcome"], "archived_clock": anchor.isoformat(),
                        "classification": result["classification"], "ridge_score": result["ridge_score"],
                        "artifact_sha256": predictor.artifact_hash, "dataset_sha256": dataset["sha256"],
                        "provider_calls": 0, "real_orders": 0, "evaluated_final_labels": False,
                        "prices_and_fills": "synthetic; not historical liquidity", "phases": phases,
                        "saved_weights": [p.target_weight for p in basket.positions]}
        finally:
            engine.dispose()
    finally:
        predictor.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = replay()
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
