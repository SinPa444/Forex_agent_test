"""
agents/technical/data/circuit_breaker.py
========================================
Circuit Breaker سبک برای تماس‌های خارجی (yfinance).
الگو: corepkg/policies/circuit_breaker از AgenticTrading — ساده‌سازی‌شده
برای تک‌پروسه (thread-safe).

حالت‌ها:
  CLOSED    → تماس مجاز
  OPEN      → همه تماس‌ها رد می‌شوند (failure_threshold شکست پشت‌سرهم)
  HALF_OPEN → بعد از recovery_timeout، یک تماس آزمایشی مجاز است
"""

from __future__ import annotations

import logging
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitBreakerState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """
    مانیتور شکست‌های پیاپی یک سرویس خارجی.

    Args:
        failure_threshold: تعداد شکست مجاز قبل از OPEN شدن
        recovery_timeout: ثانیه تا حالت HALF_OPEN بعد از OPEN
    """

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 300.0) -> None:
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout

        self._state = CircuitBreakerState.CLOSED
        self._failure_count = 0
        self._last_failure_time: float | None = None
        self._lock = threading.RLock()

    @property
    def state(self) -> CircuitBreakerState:
        with self._lock:
            if self._state is CircuitBreakerState.OPEN and self._should_attempt_reset():
                self._state = CircuitBreakerState.HALF_OPEN
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failure_count

    def allow(self) -> bool:
        """آیا تماس جدید مجاز است؟"""
        return self.state is not CircuitBreakerState.OPEN

    def record_success(self) -> None:
        with self._lock:
            if self._state is CircuitBreakerState.HALF_OPEN:
                logger.info("[CircuitBreaker] HALF_OPEN → CLOSED (success)")
            self._state = CircuitBreakerState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state is CircuitBreakerState.HALF_OPEN:
                self._state = CircuitBreakerState.OPEN
                logger.warning("[CircuitBreaker] HALF_OPEN → OPEN (failure after probe)")
            elif self._failure_count >= self.failure_threshold:
                if self._state is not CircuitBreakerState.OPEN:
                    logger.warning(
                        "[CircuitBreaker] CLOSED → OPEN after %d failures",
                        self._failure_count,
                    )
                self._state = CircuitBreakerState.OPEN

    def _should_attempt_reset(self) -> bool:
        if self._last_failure_time is None:
            return False
        return (time.monotonic() - self._last_failure_time) >= self.recovery_timeout

    def reset(self) -> None:
        """بازتست دستی (برای tests)."""
        with self._lock:
            self._state = CircuitBreakerState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None
