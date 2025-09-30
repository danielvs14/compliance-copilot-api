from __future__ import annotations

import logging
import sys
from contextlib import contextmanager

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from ..db.session import SessionLocal
from ..services.reminders import dispatch_reminders, queue_reminders


logger = logging.getLogger(__name__)


@contextmanager
def session_scope():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def run_queue_job() -> None:
    with session_scope() as session:
        stats = queue_reminders(session)
    if stats:
        logger.info("Queued reminders: %s", dict(stats))


def run_dispatch_job() -> None:
    with session_scope() as session:
        stats = dispatch_reminders(session)
    if stats:
        logger.info("Dispatched reminders: %s", dict(stats))


def run_once() -> None:
    run_queue_job()
    run_dispatch_job()


def configure_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler(timezone="UTC")
    scheduler.add_job(run_queue_job, CronTrigger(hour=2, minute=0))
    scheduler.add_job(run_dispatch_job, IntervalTrigger(minutes=5))
    return scheduler


def main() -> None:
    logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(sys.stdout)])

    if len(sys.argv) > 1 and sys.argv[1] == "run-once":
        logger.info("Running reminder worker once")
        run_once()
        return

    scheduler = configure_scheduler()
    logger.info("Starting reminder worker scheduler")
    scheduler.start()


if __name__ == "__main__":
    main()
