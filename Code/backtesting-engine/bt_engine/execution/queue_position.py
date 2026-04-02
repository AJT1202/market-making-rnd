"""Queue position assignment and drain model."""

import numpy as np

from bt_engine.execution.order import SimOrder
from bt_engine.types import QueueMode


class QueuePositionModel:
    """Assigns initial queue position when an order becomes visible."""

    def __init__(self, mode: QueueMode, seed: int = 42):
        self.mode = mode
        self._rng = np.random.RandomState(seed)

    def assign_queue_position(self, order: SimOrder, depth_at_price_cs: int) -> int:
        """Returns queue_ahead_cs based on mode.

        CONSERVATIVE: all existing depth is ahead (back of queue).
        PROBABILISTIC: uniform random fraction of existing depth.
        OPTIMISTIC: 0 (front of queue).
        """
        if self.mode == QueueMode.OPTIMISTIC:
            queue_ahead = 0
        elif self.mode == QueueMode.CONSERVATIVE:
            queue_ahead = depth_at_price_cs
        else:  # PROBABILISTIC
            fraction = self._rng.uniform(0.0, 1.0)
            queue_ahead = int(depth_at_price_cs * fraction)

        order.queue_ahead_cs = queue_ahead
        return queue_ahead

    def drain_queue(self, order: SimOrder, trade_size_cs: int) -> int:
        """Drain queue_ahead by trade volume. Returns remaining queue_ahead."""
        drained = min(order.queue_ahead_cs, trade_size_cs)
        order.queue_ahead_cs -= drained
        return order.queue_ahead_cs
