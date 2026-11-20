import pytest

import app.config
import app.scheduler


def test_afternoon_job_tolerates_a_late_start(monkeypatch):
    # APScheduler's default 1 s grace silently skips a late run, and a missing
    # push reads as "all good".
    monkeypatch.setattr(app.scheduler.scheduler, "start", lambda: None)
    app.scheduler.start_scheduler()
    job = app.scheduler.scheduler.get_job("afternoon_commute_check")
    assert job.misfire_grace_time >= 300
    assert job.coalesce is True


def test_follow_up_job_runs_every_five_minutes_on_commute_days(monkeypatch):
    monkeypatch.setattr(app.scheduler.scheduler, "start", lambda: None)
    app.scheduler.start_scheduler()
    job = app.scheduler.scheduler.get_job("afternoon_follow_up_check")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["minute"] == "*/5"
    assert fields["day_of_week"] == "mon,tue,wed,thu,fri"


def test_both_jobs_follow_commute_days(monkeypatch):
    monkeypatch.setattr(app.scheduler.scheduler, "start", lambda: None)
    monkeypatch.setattr(app.scheduler, "COMMUTE_DAYS_CRON", "mon,tue")
    app.scheduler.scheduler.remove_all_jobs()  # drop jobs queued by earlier tests
    app.scheduler.start_scheduler()
    for job_id in ("afternoon_commute_check", "afternoon_follow_up_check"):
        fields = {f.name: str(f) for f in app.scheduler.scheduler.get_job(job_id).trigger.fields}
        assert fields["day_of_week"] == "mon,tue"


def test_commute_days_setting(monkeypatch):
    monkeypatch.setenv("COMMUTE_DAYS", "Monday, tue,THU")
    assert app.config._env_days("COMMUTE_DAYS", "mon") == [0, 1, 3]
    monkeypatch.setenv("COMMUTE_DAYS", "mon,funday")
    with pytest.raises(ValueError):
        app.config._env_days("COMMUTE_DAYS", "mon")
