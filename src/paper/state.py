"""Paper trading state — pure (P6).

Position + Balance dataclasses + state transitions. No I/O.

References:
- ADR-007 paper sizing $1000
- ADR-010 risk management (단일 ≤2%, 일일 ≤5%)
- INSIGHT-007 fee 0.0014 round-trip (OKX SPOT LV3+ taker 0.07% × 2)
- LESSON-001 NULL cascade prevention
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from src.domain.metrics import DEFAULT_FEE_ROUND_TRIP


class PositionStatus(Enum):
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Position:
    """Paper position (long-only — Polaris ADR-001 SPOT).

    LESSON-001: numeric column never None — defaults explicit.
    """

    position_id: str  # 보통 "{ticker}-{open_ts_ms}"
    ticker: str
    direction: int  # +1 long (Polaris SPOT). -1 reserved for ADR-009 PERP.
    entry_price: float
    size_usd: float
    open_ts_ms: int
    close_ts_ms: int = 0
    exit_price: float = 0.0
    fee_round_trip: float = DEFAULT_FEE_ROUND_TRIP
    status: PositionStatus = PositionStatus.OPEN

    def __post_init__(self) -> None:
        if self.entry_price <= 0:
            raise ValueError(f"entry_price must be > 0, got {self.entry_price}")
        if self.size_usd <= 0:
            raise ValueError(f"size_usd must be > 0, got {self.size_usd}")
        if self.direction not in (1, -1):
            raise ValueError(f"direction must be +1 or -1, got {self.direction}")
        if self.open_ts_ms <= 0:
            raise ValueError(f"open_ts_ms must be > 0, got {self.open_ts_ms}")

    @property
    def is_open(self) -> bool:
        return self.status == PositionStatus.OPEN

    def close(self, exit_price: float, close_ts_ms: int) -> "Position":
        """새 closed Position 반환 (immutable). already CLOSED 재종료 차단 (codex fix)."""
        if not self.is_open:
            raise ValueError(
                f"position {self.position_id} already closed (status={self.status.value})"
            )
        if exit_price <= 0:
            raise ValueError(f"exit_price must be > 0, got {exit_price}")
        if close_ts_ms < self.open_ts_ms:
            raise ValueError(
                f"close_ts_ms {close_ts_ms} < open_ts_ms {self.open_ts_ms}"
            )
        return replace(
            self,
            exit_price=exit_price,
            close_ts_ms=close_ts_ms,
            status=PositionStatus.CLOSED,
        )

    @property
    def gross_pct(self) -> float:
        """Gross return % (signed). 0 if not closed."""
        if not self.is_open and self.exit_price > 0:
            return self.direction * (self.exit_price - self.entry_price) / self.entry_price
        return 0.0

    @property
    def net_pct(self) -> float:
        """Net return % after fee. 0 if not closed."""
        if self.is_open or self.exit_price <= 0:
            return 0.0
        return self.gross_pct - self.fee_round_trip

    @property
    def net_usd(self) -> float:
        """Net P&L in USD."""
        return self.size_usd * self.net_pct


@dataclass(frozen=True, slots=True)
class PaperBalance:
    """Paper account balance + open positions.

    Immutable — state transitions via factory methods.
    """

    starting_usd: float
    cash_usd: float
    open_positions: tuple[Position, ...] = field(default_factory=tuple)
    closed_positions: tuple[Position, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.starting_usd <= 0:
            raise ValueError(f"starting_usd must be > 0, got {self.starting_usd}")

    @property
    def n_open(self) -> int:
        return len(self.open_positions)

    @property
    def n_closed(self) -> int:
        return len(self.closed_positions)

    @property
    def realized_pnl_usd(self) -> float:
        return sum(p.net_usd for p in self.closed_positions)

    def equity_usd(self, current_prices: dict[str, float]) -> float:
        """Total equity = cash + open positions market value (당일 종가 기준).

        current_prices: {ticker: price}.
        Open position이지만 current_prices에 없으면 entry_price 사용 (보수적).
        """
        equity = self.cash_usd
        for pos in self.open_positions:
            price = current_prices.get(pos.ticker, pos.entry_price)
            # market value = size_usd × (1 + gross_pct)
            unreal_gross = pos.direction * (price - pos.entry_price) / pos.entry_price
            equity += pos.size_usd * (1.0 + unreal_gross)
        return equity

    def open(self, position: Position) -> "PaperBalance":
        """새 position open — cash 차감 + open_positions 추가."""
        if position.size_usd > self.cash_usd:
            raise ValueError(
                f"insufficient cash: need {position.size_usd}, have {self.cash_usd}"
            )
        return replace(
            self,
            cash_usd=self.cash_usd - position.size_usd,
            open_positions=self.open_positions + (position,),
        )

    def close(self, position_id: str, exit_price: float, close_ts_ms: int) -> "PaperBalance":
        """Position close — open_positions에서 제거 + closed_positions로 + cash + size + net 환원."""
        target = next((p for p in self.open_positions if p.position_id == position_id), None)
        if target is None:
            raise ValueError(f"position not found: {position_id}")
        closed = target.close(exit_price=exit_price, close_ts_ms=close_ts_ms)
        # cash = cash + size_usd (원금) + net_usd (P&L)
        return replace(
            self,
            cash_usd=self.cash_usd + target.size_usd + closed.net_usd,
            open_positions=tuple(p for p in self.open_positions if p.position_id != position_id),
            closed_positions=self.closed_positions + (closed,),
        )
