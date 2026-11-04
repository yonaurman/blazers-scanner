"""Shared HTTP session with retries."""

import threading

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

_local = threading.local()


def get_session() -> requests.Session:
    """Return a per-thread session (requests.Session is not guaranteed thread-safe)."""
    session = getattr(_local, "session", None)
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = config.USER_AGENT
        retry = Retry(
            total=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "POST"}),
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _local.session = session
    return session
