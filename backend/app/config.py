"""Application configuration, loaded from environment / .env."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root is two levels up from this file: backend/app/config.py -> repo/
REPO_ROOT = Path(__file__).resolve().parents[2]

LIVE_CONFIRMATION_PHRASE = "I_UNDERSTAND_THE_RISK"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Breeze credentials ---
    breeze_api_key: str = ""
    breeze_api_secret: str = ""
    breeze_session_token: str = ""

    # --- Mode ---
    trading_mode: str = "paper"
    live_trading_confirmation: str = ""

    # --- Capital & risk ---
    starting_capital: float = 100_000.0
    risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 3.0
    max_open_positions: int = 5
    max_position_pct: float = 20.0

    # --- Stop / target ---
    atr_stop_multiplier: float = 1.5
    atr_target_multiplier: float = 2.5
    use_trailing_stop: bool = True
    trail_atr_multiplier: float = 1.5

    # --- Exit policy ---
    # What happens when a position moves against you. See models.ExitPolicy.
    exit_policy: str = "stop_only"
    max_adds: int = 2
    add_trigger_atr: float = 1.0
    max_symbol_exposure_pct: float = 25.0

    # --- Signals ---
    signal_entry_threshold: float = 0.35
    signal_exit_threshold: float = 0.15

    # --- Robotic (automated) trading ---
    robotic_trading: bool = False
    robotic_max_positions: int = 6
    robotic_universe_size: int = 20

    # --- Live safety ---
    # Hard ceiling on a single live order. 0 disables the check.
    max_order_value: float = 0.0

    # --- Universe & timing ---
    equity_universe: str = "RELIND,TCS,INFTEC,HDFBAN,ICIBAN"
    benchmark_code: str = "NIFTY"
    candle_interval: str = "5minute"
    intraday_squareoff_time: str = "15:10"

    # --- Costs ---
    brokerage_pct: float = 0.03
    slippage_pct: float = 0.05

    # --- Infra ---
    database_path: str = "data/market.duckdb"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    @field_validator("trading_mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in {"paper", "live"}:
            raise ValueError("TRADING_MODE must be 'paper' or 'live'")
        return v

    @field_validator("exit_policy")
    @classmethod
    def _validate_exit_policy(cls, v: str) -> str:
        allowed = {"stop_only", "capped_averaging", "average_no_stop"}
        normalised = v.strip().lower()
        if normalised not in allowed:
            raise ValueError(f"EXIT_POLICY must be one of {sorted(allowed)}")
        return normalised

    @field_validator("candle_interval")
    @classmethod
    def _validate_interval(cls, v: str) -> str:
        # Intervals accepted by Breeze's get_historical_data_v2.
        allowed = {"1second", "1minute", "5minute", "30minute", "1day"}
        if v not in allowed:
            raise ValueError(f"CANDLE_INTERVAL must be one of {sorted(allowed)}")
        return v

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------
    @property
    def universe(self) -> list[str]:
        return [c.strip().upper() for c in self.equity_universe.split(",") if c.strip()]

    @property
    def squareoff_time(self) -> time:
        hh, mm = self.intraday_squareoff_time.split(":")
        return time(int(hh), int(mm))

    @property
    def db_path(self) -> Path:
        p = Path(self.database_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def is_live(self) -> bool:
        """Live trading requires BOTH the mode flag and the explicit confirmation phrase.

        The two-key design is deliberate: flipping TRADING_MODE alone — by a stray
        env var, a copied deploy config, or a typo — must never be enough to start
        sending real orders.
        """
        return (
            self.trading_mode == "live"
            and self.live_trading_confirmation.strip() == LIVE_CONFIRMATION_PHRASE
        )

    @property
    def risk_fraction(self) -> float:
        return self.risk_per_trade_pct / 100.0

    def live_mode_error(self) -> str | None:
        """Explain why live mode is not active, or None if it is."""
        if self.trading_mode != "live":
            return None
        if self.live_trading_confirmation.strip() != LIVE_CONFIRMATION_PHRASE:
            return (
                "TRADING_MODE=live but LIVE_TRADING_CONFIRMATION is not set to "
                f"'{LIVE_CONFIRMATION_PHRASE}'. Falling back to paper trading."
            )
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
