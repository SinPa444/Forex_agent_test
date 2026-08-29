"""
ui/config.py
============

UI-only configuration. Reads non-secret defaults from the environment.
API keys are NEVER read or exposed here — LLM construction happens
server-side via orchestration.run_phase3.build_llm().
"""

import os

# How long a live analysis result is reused before a new LLM run is allowed.
CACHE_TTL_MINUTES = int(os.getenv("UI_CACHE_TTL_MINUTES", "15"))

# LLM provider/model defaults (server-side; not user-selectable in the UI).
LLM_PROVIDER = os.getenv("UI_LLM_PROVIDER", "arvan")
LLM_MODEL = os.getenv("UI_LLM_MODEL", "")

# Technical timeframes the user may force when "Override Timeframe" is on.
TIMEFRAME_CHOICES = ["M15", "H1", "H4", "D1"]

# How many log lines are kept visible in the live status log.
LOG_TAIL_LINES = 40

# History tab defaults.
HISTORY_RANGE = "last_30_days"
