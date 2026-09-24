"""Constants for the Hunch integration."""

from __future__ import annotations

import dataclasses

from hunch import Thresholds

DOMAIN = "hunch"

CONF_API_KEY = "api_key"

OPT_FALLBACK_AGENT = "fallback_agent"
OPT_MODEL = "model"
DEFAULT_MODEL = "jev-1.13.0"
OPT_RESPONSE_LANGUAGE = "response_language"
DEFAULT_RESPONSE_LANGUAGE = "auto"
OPT_TIMEOUT_MS = "timeout_ms"
DEFAULT_TIMEOUT_MS = 1500
OPT_MAX_SILENT_TARGETS = "max_silent_targets"
OPT_DEVICE_ROUND = "device_round"
OPT_MAX_ROUNDS = "max_rounds"

PENDING_TTL_SECONDS = 120
TRACE_BUFFER = 50

OPT_TIMER_SCRIPT = "timer_script"
EVENT_TIMER_FINISHED = "hunch_timer_finished"
TIMER_STORE_KEY = f"{DOMAIN}.timers"
TIMER_STORE_VERSION = 1
MAX_OVERDUE_SECONDS = 3600  # a stored device action later than this is not carried out

# Thresholds are stored flat in a config entry's options as `threshold_<name>` keys
# (not a nested dict), one per Thresholds field. Derived, not hand-typed, so it can
# never drift from hunch.config.Thresholds.
THRESHOLD_FIELDS = tuple(f.name for f in dataclasses.fields(Thresholds))
