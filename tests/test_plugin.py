"""Tests for the Oura plugin: manifest, variables, layout, sign-in and failures."""

import re

import requests
from src.devices import BoardContext

from plugins.oura import (
    FORBIDDEN_ERROR,
    NOT_CONNECTED_ERROR,
    PLATFORM_TOO_OLD_ERROR,
    UNAUTHORIZED_ERROR,
    OuraPlugin,
    format_clock,
    format_hm,
    pick_latest,
    pick_main_sleep,
    score_label,
    score_tile,
    signed,
)

from .conftest import TEST_TOKEN, FakeResponse
from .fixtures import TODAY, YESTERDAY, daily_activity, daily_readiness, daily_sleep, sleep_period

FLAGSHIP = BoardContext.from_device_type("flagship")
NOTE = BoardContext.from_device_type("note")


def fetch(plugin, board=None):
    with plugin._bound_board(board):
        return plugin.fetch_data()


# ----------------------------------------------------------------------
# Manifest
# ----------------------------------------------------------------------


class TestManifest:
    def test_identity(self, manifest_data, plugin):
        assert manifest_data["id"] == "oura" == plugin.plugin_id
        assert manifest_data["category"] == "data"
        assert manifest_data["fiestaboard_version"] == ">=9.8.0"

    def test_oauth_block(self, manifest_data):
        oauth = manifest_data["oauth"]
        assert oauth["flows"] == ["relay"]
        assert oauth["authorization_url"] == "https://cloud.ouraring.com/oauth/authorize"
        assert oauth["token_url"] == "https://api.ouraring.com/oauth/token"
        assert oauth["scopes"] == ["daily"]
        assert oauth["client_id_setting"] == "client_id"
        assert oauth["client_secret_setting"] == "client_secret"
        assert oauth["app_setup_url"] == "https://cloud.ouraring.com/oauth/applications"
        assert "client_id" not in oauth and "client_secret" not in oauth

    def test_oauth_block_parses_in_core(self, manifest_data):
        from src.oauth.provider import parse_provider_block

        provider = parse_provider_block(manifest_data["oauth"], manifest_data["name"], manifest_data["settings_schema"])
        assert provider.resolve_client_secret({"client_secret": "test_secret"}) == "test_secret"

    def test_secret_is_a_password_field(self, manifest_data):
        props = manifest_data["settings_schema"]["properties"]
        assert props["client_secret"]["ui:widget"] == "password"
        assert "required" not in manifest_data["settings_schema"]

    def test_every_data_key_is_declared(self, manifest_data, plugin, oura_api):
        declared = set(manifest_data["variables"]["simple"])
        result = fetch(plugin)
        assert set(result.data) == declared

    def test_simple_max_lengths_hold_for_sample_data(self, manifest_data, plugin, oura_api):
        data = fetch(plugin).data
        for name, spec in manifest_data["variables"]["simple"].items():
            if "max_length" in spec:
                tiles = re.sub(r"\{\d+\}", "#", str(data[name]))  # a colour code is one tile
                assert len(tiles) <= spec["max_length"], name


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


class TestHelpers:
    def test_format_hm(self):
        assert format_hm(27120) == "7H 32M"
        assert format_hm(3000) == "50M"
        assert format_hm(None) == ""
        assert format_hm("x") == ""

    def test_score_tile_and_label(self):
        assert score_tile(85) == "{66}" and score_label(85) == "OPTIMAL"
        assert score_tile(70) == "{65}" and score_label(84) == "GOOD"
        assert score_tile(69) == "{63}" and score_label(10) == "PAY ATTENTION"
        assert score_tile(None) == "" and score_label(None) == ""

    def test_format_clock(self):
        assert format_clock("2026-10-01T22:48:00-07:00") == "22:48"
        assert format_clock("") == ""
        assert format_clock("not a time") == ""

    def test_signed(self):
        assert signed(0.21) == "+0.2"
        assert signed(-0.34) == "-0.3"
        assert signed(0.01) == "0.0"
        assert signed(None) == ""

    def test_pick_latest_prefers_newest_scored_day(self):
        docs = [daily_sleep(YESTERDAY, 70), daily_sleep(TODAY, None)]
        assert pick_latest(docs)["day"] == YESTERDAY
        assert pick_latest([daily_sleep(YESTERDAY, 70), daily_sleep(TODAY, 80)])["score"] == 80
        assert pick_latest([daily_sleep(TODAY, None)])["day"] == TODAY
        assert pick_latest([]) is None

    def test_pick_main_sleep(self):
        nap = sleep_period(TODAY, kind="sleep", total=1800)
        main = sleep_period(TODAY, kind="long_sleep", total=25000)
        old = sleep_period(YESTERDAY, kind="long_sleep", total=30000)
        rejected = sleep_period(TODAY, kind="rest", total=40000)
        deleted = sleep_period(TODAY, kind="deleted", total=40000)
        assert pick_main_sleep([nap, old, main, rejected, deleted]) is main
        assert pick_main_sleep([nap]) is nap
        assert pick_main_sleep([rejected]) is None


