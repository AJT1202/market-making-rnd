"""Latency model for order submission and cancellation delays."""

from bt_engine.config import LatencyConfig


class LatencyModel:
    """Computes latency delays. CONSTANT mode only for now."""

    def __init__(self, config: LatencyConfig):
        self.config = config

    @property
    def submit_us(self) -> int:
        return self.config.submit_us

    @property
    def visible_us(self) -> int:
        return self.config.visible_us

    @property
    def cancel_us(self) -> int:
        return self.config.cancel_us
