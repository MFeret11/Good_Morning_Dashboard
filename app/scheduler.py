"""Sets up the background scheduler that triggers the afternoon commute
notification automatically, without needing an external cron job."""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import NOTIFY_HOUR, NOTIFY_MINUTE
from app.afternoon_check import run_afternoon_check

scheduler = BackgroundScheduler()


def start_scheduler():
    scheduler.add_job(
        run_afternoon_check,
        trigger=CronTrigger(day_of_week="mon-fri", hour=NOTIFY_HOUR, minute=NOTIFY_MINUTE),
        id="afternoon_commute_check",
        replace_existing=True,
    )
    scheduler.start()
    print(f"[scheduler] Afternoon check scheduled for {NOTIFY_HOUR:02d}:{NOTIFY_MINUTE:02d} on weekdays")
