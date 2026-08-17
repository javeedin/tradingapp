"""End-to-end checks on the option order endpoints.

The endpoint wiring is where option orders are most likely to break — a keyword
that does not reach the broker, a contract key that never becomes the position
key — and none of that is visible from unit tests of the planner.

The app's lifespan is deliberately not started: it would build a trader against
the real DuckDB file and start the scheduler. Injecting a paper trader into
`state` exercises the same request handlers without either.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import main as api
from app.broker.paper import PaperBroker
from app.data.breeze_client import BreezeClient
from app.data.store import MarketStore
from app.engine.live import LiveTrader
from app.models import DEFAULT_INTRADAY_PRODUCT, ProductType
from app.risk.manager import RiskManager
from app.strategy.signals import SignalEngine

# Far enough out that no test trips the near-expiry warning by accident.
EXPIRY = (date.today() + timedelta(days=45)).isoformat()


@pytest.fixture
def client(tmp_path, monkeypatch):
    store = MarketStore(tmp_path / "api.duckdb")
    trader = LiveTrader(
        broker=PaperBroker(starting_capital=1_000_000),
        client=BreezeClient(api_key="k", api_secret="s"),
        store=store,
        engine=SignalEngine(),
        risk=RiskManager(),
        product=DEFAULT_INTRADAY_PRODUCT,
    )

    previous = api.state.get("trader")
    api.state["trader"] = trader
    # Not connected to Breeze in tests, so every premium must come from the
    # request. Anything that tries to quote should surface as a clear 400.
    monkeypatch.setattr(api, "fetch_premium", lambda *_a, **_k: None)

    # TestClient used without `with`, so lifespan never runs.
    yield TestClient(api.app), trader

    api.state["trader"] = previous
    store.close()


def payload(**kwargs):
    defaults = dict(
        underlying="NIFTY",
        expiry=EXPIRY,
        strike=24300,
        right="call",
        side="buy",
        lots=1,
        premium=120.0,
    )
    defaults.update(kwargs)
    return defaults


# ----------------------------------------------------------------------
# Preview
# ----------------------------------------------------------------------
def test_preview_prices_a_lot_without_placing_anything(client):
    http, trader = client
    response = http.post("/api/options/preview", json=payload())

    assert response.status_code == 200
    body = response.json()
    assert body["quantity"] == 75
    assert body["stoploss"] < 120.0 < body["target"]
    assert trader.broker.open_position_count == 0


def test_preview_without_a_premium_and_without_a_session_says_why(client):
    http, _ = client
    response = http.post("/api/options/preview", json=payload(premium=None))

    assert response.status_code == 400
    assert "No premium available" in response.json()["detail"]


def test_preview_rejects_an_unknown_right(client):
    http, _ = client
    assert http.post("/api/options/preview", json=payload(right="spread")).status_code == 400


def test_preview_rejects_zero_lots_at_the_schema(client):
    http, _ = client
    assert http.post("/api/options/preview", json=payload(lots=0)).status_code == 422


# ----------------------------------------------------------------------
# Placement
# ----------------------------------------------------------------------
def test_placing_an_option_order_opens_a_position_keyed_by_contract(client):
    http, trader = client
    response = http.post("/api/options/orders", json=payload())

    assert response.status_code == 200
    body = response.json()
    key = body["order"]["contract"]["key"]

    assert key == f"NIFTY 24300 CE {date.fromisoformat(EXPIRY).strftime('%d%b%y').upper()}"
    assert trader.broker.has_position(key)
    assert trader.broker.get_position(key).product is ProductType.OPTIONS


def test_the_same_series_cannot_be_opened_twice(client):
    http, _ = client
    http.post("/api/options/orders", json=payload())
    again = http.post("/api/options/orders", json=payload())

    assert again.status_code == 409


def test_a_call_and_a_put_on_one_strike_are_separate_positions(client):
    http, trader = client
    assert http.post("/api/options/orders", json=payload(right="call")).status_code == 200
    assert http.post("/api/options/orders", json=payload(right="put")).status_code == 200

    assert trader.broker.open_position_count == 2


def test_an_expired_contract_is_refused(client):
    http, _ = client
    past = (date.today() - timedelta(days=1)).isoformat()
    response = http.post("/api/options/orders", json=payload(expiry=past))

    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_an_unaffordable_order_is_refused_and_still_recorded(client):
    """The most common failure must not be a silent gap in the history."""
    http, trader = client
    response = http.post("/api/options/orders", json=payload(lots=500))

    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Insufficient funds" in detail
    assert "largest affordable size" in detail

    recorded = trader.store.recent_orders(product="options")
    assert len(recorded) == 1
    assert recorded[0]["status"] == "rejected"


def test_writing_an_option_is_permitted_but_blocks_underlying_margin(client):
    http, trader = client
    before = trader.broker.cash

    response = http.post("/api/options/orders", json=payload(side="sell"))

    assert response.status_code == 200
    blocked = before - trader.broker.cash
    # Margin against the 24,300 strike, not the ₹9,000 premium collected.
    assert blocked > 100_000


def test_the_plan_returned_on_placement_carries_the_writer_warning(client):
    http, _ = client
    body = http.post("/api/options/orders", json=payload(side="sell")).json()
    assert any("unbounded" in w for w in body["plan"]["warnings"])


# ----------------------------------------------------------------------
# History and positions
# ----------------------------------------------------------------------
def test_option_history_excludes_equity_orders(client):
    http, trader = client
    trader.broker.buy(symbol="RELIND", quantity=1, price=1000.0, stoploss=980.0, target=1040.0)
    trader.store.save_order(trader.broker.orders[-1], mode="paper")
    http.post("/api/options/orders", json=payload())

    body = http.get("/api/options/orders").json()

    assert [o["symbol"] for o in body["orders"]] == [
        f"NIFTY 24300 CE {date.fromisoformat(EXPIRY).strftime('%d%b%y').upper()}"
    ]


def test_option_positions_lists_only_option_legs(client):
    http, trader = client
    trader.broker.buy(symbol="RELIND", quantity=1, price=1000.0, stoploss=980.0, target=1040.0)
    http.post("/api/options/orders", json=payload())

    body = http.get("/api/options/positions").json()

    assert body["count"] == 1
    assert body["positions"][0]["contract"]["underlying"] == "NIFTY"


def test_option_positions_reports_that_premiums_were_not_repriced(client):
    """Without a session P&L stays at zero, and the UI needs to be able to say so."""
    http, _ = client
    http.post("/api/options/orders", json=payload())

    body = http.get("/api/options/positions").json()
    assert body["repriced"] == 0


def test_an_option_position_can_be_closed_through_the_normal_endpoint(client):
    http, trader = client
    key = http.post("/api/options/orders", json=payload()).json()["order"]["contract"]["key"]

    response = http.post("/api/positions/close", json={"symbol": key, "price": 200.0})

    assert response.status_code == 200
    assert not trader.broker.has_position(key)
    assert response.json()["closed"]["contract"]["strike"] == 24300.0


# ----------------------------------------------------------------------
# Engine management
# ----------------------------------------------------------------------
def test_the_cycle_exits_an_option_that_hit_its_target(client, monkeypatch):
    """The candle path skips option legs, so without the premium path a paper
    option would sit past its target forever."""
    http, trader = client
    body = http.post("/api/options/orders", json=payload()).json()
    key = body["order"]["contract"]["key"]
    target = body["plan"]["target"]

    monkeypatch.setattr(
        "app.engine.live.fetch_premium", lambda *_a, **_k: target + 5.0
    )
    actions = trader._manage_positions(api.datetime.now().replace(hour=11))

    assert not trader.broker.has_position(key)
    assert [a["reason"] for a in actions] == ["target"]


def test_the_cycle_exits_an_option_that_hit_its_stop(client, monkeypatch):
    http, trader = client
    body = http.post("/api/options/orders", json=payload()).json()
    key = body["order"]["contract"]["key"]

    monkeypatch.setattr(
        "app.engine.live.fetch_premium", lambda *_a, **_k: body["plan"]["stoploss"] - 1.0
    )
    trader._manage_positions(api.datetime.now().replace(hour=11))

    assert not trader.broker.has_position(key)


def test_an_unquotable_option_is_left_alone_rather_than_closed_at_a_guess(client, monkeypatch):
    http, trader = client
    key = http.post("/api/options/orders", json=payload()).json()["order"]["contract"]["key"]

    monkeypatch.setattr("app.engine.live.fetch_premium", lambda *_a, **_k: None)
    actions = trader._manage_positions(api.datetime.now().replace(hour=11))

    assert trader.broker.has_position(key)
    assert actions == []


def test_an_option_holding_its_levels_is_repriced_but_kept(client, monkeypatch):
    http, trader = client
    key = http.post("/api/options/orders", json=payload()).json()["order"]["contract"]["key"]

    monkeypatch.setattr("app.engine.live.fetch_premium", lambda *_a, **_k: 130.0)
    trader._manage_positions(api.datetime.now().replace(hour=11))

    position = trader.broker.get_position(key)
    assert position is not None
    assert position.last_price == 130.0
    assert position.unrealized_pnl > 0
