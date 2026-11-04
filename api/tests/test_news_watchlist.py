"""News discovery must find entity aliases without accepting generic question words."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.models import Side, utcnow
from app.services import news
from news_agent.core import Article, matches


def topic(question, thesis=""):
    deadline = utcnow() + timedelta(days=730)
    market = SimpleNamespace(id=1, external_id="TEST-2028", question=question,
                             category="Elections", closes_at=deadline)
    position = SimpleNamespace(market_id=1, market=market, side=Side.YES)
    basket = SimpleNamespace(id=1, description=thesis, positions=[position])
    spec = SimpleNamespace(rules="Resolves according to the official result.", deadline=deadline)
    session = SimpleNamespace(get=lambda _model, _key: spec)
    return news.watchlist(session, basket)["topics"][0]


def source(headline):
    return Article.parse({"headline": headline, "summary": "", "published_at": utcnow().isoformat(),
                          "url": "https://example.org/story", "source": "Test source"})


@pytest.mark.parametrize("headline", [
    "GOP suffers sweeping midterm losses as voters reject White House agenda",
    "Republicans face backlash after bruising midterm defeat",
    "Republican leaders reassess their prospects",
])
def test_republican_market_matches_party_variants(headline):
    watched = topic("Will the Republican candidate win the 2028 presidential election "
                    "by 91 to 130 Electoral College votes?")
    assert matches(source(headline), watched)


@pytest.mark.parametrize("headline", [
    "Democratic leaders announce a new platform",
    "Democrats announce a new platform",
    "A Democrat gains support in the latest poll",
])
def test_democratic_market_matches_party_variants(headline):
    watched = topic("Will Democrats hold at least 250 House seats after the 2028 election?")
    assert matches(source(headline), watched)


@pytest.mark.parametrize("headline", [
    "At least 69 people killed in a train crash south of Madrid",
    "College students throw a birthday party",
    "New candidate announced for college principal",
    "Negotiations between transport officials resume in Spain",
])
def test_generic_question_words_do_not_admit_unrelated_articles(headline):
    watched = topic("Will the Republican candidate win the 2028 presidential election "
                    "by between 91 and 130 Electoral College votes?")
    seats = topic("Will Democrats hold at least 250 House seats after the 2028 election?")
    assert not matches(source(headline), watched)
    assert not matches(source(headline), seats)


def test_explicit_thesis_entity_can_supply_discovery_aliases():
    watched = topic("Will Senate control change in 2028?", "Republicans win in 2028.")
    assert matches(source("GOP announces a new platform"), watched)
    # Discovery never changes the exact question or thesis the classifier verifies.
    assert watched["contract"]["question"] == "Will Senate control change in 2028?"
    assert watched["contract"]["thesis"] == "Republicans win in 2028."


def test_nonpolitical_markets_keep_distinctive_subject_matching():
    watched = topic("Will Bitcoin be above $100,000 before December 31, 2028?")
    assert matches(source("Bitcoin surges as institutional demand grows"), watched)
    assert not matches(source("At least ten people gather above a station"), watched)


def test_question_without_distinctive_words_does_not_match_every_article():
    watched = topic("Will at least one candidate win before the election?")
    assert not matches(source("At least one candidate drops out"), watched)
