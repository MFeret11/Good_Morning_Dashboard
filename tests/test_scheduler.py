import app.scheduler


def test_afternoon_job_tolerates_a_late_start(monkeypatch):
    # APScheduler's default 1 s grace silently skips a late run, and a missing
    # push reads as "all good".
    monkeypatch.setattr(app.scheduler.scheduler, "start", lambda: None)
    app.scheduler.start_scheduler()
    job = app.scheduler.scheduler.get_job("afternoon_commute_check")
    assert job.misfire_grace_time >= 300
    assert job.coalesce is True


def test_follow_up_job_runs_every_five_minutes_on_weekdays(monkeypatch):
    monkeypatch.setattr(app.scheduler.scheduler, "start", lambda: None)
    app.scheduler.start_scheduler()
    job = app.scheduler.scheduler.get_job("afternoon_follow_up_check")
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["minute"] == "*/5"
    assert fields["day_of_week"] == "mon-fri"
