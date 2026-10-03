"""Shared fixtures for the Oura plugin tests.

``oura_api`` patches ``requests.get`` with a fake Oura API v2 that answers
the four ``usercollection`` endpoints this plugin reads. Tests change its
documents or queue raw responses to simulate errors. No test touches the
network.

``get_oauth_token`` is patched on the plugin instance: the platform's OAuth
service (which also stores Oura's single-use rotated refresh tokens) is not
under test here, only how the plugin uses the token it hands out.
"""

import json
from collections import deque
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from plugins.oura import OuraPlugin

from .fixtures import daily_activity, daily_readiness, daily_sleep, page, sleep_period

MANIFEST_PATH = Path(__file__).parent.parent / "manifest.json"
API = "https://api.ouraring.com/v2/usercollection"
TEST_CLIENT_ID = "test_client_id"
TEST_CLIENT_SECRET = "test_client_secret"
TEST_TOKEN = "test_access_token"


class FakeResponse:
    def __init__(self, body=None, status_code=200, headers=None):
        self._body = body
        self.status_code = status_code
        self.headers = headers or {}
        self.content = b"" if body is None else b"x" if isinstance(body, Exception) else json.dumps(body).encode()

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeOura:
    """Answers requests.get the way the Oura API v2 would."""

    def __init__(self):
        self.docs = {
            "/daily_sleep": [daily_sleep()],
            "/daily_readiness": [daily_readiness()],
            "/daily_activity": [daily_activity()],
            "/sleep": [sleep_period()],
        }
        self.overrides = {}
        self.calls = []

    def fail(self, path, response=None, *, status=None, headers=None, times=1):
        if response is None:
            response = FakeResponse({"detail": "test"}, status, headers)
        self.overrides.setdefault(path, deque()).extend([response] * times)

    def paths(self):
        return [url[len(API) :] for url, _ in self.calls]

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, {"params": params, "headers": headers, "timeout": timeout}))
        path = url[len(API) :]
        queued = self.overrides.get(path)
        if queued:
            response = queued.popleft()
            if isinstance(response, Exception):
                raise response
            return response
        if path in self.docs:
            return FakeResponse(page(*self.docs[path]))
        raise AssertionError(f"Unexpected Oura request: {url}")  # pragma: no cover


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def manifest_data():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def clock():
    c = Clock()
    with patch("plugins.oura._monotonic", side_effect=c), patch("plugins.oura._today", return_value=date(2026, 10, 2)):
        yield c


@pytest.fixture
def oura_api():
    fake = FakeOura()
    with patch("plugins.oura.requests.get", side_effect=fake):
        yield fake


@pytest.fixture
def token():
    class Token:
        value = TEST_TOKEN

    return Token


@pytest.fixture
def make_plugin(manifest_data, token):
    def factory(**config):
        p = OuraPlugin(manifest_data)
        p.config = {"enabled": True, "client_id": TEST_CLIENT_ID, "client_secret": TEST_CLIENT_SECRET, **config}
        p.get_oauth_token = lambda: token.value
        return p

    return factory


@pytest.fixture
def plugin(make_plugin):
    return make_plugin()
