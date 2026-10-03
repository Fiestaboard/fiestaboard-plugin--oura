"""Oura plugin for FiestaBoard.

Shows an Oura Ring owner's daily Sleep, Readiness and Activity scores, plus
last night's sleep (total sleep, resting heart rate, HRV, bedtime) and
today's steps.

Sign-in uses FiestaBoard's platform OAuth (the manifest's ``oauth`` block):
each user registers their own Oura API application, pastes its Client ID and
Client Secret into the plugin's settings and presses Sign in. The platform
runs the authorization-code flow (with PKCE), sends the secret to Oura's
token endpoint, stores the tokens and refreshes them. Oura refresh tokens
are single-use: every refresh returns a new one, which the platform stores.
This plugin only ever calls ``self.get_oauth_token()``.

Endpoints (Oura API v2, ``https://api.ouraring.com/v2/usercollection``),
each with ``start_date``/``end_date``, answering ``{"data": [...]}``:

* ``/daily_sleep``      ``score``
* ``/daily_readiness``  ``score``, ``temperature_deviation``
* ``/daily_activity``   ``score``, ``steps``, ``active_calories``, ``total_calories``
* ``/sleep``            sleep periods: ``total_sleep_duration``, ``lowest_heart_rate`` ...
"""

import logging
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from src.plugins.base import PluginBase, PluginResult
from src.text_to_board import take_tiles

logger = logging.getLogger(__name__)

API_BASE = "https://api.ouraring.com/v2/usercollection"
USER_AGENT = "FiestaBoard Oura Plugin (https://github.com/Fiestaboard/fiestaboard-plugin--oura)"
REQUEST_TIMEOUT_SECONDS = 10
ENDPOINTS = ("daily_sleep", "daily_readiness", "daily_activity", "sleep")
# Days before today to ask for, so last night's data is found even before
# today's documents exist.
LOOKBACK_DAYS = 2

# One snapshot is shared between boards (Flagship and Note fetch separately).
SNAPSHOT_TTL_SECONDS = 60
TRANSIENT_COOLDOWN_SECONDS = 60
AUTH_COOLDOWN_SECONDS = 300
DEFAULT_RETRY_AFTER_SECONDS = 60
MAX_RETRY_AFTER_SECONDS = 3600
STALE_DATA_LIMIT_SECONDS = 3600

DEFAULT_COLS = 22
DEFAULT_ROWS = 6

# Oura's own score bands: 85+ optimal, 70-84 good, below 70 pay attention.
TILE_OPTIMAL = "{66}"  # green
TILE_GOOD = "{65}"  # yellow
TILE_LOW = "{63}"  # red

# Sleep period types that count as real sleep ("rest" was rejected by the
# user, "deleted" was deleted). "long_sleep" is the main night.
COUNTED_SLEEP_TYPES = ("long_sleep", "sleep", "late_nap")

NOT_CONNECTED_ERROR = "Not signed in to Oura. Open this plugin's settings and sign in."
PLATFORM_TOO_OLD_ERROR = "This FiestaBoard version cannot sign in to Oura. Update FiestaBoard to use this plugin."
UNAUTHORIZED_ERROR = "Oura rejected the sign-in (401). Open this plugin's settings and press Reconnect."
FORBIDDEN_ERROR = (
    "Oura refused access (403). Check your Oura Membership is active, then press Reconnect "
    "and allow access to daily data."
)


class OuraError(Exception):
    """An Oura request failed; the message is shown to the user."""

    def __init__(self, message: str, *, transient: bool, cooldown: float):
        super().__init__(message)
        self.transient = transient
        self.cooldown = cooldown


# ----------------------------------------------------------------------
# Pure helpers
# ----------------------------------------------------------------------


def _monotonic() -> float:
    return time.monotonic()


def _today() -> date:
    return date.today()


