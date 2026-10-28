import os
from datetime import datetime

# Fixed settings for the test run, set before app.config is imported so a
# developer's real .env can't change test results (load_dotenv never
# overrides variables that are already set).
os.environ.update({
    "HOME_STATION": "Glenside",
    "WORK_STATION": "Ambler",
    "TRANSFER_STATION": "Jefferson Station",
    "TRANSFER_FALLBACK_STATIONS": "Suburban Station,30th Street Station",
    "WALK_TIMES": "Glenside:5,Ambler:15,Jefferson Station:3",
    "AFTERNOON_TARGET_DEPARTURE": "5:00PM",
    "NOTIFY_TIME": "4:00PM",
    "RELEVANT_LINES": "Lansdale/Doylestown,Paoli/Thorndale",
    "WEATHER_LAT": "39.9526",
    "WEATHER_LON": "-75.1652",
    "NTFY_TOPIC": "",
})

import pytest  # noqa: E402

import app.afternoon_check  # noqa: E402
import app.alerts  # noqa: E402
import app.commute  # noqa: E402
import app.weather  # noqa: E402


@pytest.fixture(autouse=True)
def reset_module_caches():
    """The app keeps in-memory caches at module level. Clear them before each
    test so one test's mocked response can't leak into the next."""
    app.alerts._cache.update({"data": None, "fetched_at": 0.0})
    app.commute._TRIP_CACHE.clear()
    app.commute._TRIP_RECENT.clear()
    app.commute._TRAIN_POSITIONS.clear()
    app.commute._TRAINVIEW_CACHE = ([], 0.0)
    app.weather._WEATHER_CACHE.clear()
    app.afternoon_check._follow_up.clear()
    yield


@pytest.fixture(autouse=True)
def fetch_every_time(monkeypatch):
    """Most tests swap SEPTA's answer between two lookups of the same stations,
    so reusing a recent answer is off by default; test_api_usage.py turns it on."""
    monkeypatch.setattr(app.commute, "TRIP_FRESH_SECONDS", 0)


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Pin 'now' to 5:00AM on a weekday, before every train in the fixtures,
    so train selection doesn't depend on when the tests run."""
    now = datetime(2026, 1, 5, 5, 0)
    monkeypatch.setattr(app.commute, "_now", lambda: now)
    return now
