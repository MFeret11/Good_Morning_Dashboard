"""Sets up the background scheduler that triggers the afternoon commute
notification automatically, without needing an external cron job."""
from apscheduler.executors.pool import ThreadPoolExecutor
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import NOTIFY_HOUR, NOTIFY_MINUTE, AFTERNOON_END
from app.afternoon_check import run_afternoon_check, run_follow_up_check

FOLLOW_UP_EVERY_MINUTES = 5

# One worker thread, so the afternoon push and a follow-up never run at the
# same time and race on the follow-up state.
scheduler = BackgroundScheduler(executors={"default": ThreadPoolExecutor(1)})


def start_scheduler():
    scheduler.add_job(
        run_afternoon_check,
        trigger=CronTrigger(day_of_week="mon-fri", hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE),
        id="afternoon_commute_check",
        replace_existing=True,
        # APScheduler's default grace is 1 second: a run that starts any later
        # (busy Pi, slow wake) is silently skipped, and no push reads as
        # "all good". Allow up to 5 minutes late, and never run twice.
        misfire_grace_time=300,
        coalesce=True,
    )
    # Follow-ups do nothing until the afternoon push has gone out, and stop
    # once the train leaves (see afternoon_check.run_follow_up_check).
    scheduler.add_job(
        run_follow_up_check,
        trigger=CronTrigger(
            day_of_week="mon-fri",
            hour=f"{NOTIFY_HOUR}-{max(NOTIFY_HOUR, AFTERNOON_END - 1)}",
            minute=f"*/{FOLLOW_UP_EVERY_MINUTES}",
        ),
        id="afternoon_follow_up_check",
        replace_existing=True,
        misfire_grace_time=60,
        coalesce=True,
    )
    scheduler.start()
    print(f"[scheduler] Afternoon check scheduled for {NOTIFY_HOUR:02d}:{NOTIFY_MINUTE:02d} on weekdays, "
          f"follow-ups every {FOLLOW_UP_EVERY_MINUTES} min until the train leaves")