def _int(value: Any) -> Any:
    """Round a number to an int; anything else (null) becomes ``""``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    return int(round(value))


def format_hm(seconds: Any) -> str:
    """``27120`` -> ``7H 32M``; under an hour -> ``50M``; null -> ``""``."""
    total = _int(seconds)
    if total == "":
        return ""
    hours, minutes = divmod(max(total, 0) // 60, 60)
    return f"{hours}H {minutes}M" if hours else f"{minutes}M"


def score_tile(score: Any) -> str:
    if _int(score) == "":
        return ""
    if score >= 85:
        return TILE_OPTIMAL
    return TILE_GOOD if score >= 70 else TILE_LOW


def score_label(score: Any) -> str:
    if _int(score) == "":
        return ""
    if score >= 85:
        return "OPTIMAL"
    return "GOOD" if score >= 70 else "PAY ATTENTION"


def format_clock(value: Any) -> str:
    """``2026-10-01T22:48:00-07:00`` -> ``22:48`` (the ring wearer's local time)."""
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%H:%M")
    except ValueError:
        return ""


def signed(value: Any) -> str:
    """``0.21`` -> ``+0.2``; ``-0.34`` -> ``-0.3``; about zero -> ``0.0``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    rounded = round(value, 1)
    if rounded == 0:
        return "0.0"
    return f"{rounded:+.1f}"


def pick_latest(docs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The newest daily document that has a score, else the newest one."""
    if not docs:
        return None
    ordered = sorted(docs, key=lambda d: d.get("day") or "")
    scored = [d for d in ordered if d.get("score") is not None]
    return (scored or ordered)[-1]


def pick_main_sleep(periods: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Last night's main sleep: newest day, a long sleep first, then the longest."""
    counted = [p for p in periods if p.get("type") in COUNTED_SLEEP_TYPES]
    if not counted:
        return None
    return max(
        counted,
        key=lambda p: (p.get("day") or "", p.get("type") == "long_sleep", p.get("total_sleep_duration") or 0),
    )


def fit(text: str, width: int) -> str:
    return take_tiles(text, width)[0]


# ----------------------------------------------------------------------
# Plugin
# ----------------------------------------------------------------------


class OuraPlugin(PluginBase):
    """Daily Oura Ring scores and last night's sleep."""

    def __init__(self, manifest: Dict[str, Any]):
        super().__init__(manifest)
        self._lock = threading.RLock()
        self._reset_state()

    def _reset_state(self) -> None:
        self._snapshot: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._snapshot_at = 0.0
        self._last_good: Optional[Dict[str, List[Dict[str, Any]]]] = None
        self._last_good_at = 0.0
        self._cooldown_until = 0.0
        self._cooldown_error: Optional[OuraError] = None
        self._cooldown_token: Optional[str] = None

    @property
    def plugin_id(self) -> str:
        return "oura"

    def validate_config(self, config: Dict[str, Any]) -> List[str]:
        # Client ID and secret may be empty: the plugin reports "not signed
        # in" until someone signs in, and the platform will not start a
        # sign-in without them.
        return self._validate_refresh_seconds(config)

    def on_config_change(self, old_config: Dict[str, Any], new_config: Dict[str, Any]) -> None:
        super().on_config_change(old_config, new_config)
        with self._lock:
            self._reset_state()

    # ------------------------------------------------------------------
    # Oura requests
    # ------------------------------------------------------------------

    def _get(self, token: str, endpoint: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        try:
            response = requests.get(
                f"{API_BASE}/{endpoint}",
                params=params,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.exceptions.Timeout:
            raise OuraError("Timed out contacting Oura", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS)
        except requests.exceptions.RequestException as exc:
            raise OuraError(
                f"Could not reach Oura: {type(exc).__name__}", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS
            )

        status = response.status_code
        if status == 200:
            try:
                return list((response.json() or {}).get("data") or [])
            except (ValueError, AttributeError):
                raise OuraError("Oura sent an unreadable answer", transient=True, cooldown=TRANSIENT_COOLDOWN_SECONDS)
        if status == 401:
            raise OuraError(UNAUTHORIZED_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if status == 403:
            raise OuraError(FORBIDDEN_ERROR, transient=False, cooldown=AUTH_COOLDOWN_SECONDS)
        if status == 429:
            wait = self._retry_after(response)
            raise OuraError(f"Oura rate limit reached; trying again in {int(wait)}s", transient=True, cooldown=wait)
        if status >= 500:
            raise OuraError(
                f"Oura is having trouble (HTTP {status}); will retry",
                transient=True,
                cooldown=TRANSIENT_COOLDOWN_SECONDS,
            )
        raise OuraError(
            f"Unexpected response from Oura (HTTP {status})", transient=False, cooldown=TRANSIENT_COOLDOWN_SECONDS
        )

    @staticmethod
    def _retry_after(response: requests.Response) -> float:
        try:
            wait = float(response.headers.get("Retry-After", DEFAULT_RETRY_AFTER_SECONDS))
        except (TypeError, ValueError):
            wait = DEFAULT_RETRY_AFTER_SECONDS
        return min(max(wait, 1.0), MAX_RETRY_AFTER_SECONDS)

    def _start_cooldown(self, exc: OuraError, token: str) -> None:
        self._cooldown_until = max(self._cooldown_until, _monotonic() + exc.cooldown)
        self._cooldown_error = exc
        # A rejected token only blocks that token: reconnecting gives a new one.
        self._cooldown_token = None if exc.transient else token

    def _in_cooldown(self, token: str) -> bool:
        if self._cooldown_error is None or _monotonic() >= self._cooldown_until:
            return False
        return self._cooldown_token is None or self._cooldown_token == token

    def _load_snapshot(self, token: str) -> Dict[str, List[Dict[str, Any]]]:
        if self._snapshot is not None and _monotonic() - self._snapshot_at < SNAPSHOT_TTL_SECONDS:
            return self._snapshot
        if self._in_cooldown(token):
            raise self._cooldown_error
        today = _today()
        params = {
            "start_date": (today - timedelta(days=LOOKBACK_DAYS)).isoformat(),
            "end_date": (today + timedelta(days=1)).isoformat(),
        }
        try:
            snapshot = {endpoint: self._get(token, endpoint, params) for endpoint in ENDPOINTS}
        except OuraError as exc:
            self._start_cooldown(exc, token)
            raise
        self._snapshot = snapshot
        self._snapshot_at = _monotonic()
        return snapshot

    # ------------------------------------------------------------------
    # fetch_data
    # ------------------------------------------------------------------

    def _board_size(self) -> Tuple[int, int]:
        board = self.board
        if board is None:
            return DEFAULT_COLS, DEFAULT_ROWS
        return board.cols, board.rows

    def fetch_data(self) -> PluginResult:
        try:
            get_token = getattr(self, "get_oauth_token", None)
            if get_token is None:
                return PluginResult(available=False, error=PLATFORM_TOO_OLD_ERROR)
            token = get_token()
            if not token:
                return PluginResult(available=False, error=NOT_CONNECTED_ERROR)

            with self._lock:
                try:
                    snapshot = self._load_snapshot(token)
                except OuraError as exc:
                    stale = self._stale_snapshot()
                    if not (exc.transient and stale is not None):
                        logger.warning("Oura: %s", exc)
                        return PluginResult(available=False, error=str(exc))
                    logger.info("Oura: %s; showing the last data", exc)
                    snapshot = stale
                else:
                    self._last_good = snapshot
                    self._last_good_at = _monotonic()

            data = self._build_data(snapshot)
            cols, rows = self._board_size()
            return PluginResult(available=True, data=data, formatted_lines=self._format_display(data, cols, rows))
        except Exception as exc:  # fetch_data must never raise
            logger.exception("Error fetching Oura data")
            return PluginResult(available=False, error=str(exc))

    def _stale_snapshot(self) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        if self._last_good is None or _monotonic() - self._last_good_at > STALE_DATA_LIMIT_SECONDS:
            return None
        return self._last_good

    # ------------------------------------------------------------------
    # Variables and display
    # ------------------------------------------------------------------

    @staticmethod
    def _build_data(snapshot: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        sleep_day = pick_latest(snapshot.get("daily_sleep") or []) or {}
        readiness = pick_latest(snapshot.get("daily_readiness") or []) or {}
        activity = pick_latest(snapshot.get("daily_activity") or []) or {}
        period = pick_main_sleep(snapshot.get("sleep") or []) or {}

        scores = {
            "sleep": _int(sleep_day.get("score")),
            "readiness": _int(readiness.get("score")),
            "activity": _int(activity.get("score")),
        }
        total_seconds = _int(period.get("total_sleep_duration"))
        steps = _int(activity.get("steps"))

        def shown(score: Any) -> str:
            return "--" if score == "" else str(score)

        data: Dict[str, Any] = {}
        for name, score in scores.items():
            data[f"{name}_score"] = score
            data[f"{name}_tile"] = score_tile(score)
            data[f"{name}_label"] = score_label(score)
        data.update(
            {
                "scores_line": (
                    f"SLP {shown(scores['sleep'])} RDY {shown(scores['readiness'])} ACT {shown(scores['activity'])}"
                ),
                # Sleep
                "total_sleep": format_hm(total_seconds),
                "total_sleep_minutes": "" if total_seconds == "" else total_seconds // 60,
                "time_in_bed": format_hm(period.get("time_in_bed")),
                "deep_sleep": format_hm(period.get("deep_sleep_duration")),
                "rem_sleep": format_hm(period.get("rem_sleep_duration")),
                "sleep_efficiency": _int(period.get("efficiency")),
                "resting_hr": _int(period.get("lowest_heart_rate")),
                "average_hr": _int(period.get("average_heart_rate")),
                "average_hrv": _int(period.get("average_hrv")),
                "bedtime": format_clock(period.get("bedtime_start") or ""),
                "wake_time": format_clock(period.get("bedtime_end") or ""),
                # Activity
                "steps": steps,
                "steps_text": "" if steps == "" else f"{steps:,}",
                "active_calories": _int(activity.get("active_calories")),
                "total_calories": _int(activity.get("total_calories")),
                # Body
                "temperature_deviation": signed(readiness.get("temperature_deviation")),
            }
        )
        return data

    @staticmethod
    def _format_display(data: Dict[str, Any], cols: int, rows: int) -> List[str]:
        """Fallback layout for a board *cols* x *rows* tiles when there is no template."""

        def score_line(name: str) -> str:
            score = data[f"{name}_score"]
            text = f"{name.upper()} {'--' if score == '' else score}"
            tile = data[f"{name}_tile"]
            return f"{tile} {text}" if tile else text

        scores = [score_line("sleep"), score_line("readiness"), score_line("activity")]
        if rows < 6:
            lines = scores
        else:
            details = " ".join(
                part
                for part in (
                    f"RHR {data['resting_hr']}" if data["resting_hr"] != "" else "",
                    f"STEPS {data['steps_text']}" if data["steps_text"] else "",
                )
                if part
            )
            lines = ["OURA", *scores, f"SLEPT {data['total_sleep']}" if data["total_sleep"] else "", details]
        return [fit(line, cols) for line in lines[:rows]]

    def get_formatted_display(self) -> Optional[List[str]]:
        result = self.get_data(self.board)
        if not result.available or not result.data:
            return None
        cols, rows = self._board_size()
        return self._format_display(result.data, cols, rows)


# Export hook: the loader looks for a module-level `Plugin`.
Plugin = OuraPlugin