# ----------------------------------------------------------------------
# Variables
# ----------------------------------------------------------------------


class TestVariables:
    def test_scores(self, plugin, oura_api):
        data = fetch(plugin).data
        assert (data["sleep_score"], data["readiness_score"], data["activity_score"]) == (82, 76, 91)
        assert data["sleep_tile"] == "{65}" and data["activity_tile"] == "{66}"
        assert data["sleep_label"] == "GOOD" and data["activity_label"] == "OPTIMAL"
        assert data["scores_line"] == "SLP 82 RDY 76 ACT 91"

    def test_sleep_details(self, plugin, oura_api):
        data = fetch(plugin).data
        assert data["total_sleep"] == "7H 32M"
        assert data["total_sleep_minutes"] == 452
        assert data["time_in_bed"] == "8H 5M"
        assert data["deep_sleep"] == "1H 30M"
        assert data["rem_sleep"] == "1H 50M"
        assert data["resting_hr"] == 52
        assert data["average_hr"] == 58
        assert data["average_hrv"] == 48
        assert data["sleep_efficiency"] == 93
        assert data["bedtime"] == "22:48" and data["wake_time"] == "06:53"

    def test_activity_and_body(self, plugin, oura_api):
        data = fetch(plugin).data
        assert data["steps"] == 8423
        assert data["steps_text"] == "8,423"
        assert data["active_calories"] == 412
        assert data["total_calories"] == 2310
        assert data["temperature_deviation"] == "+0.2"

    def test_requests_a_short_date_window(self, plugin, oura_api):
        fetch(plugin)
        assert sorted(oura_api.paths()) == ["/daily_activity", "/daily_readiness", "/daily_sleep", "/sleep"]
        for _, kwargs in oura_api.calls:
            assert kwargs["params"] == {"start_date": "2026-09-30", "end_date": "2026-10-03"}
            assert kwargs["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
            assert kwargs["timeout"] == 10

    def test_no_data_yet(self, plugin, oura_api):
        for key in oura_api.docs:
            oura_api.docs[key] = []
        result = fetch(plugin)
        assert result.available
        data = result.data
        assert data["sleep_score"] == "" and data["sleep_tile"] == ""
        assert data["scores_line"] == "SLP -- RDY -- ACT --"
        assert data["total_sleep"] == "" and data["resting_hr"] == ""
        assert data["steps_text"] == "" and data["temperature_deviation"] == ""

    def test_null_fields_become_empty(self, plugin, oura_api):
        period = sleep_period()
        for field in ("lowest_heart_rate", "average_heart_rate", "average_hrv", "efficiency", "total_sleep_duration"):
            period[field] = None
        oura_api.docs["/sleep"] = [period]
        oura_api.docs["/daily_readiness"] = [daily_readiness(temperature_deviation=None)]
        data = fetch(plugin).data
        assert data["resting_hr"] == data["average_hr"] == data["average_hrv"] == ""
        assert data["total_sleep"] == "" and data["total_sleep_minutes"] == ""
        assert data["temperature_deviation"] == ""

    def test_uses_latest_day(self, plugin, oura_api):
        oura_api.docs["/daily_activity"] = [daily_activity(YESTERDAY, steps=12000), daily_activity(TODAY, steps=300)]
        assert fetch(plugin).data["steps"] == 300


# ----------------------------------------------------------------------
# Layout
# ----------------------------------------------------------------------


class TestLayout:
    def test_flagship(self, plugin, oura_api):
        lines = fetch(plugin, FLAGSHIP).formatted_lines
        assert lines == [
            "OURA",
            "{65} SLEEP 82",
            "{65} READINESS 76",
            "{66} ACTIVITY 91",
            "SLEPT 7H 32M",
            "RHR 52 STEPS 8,423",
        ]

    def test_note(self, plugin, oura_api):
        lines = fetch(plugin, NOTE).formatted_lines
        assert lines == ["{65} SLEEP 82", "{65} READINESS 76", "{66} ACTIVITY 91"]

    def test_missing_scores_show_dashes(self, plugin, oura_api):
        for key in oura_api.docs:
            oura_api.docs[key] = []
        lines = fetch(plugin, NOTE).formatted_lines
        assert lines == ["SLEEP --", "READINESS --", "ACTIVITY --"]

    def test_get_formatted_display(self, plugin, oura_api):
        assert plugin.get_formatted_display()[0] == "OURA"

    def test_get_formatted_display_unavailable(self, plugin, token):
        token.value = None
        assert plugin.get_formatted_display() is None


# ----------------------------------------------------------------------
# Signing in and configuration
# ----------------------------------------------------------------------


class TestAuth:
    def test_not_connected(self, plugin, token, oura_api):
        token.value = None
        result = fetch(plugin)
        assert not result.available and result.error == NOT_CONNECTED_ERROR
        assert oura_api.calls == []

    def test_platform_too_old(self, manifest_data, oura_api):
        p = OuraPlugin(manifest_data)
        p.config = {"enabled": True}
        if hasattr(type(p), "get_oauth_token"):
            # Core provides it; hide it to simulate an older FiestaBoard.
            p.get_oauth_token = None
        result = p.fetch_data()
        assert not result.available and result.error == PLATFORM_TOO_OLD_ERROR

    def test_token_read_on_every_fetch(self, plugin, token, oura_api, clock):
        fetch(plugin)
        token.value = "test_rotated_token"
        clock.advance(120)
        fetch(plugin)
        assert oura_api.calls[-1][1]["headers"]["Authorization"] == "Bearer test_rotated_token"

    def test_validate_config(self, plugin):
        assert plugin.validate_config({}) == []
        assert plugin.validate_config({"client_id": "test_id", "client_secret": "test_s", "refresh_seconds": 600}) == []
        assert plugin.validate_config({"refresh_seconds": 5})

    def test_config_change_drops_snapshot(self, plugin, oura_api):
        fetch(plugin)
        plugin.on_config_change(plugin.config, {**plugin.config, "refresh_seconds": 900})
        fetch(plugin)
        assert len(oura_api.calls) == 8


# ----------------------------------------------------------------------
# Failures, cooldowns and sharing one fetch
# ----------------------------------------------------------------------


class TestFailures:
    def test_snapshot_shared_between_boards(self, plugin, oura_api):
        fetch(plugin, FLAGSHIP)
        fetch(plugin, NOTE)
        assert len(oura_api.calls) == 4

    def test_snapshot_expires(self, plugin, oura_api, clock):
        fetch(plugin)
        clock.advance(61)
        fetch(plugin)
        assert len(oura_api.calls) == 8

    def test_401(self, plugin, oura_api):
        oura_api.fail("/daily_sleep", status=401)
        result = fetch(plugin)
        assert result.error == UNAUTHORIZED_ERROR

    def test_401_cooldown_is_per_token(self, plugin, oura_api, token, clock):
        oura_api.fail("/daily_sleep", status=401)
        fetch(plugin)
        n = len(oura_api.calls)
        assert fetch(plugin).error == UNAUTHORIZED_ERROR
        assert len(oura_api.calls) == n  # same token: not asked again
        token.value = "test_new_token"
        assert fetch(plugin).available

    def test_403(self, plugin, oura_api):
        oura_api.fail("/sleep", status=403)
        assert fetch(plugin).error == FORBIDDEN_ERROR

    def test_429_honors_retry_after(self, plugin, oura_api, clock):
        oura_api.fail("/daily_sleep", status=429, headers={"Retry-After": "120"})
        result = fetch(plugin)
        assert "rate limit" in result.error and "120s" in result.error
        n = len(oura_api.calls)
        clock.advance(60)
        fetch(plugin)
        assert len(oura_api.calls) == n
        clock.advance(61)
        assert fetch(plugin).available

    def test_429_bad_retry_after(self, plugin, oura_api):
        oura_api.fail("/daily_sleep", status=429, headers={"Retry-After": "soon"})
        assert "60s" in fetch(plugin).error

    def test_transient_error_shows_last_good_data(self, plugin, oura_api, clock):
        assert fetch(plugin).available
        clock.advance(61)
        oura_api.fail("/daily_sleep", status=503)
        result = fetch(plugin)
        assert result.available and result.data["sleep_score"] == 82

    def test_stale_data_expires(self, plugin, oura_api, clock):
        fetch(plugin)
        clock.advance(4000)
        oura_api.fail("/daily_sleep", requests.exceptions.Timeout("slow"))
        result = fetch(plugin)
        assert not result.available and "Timed out" in result.error

    def test_connection_error(self, plugin, oura_api):
        oura_api.fail("/daily_sleep", requests.exceptions.ConnectionError("down"))
        assert "Could not reach Oura" in fetch(plugin).error

    def test_bad_json(self, plugin, oura_api):
        oura_api.fail("/daily_sleep", FakeResponse(ValueError("bad json"), 200))
        assert "unreadable" in fetch(plugin).error

    def test_other_status(self, plugin, oura_api):
        oura_api.fail("/daily_sleep", status=422)
        assert "HTTP 422" in fetch(plugin).error

    def test_fetch_never_raises(self, plugin, oura_api, monkeypatch):
        monkeypatch.setattr(plugin, "_build_data", lambda *a: 1 / 0)
        result = fetch(plugin)
        assert not result.available and "division" in result.error
