"""BacktestEngine: the 5-phase event loop.

Processes the unified timeline in strict chronological order:
  Phase 1: External events (snapshots, trades, underlying prices)
  Phase 2: Internal events (ORDER_VISIBLE, CANCEL_EFFECTIVE)
  Phase 3: Fill checks (snapshot-driven or trade-driven)
  Phase 4: Fair value recomputation
  Phase 5: Strategy update and action processing
"""

import datetime

from bt_engine.config import EngineConfig
from bt_engine.types import EventKind, FillMode, Side, TokenSide, OrderStatus
from bt_engine.units import bps_to_ticks, ticks_to_price, cs_to_shares, tc_to_dollars
from bt_engine.data.schema import BookSnapshot, TradeEvent, UnderlyingPrice
from bt_engine.data.store import DataStore
from bt_engine.engine.internal_queue import InternalEventQueue
from bt_engine.execution.latency import LatencyModel
from bt_engine.execution.order import OrderManager, Fill
from bt_engine.execution.queue_position import QueuePositionModel
from bt_engine.execution.fill_engine import TradeDrivenFillEngine
from bt_engine.execution.fill_engine_snapshot import SnapshotFillEngine
from bt_engine.fair_value.pricer import BlackScholesPricer
from bt_engine.fair_value.manager import FairValueManager
from bt_engine.portfolio.positions import Portfolio
from bt_engine.strategy.interface import Strategy, StrategyAction, StrategyUpdate
from bt_engine.analytics.journal import AuditJournal


class BacktestEngine:
    """Event-driven backtesting engine for binary event market making."""

    def __init__(
        self,
        config: EngineConfig,
        data: DataStore,
        strategy: Strategy,
    ):
        self.config = config
        self.data = data
        self.strategy = strategy

        # Components
        self.latency = LatencyModel(config.latency)
        self.order_mgr = OrderManager(self.latency)
        self.queue_model = QueuePositionModel(config.fill.queue_mode, seed=config.seed)
        self.portfolio = Portfolio(
            initial_cash_tc=config.initial_cash_tc,
            strikes=data.strikes,
            mode=config.position_mode,
        )
        self.journal = AuditJournal()

        # Fair value
        pricer = BlackScholesPricer(sigma=config.sigma, r=config.risk_free_rate)
        self.fv_manager = FairValueManager(
            pricer=pricer,
            strikes=data.strikes,
            expiry_utc_us=config.event.expiry_utc_us,
        )

        # Fill engine
        if data.fill_mode == FillMode.TRADE_DRIVEN:
            self.fill_engine_trade = TradeDrivenFillEngine(self.queue_model)
            self.fill_engine_snapshot = None
        else:
            self.fill_engine_trade = None
            self.fill_engine_snapshot = SnapshotFillEngine(
                cancel_discount=config.fill.cancel_discount,
            )

        # Internal event queue
        self.internal_queue = InternalEventQueue()

        # State
        self.current_time_us: int = 0
        self.latest_underlying_cents: int = 0
        self.latest_fair_values: dict[int, int] = {}  # strike -> bps
        self.fills: list[Fill] = []
        self.settlement_results: dict[int, int] = {}

        # H2: Map order_id -> snapshot payload_index at decision time
        self._order_decision_snap: dict[str, int] = {}

        # C4: Track previous snapshot timestamp per (strike, token_side) for
        # lookahead guard on BBO-movement fills
        self._prev_snapshot_ts: dict[tuple[int, TokenSide], int] = {}

        # Progress
        self._snapshots_processed = 0
        self._total_orders = 0

    def run(self) -> "BacktestResult":
        """Run the full backtest. Returns a BacktestResult."""
        timeline = self.data.timeline
        n = len(timeline)
        if n == 0:
            return self._build_result()

        print(f"Running backtest: {n:,} timeline events, "
              f"fill_mode={self.data.fill_mode.name}, "
              f"{len(self.data.strikes)} strikes")

        progress_interval = max(1, n // 10)

        for i, event in enumerate(timeline):
            # M2: Process any internal events that should fire BEFORE this
            # external event (their timestamp is strictly earlier).
            while not self.internal_queue.empty:
                next_internal_ts = self.internal_queue.peek_timestamp()
                if next_internal_ts is not None and next_internal_ts < event.timestamp_us:
                    self.current_time_us = next_internal_ts
                    for internal in self.internal_queue.pop_events_at(next_internal_ts):
                        self._process_internal_event(internal)
                else:
                    break

            self.current_time_us = event.timestamp_us

            # --- Phase 1: Process external event ---
            if event.kind == EventKind.UNDERLYING_PRICE:
                self._on_underlying_price(event.payload_index)

            elif event.kind == EventKind.BOOK_SNAPSHOT:
                snap = self.data.get_snapshot(event.payload_index)
                self.data.update_latest_snapshot(
                    event.strike, event.token_side, event.payload_index
                )
                self._on_book_snapshot(snap)

            elif event.kind == EventKind.TRADE:
                trade = self.data.get_trade(event.payload_index)
                self._on_trade(trade)

            # --- Phase 2: Process internal events due at this timestamp ---
            for internal in self.internal_queue.pop_events_at(self.current_time_us):
                self._process_internal_event(internal)

            # --- Progress ---
            if (i + 1) % progress_interval == 0:
                pct = (i + 1) / n * 100
                dt = datetime.datetime.fromtimestamp(
                    self.current_time_us / 1e6, tz=datetime.timezone.utc
                )
                print(f"  [{pct:5.1f}%] {dt.strftime('%H:%M:%S')} UTC | "
                      f"fills: {len(self.fills)} | orders: {self._total_orders}")

        # Drain remaining internal events
        for internal in self.internal_queue.pop_events_up_to(2**63 - 1):
            self._process_internal_event(internal)

        # Settle
        self._settle()

        print(f"  Done: {self._snapshots_processed:,} snapshots, "
              f"{len(self.fills)} fills, {self._total_orders} orders, "
              f"final cash: ${tc_to_dollars(self.portfolio.cash_tc):,.2f}")

        return self._build_result()

    # --- Phase 1 handlers ---

    def _on_underlying_price(self, payload_index: int) -> None:
        up = self.data.get_underlying_price(payload_index)
        self.latest_underlying_cents = up.price_cents

        # Recompute fair values
        if self.latest_underlying_cents > 0:
            self.latest_fair_values = self.fv_manager.compute_all(
                self.latest_underlying_cents, self.current_time_us
            )

    def _on_book_snapshot(self, snap: BookSnapshot) -> None:
        self._snapshots_processed += 1

        if not snap.is_valid:
            return

        # Phase 3a: Snapshot-mode fill check
        if self.fill_engine_snapshot is not None:
            resting = self.order_mgr.get_resting_orders(snap.strike, snap.token_side)
            if resting:
                # C4: Only orders visible at or before the previous snapshot
                # are eligible for BBO-movement fills. This prevents an order
                # placed at snapshot T from filling on the T→T+1 movement
                # (which would be a free option / lookahead bias).
                # With zero latency: order placed at T has visible_ts=T,
                # prev_ts=T (set when T was processed), so visible_ts <= prev_ts
                # is true — the order is eligible when T+1 arrives.
                prev_ts = self._prev_snapshot_ts.get((snap.strike, snap.token_side), 0)
                eligible = [o for o in resting if o.visible_ts_us <= prev_ts]
                if eligible:
                    fills = self.fill_engine_snapshot.check_fills(eligible, snap)
                    for fill in fills:
                        # C1: Use apply_fill to update SimOrder state atomically
                        fill = self.order_mgr.apply_fill(
                            fill.order_id,
                            fill.filled_cs,
                            fill.timestamp_us,
                            fill.is_aggressive,
                        )
                        self._process_fill(fill)
                else:
                    # Still need to update prev_bbo state in fill engine even
                    # when no eligible orders, so pass empty list
                    self.fill_engine_snapshot.check_fills([], snap)

        # C4: Update prev snapshot timestamp AFTER fill check, BEFORE strategy
        self._prev_snapshot_ts[(snap.strike, snap.token_side)] = snap.timestamp_us

        # Phase 4: Fair value is already computed from underlying price events
        # Phase 5: Strategy update
        if not self._should_run_strategy():
            return

        fv_bps = self.latest_fair_values.get(snap.strike, 0)
        if fv_bps == 0 and self.latest_underlying_cents == 0:
            return

        pos = self.portfolio.positions.get(snap.strike)
        update = StrategyUpdate(
            timestamp_us=self.current_time_us,
            strike=snap.strike,
            token_side=snap.token_side,
            best_bid_ticks=snap.best_bid_ticks,
            best_ask_ticks=snap.best_ask_ticks,
            best_bid_size_cs=snap.best_bid_size_cs,
            best_ask_size_cs=snap.best_ask_size_cs,
            mid_ticks_x2=snap.mid_ticks_x2,
            spread_ticks=snap.spread_ticks,
            fair_value_bps=fv_bps,
            underlying_price_cents=self.latest_underlying_cents,
            position_yes_cs=pos.yes_position_cs if pos else 0,
            position_no_cs=pos.no_position_cs if pos else 0,
            available_cash_tc=self.portfolio.available_cash_tc(),
        )

        actions = self.strategy.on_market_update(update)
        self._process_actions(actions, snap.strike, snap.token_side)

    def _on_trade(self, trade: TradeEvent) -> None:
        """Handle a trade event (trade-driven fill mode)."""
        if self.fill_engine_trade is None:
            return

        resting = self.order_mgr.get_resting_orders(trade.strike, trade.token_side)
        if not resting:
            return

        fills = self.fill_engine_trade.check_fills_on_trade(resting, trade)
        for fill in fills:
            # C1: Use apply_fill to update SimOrder state (remaining_cs, status)
            fill = self.order_mgr.apply_fill(
                fill.order_id,
                fill.filled_cs,
                fill.timestamp_us,
                fill.is_aggressive,
            )
            self._process_fill(fill)

    # --- Phase 2: Internal events ---

    def _process_internal_event(self, internal) -> None:
        if internal.kind == EventKind.ORDER_VISIBLE:
            order = self.order_mgr.get_order(internal.order_id)
            if order is None or not order.is_live:
                return

            # H2: Use the snapshot that was current at decision time
            snap_idx = self._order_decision_snap.get(internal.order_id, -1)
            if snap_idx >= 0:
                snap = self.data.get_snapshot(snap_idx)
            else:
                snap = self.data.latest_snapshot(order.strike, order.token_side)

            if snap is not None:
                depth = snap.depth_at_price(order.price_ticks)
                self.queue_model.assign_queue_position(order, depth)

                # Check for aggressive fill at visibility
                if self.fill_engine_trade is not None:
                    fill = self.fill_engine_trade.check_aggressive_fill(order, snap)
                    if fill:
                        self._process_fill(fill)

        elif internal.kind == EventKind.CANCEL_EFFECTIVE:
            order = self.order_mgr.get_order(internal.order_id)
            if order is not None:
                # H5: If already fully filled, no reservation to release
                if order.remaining_cs <= 0 or order.status == OrderStatus.FILLED:
                    self.order_mgr.cancel_effective(internal.order_id)
                    self.journal.record(
                        self.current_time_us, "CANCEL_EFFECTIVE",
                        order_id=internal.order_id,
                    )
                    return

                # C2: Release reservation proportional to unfilled portion.
                # apply_fill already released the filled portion's reservation,
                # so release only what corresponds to remaining_cs.
                # reserved_tc was set at order placement for the full order size.
                # Proportional release: reserved_tc * remaining_cs // size_cs
                if order.size_cs > 0:
                    release_tc = order.reserved_tc * order.remaining_cs // order.size_cs
                else:
                    release_tc = 0

                self.order_mgr.cancel_effective(internal.order_id)
                self.portfolio.release_reservation(order.strike, release_tc)
                self.journal.record(
                    self.current_time_us, "CANCEL_EFFECTIVE",
                    order_id=internal.order_id,
                )

    # --- Fill processing ---

    def _process_fill(self, fill: Fill) -> None:
        self.fills.append(fill)

        # Update portfolio
        self.portfolio.apply_fill(
            strike=fill.strike,
            token_side=fill.token_side,
            side=fill.side,
            price_ticks=fill.price_ticks,
            size_cs=fill.filled_cs,
        )

        # Journal
        self.journal.record(
            fill.timestamp_us, "FILL",
            order_id=fill.order_id,
            strike=fill.strike,
            token_side=fill.token_side.value,
            side=fill.side.value,
            price_ticks=fill.price_ticks,
            size_cs=fill.filled_cs,
            is_aggressive=fill.is_aggressive,
        )

        # Notify strategy
        actions = self.strategy.on_fill(
            fill.strike, fill.token_side, fill.side,
            fill.price_ticks, fill.filled_cs,
        )
        if actions:
            self._process_actions(actions, fill.strike, fill.token_side)

    # --- Action processing ---

    def _process_actions(
        self, actions: list[StrategyAction], strike: int, token_side: TokenSide
    ) -> None:
        for action in actions:
            if action.kind == "CANCEL":
                self._handle_cancel(action)
            elif action.kind == "PLACE":
                self._handle_place(action)

    def _handle_place(self, action: StrategyAction) -> None:
        # Reserve cash
        reserved = self.portfolio.reserve_for_order(
            strike=action.strike,
            token_side=action.token_side,
            side=action.side,
            price_ticks=action.price_ticks,
            size_cs=action.size_cs,
        )
        if not reserved:
            return

        # Submit order
        order = self.order_mgr.submit_order(
            strike=action.strike,
            token_side=action.token_side,
            side=action.side,
            price_ticks=action.price_ticks,
            size_cs=action.size_cs,
            decision_ts_us=self.current_time_us,
        )

        # C2: Store the reserved amount on the order for correct cancel release.
        # BUY: price_ticks * size_cs; SELL short: (100 - price_ticks) * short_cs
        if action.side == Side.BUY:
            order.reserved_tc = action.price_ticks * action.size_cs
        else:
            # SELL: collateral is (100 - price_ticks) * short portion
            # Compute short_cs as the portion not covered by existing inventory
            pos = self.portfolio.positions.get(action.strike)
            if pos is not None:
                if action.token_side == TokenSide.YES:
                    inventory_cs = pos.yes_position_cs
                else:
                    inventory_cs = pos.no_position_cs
            else:
                inventory_cs = 0
            short_cs = max(0, action.size_cs - max(0, inventory_cs))
            order.reserved_tc = (100 - action.price_ticks) * short_cs

        self._total_orders += 1

        # H2: Record which snapshot was current at decision time
        self._order_decision_snap[order.order_id] = self.data._latest_snapshot_idx.get(
            (action.strike, action.token_side), -1
        )

        # Notify strategy of the real order ID (map placeholder -> engine ID)
        if hasattr(self.strategy, 'notify_order_id'):
            self.strategy.notify_order_id(action.order_id, order.order_id)

        # Schedule ORDER_VISIBLE internal event
        self.internal_queue.schedule(
            timestamp_us=order.visible_ts_us,
            kind=EventKind.ORDER_VISIBLE,
            order_id=order.order_id,
        )

        self.journal.record(
            self.current_time_us, "ORDER_SUBMIT",
            order_id=order.order_id,
            strike=order.strike,
            side=order.side.value,
            price_ticks=order.price_ticks,
            size_cs=order.size_cs,
        )

    def _handle_cancel(self, action: StrategyAction) -> None:
        order = self.order_mgr.get_order(action.order_id)
        if order is None or not order.is_live:
            return

        cancel_ts = self.order_mgr.request_cancel(
            action.order_id, self.current_time_us
        )

        # Schedule CANCEL_EFFECTIVE internal event
        self.internal_queue.schedule(
            timestamp_us=cancel_ts,
            kind=EventKind.CANCEL_EFFECTIVE,
            order_id=action.order_id,
        )

    # --- Helpers ---

    def _should_run_strategy(self) -> bool:
        """Check if strategy should run at current time."""
        if not self.config.only_market_hours:
            return True

        mh = self.config.market_hours
        dt = datetime.datetime.fromtimestamp(
            self.current_time_us / 1e6, tz=datetime.timezone.utc
        )
        time_min = dt.hour * 60 + dt.minute
        open_min = mh.open_hour * 60 + mh.open_minute
        close_min = mh.close_hour * 60 + mh.close_minute
        return open_min <= time_min < close_min

    def _settle(self) -> None:
        """Settle all positions at market resolution."""
        resolutions = self.config.event.resolutions
        resolved = {k: v for k, v in resolutions.items() if v is not None}

        if not resolved:
            print("  No resolutions configured, skipping settlement")
            return

        print("  Settling positions ...")
        for strike, resolved_yes in sorted(resolved.items()):
            pos = self.portfolio.positions.get(strike)
            if pos is None:
                continue
            # M3: Capture positions BEFORE settle() zeroes them out
            yes_before = pos.yes_position_cs
            no_before = pos.no_position_cs
            pnl_tc = self.portfolio.settle(strike, resolved_yes)
            self.settlement_results[strike] = pnl_tc
            self.journal.record(
                self.current_time_us, "SETTLEMENT",
                strike=strike,
                resolved_yes=resolved_yes,
                yes_pos_cs=yes_before,
                no_pos_cs=no_before,
                pnl_tc=pnl_tc,
            )
            print(f"    Strike {strike}: resolved={'YES' if resolved_yes else 'NO'}, "
                  f"settlement=${tc_to_dollars(pnl_tc):+.2f}")

        print(f"    Final cash: ${tc_to_dollars(self.portfolio.cash_tc):,.2f}")

    def _build_result(self) -> "BacktestResult":
        return BacktestResult(
            fills=self.fills,
            portfolio=self.portfolio,
            journal=self.journal,
            settlement_results=self.settlement_results,
            fair_values=self.latest_fair_values,
            config=self.config,
        )


class BacktestResult:
    """Container for backtest output."""

    def __init__(
        self,
        fills: list[Fill],
        portfolio: Portfolio,
        journal: AuditJournal,
        settlement_results: dict[int, int],
        fair_values: dict[int, int],
        config: EngineConfig,
    ):
        self.fills = fills
        self.portfolio = portfolio
        self.journal = journal
        self.settlement_results = settlement_results
        self.fair_values = fair_values
        self.config = config

    @property
    def total_fills(self) -> int:
        return len(self.fills)

    @property
    def final_cash_tc(self) -> int:
        return self.portfolio.cash_tc

    @property
    def final_cash_dollars(self) -> float:
        return tc_to_dollars(self.portfolio.cash_tc)
