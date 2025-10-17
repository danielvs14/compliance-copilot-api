from datetime import datetime, timedelta, timezone
from typing import List
from uuid import uuid4

import pytest

from api.db.session import SessionLocal
from api.models.documents import Document
from api.models.events import Event
from api.models.memberships import Membership, MembershipRole
from api.models.org_metrics import OrgRequirementMetrics
from api.models.orgs import Org
from api.models.permits import Permit
from api.models.reminder_jobs import ReminderJob, ReminderStatusEnum
from api.models.requirements import (
    Requirement,
    RequirementAnchorTypeEnum,
    RequirementFrequencyEnum,
    RequirementStatusEnum,
)
from api.models.training_certs import TrainingCert
from api.models.users import User
from api.services.reminders import dispatch_reminders, queue_reminders


def _setup_org_with_member(session) -> Org:
    org = Org(name="QA Electric", primary_trade="electrical")
    user = User(email=f"owner+{uuid4()}@example.com", preferred_locale="en")
    session.add_all([org, user])
    session.flush()

    membership = Membership(org_id=org.id, user_id=user.id, role=MembershipRole.OWNER)
    session.add(membership)
    session.flush()
    return org


@pytest.mark.integration
def test_queue_reminders_creates_jobs_for_open_requirement() -> None:
    session = SessionLocal()
    try:
        org = _setup_org_with_member(session)

        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        document = Document(
            org_id=org.id,
            name="manual.pdf",
            storage_url="s3://bucket/manual.pdf",
        )
        session.add(document)
        session.flush()

        requirement = Requirement(
            org_id=org.id,
            document_id=document.id,
            title_en="Test task",
            title_es="Tarea de prueba",
            description_en="Do the thing",
            description_es="Haz la cosa",
            category="safety",
            frequency=RequirementFrequencyEnum.WEEKLY,
            anchor_type=RequirementAnchorTypeEnum.UPLOAD_DATE,
            anchor_value={"date": base_time.isoformat()},
            due_date=base_time + timedelta(days=7),
            next_due=base_time + timedelta(days=7),
            status=RequirementStatusEnum.OPEN,
            source_ref="manual",
        )
        session.add(requirement)
        session.commit()

        stats = queue_reminders(session, now=base_time)
        session.commit()

        jobs: List[ReminderJob] = (
            session.query(ReminderJob).filter(ReminderJob.target_id == requirement.id).all()
        )

        assert stats["scheduled"] == len(jobs) == 2  # offsets 7 & 1 days
        offsets = {job.reminder_offset_days for job in jobs}
        assert offsets == {7, 1}

        events = session.query(Event).filter(Event.type == "reminder_scheduled").all()
        assert len(events) == 2

        metrics = (
            session.query(OrgRequirementMetrics)
            .filter(OrgRequirementMetrics.org_id == org.id)
            .one()
        )
        assert metrics.reminders_scheduled_total == 2
    finally:
        session.close()


@pytest.mark.integration
def test_queue_reminders_covers_permits_and_training() -> None:
    session = SessionLocal()
    try:
        org = _setup_org_with_member(session)

        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        permit = Permit(
            org_id=org.id,
            name="City Master Permit",
            permit_number="AUS-55821",
            permit_type="License",
            jurisdiction="City of Austin",
            issued_at=base_time - timedelta(days=120),
            expires_at=base_time + timedelta(days=60),
            storage_url="s3://bucket/permits/city-master-permit.pdf",
        )
        cert = TrainingCert(
            org_id=org.id,
            worker_name="Jordan Lewis",
            certification_type="OSHA 30",
            authority="OSHA",
            issued_at=base_time - timedelta(days=300),
            expires_at=base_time + timedelta(days=45),
            storage_url="s3://bucket/training/jordan-lewis-osha30.pdf",
        )
        session.add_all([permit, cert])
        session.commit()

        stats = queue_reminders(session, now=base_time)
        session.commit()

        jobs = (
            session.query(ReminderJob)
            .filter(ReminderJob.target_type.in_(["permit", "training_cert"]))
            .all()
        )
        assert len(jobs) == 6  # 3 offsets per target type
        assert stats["scheduled"] == len(jobs)
        assert {job.reminder_offset_days for job in jobs} == {30, 7, 1}
        assert {job.target_type for job in jobs} == {"permit", "training_cert"}
    finally:
        session.close()


class DummyEmailClient:
    def __init__(self) -> None:
        self.sent_messages: List[str] = []

    def send(self, message) -> None:
        self.sent_messages.append(message.subject)


@pytest.mark.integration
def test_dispatch_reminders_sends_email_and_updates_metrics() -> None:
    session = SessionLocal()
    try:
        org = _setup_org_with_member(session)

        base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        document = Document(
            org_id=org.id,
            name="manual.pdf",
            storage_url="s3://bucket/manual.pdf",
        )
        session.add(document)
        session.flush()

        requirement = Requirement(
            org_id=org.id,
            document_id=document.id,
            title_en="Inspect harness",
            title_es="Inspeccionar arnés",
            description_en="Check before shift",
            description_es="Revisar antes del turno",
            category="safety",
            frequency=RequirementFrequencyEnum.WEEKLY,
            anchor_type=RequirementAnchorTypeEnum.UPLOAD_DATE,
            anchor_value={"date": base_time.isoformat()},
            due_date=base_time + timedelta(days=7),
            next_due=base_time + timedelta(days=7),
            status=RequirementStatusEnum.OPEN,
            source_ref="manual",
        )
        session.add(requirement)
        session.commit()

        queue_reminders(session, now=base_time)
        session.commit()

        job = (
            session.query(ReminderJob)
            .filter(
                ReminderJob.target_id == requirement.id,
                ReminderJob.reminder_offset_days == 7,
            )
            .one()
        )
        job.run_at = base_time
        session.add(job)
        session.commit()

        fake_client = DummyEmailClient()
        stats = dispatch_reminders(session, now=base_time, email_client=fake_client)
        session.commit()

        job = session.get(ReminderJob, job.id)
        assert job.status == ReminderStatusEnum.SENT
        assert stats["sent"] == 1
        assert fake_client.sent_messages  # captured subject(s)

        sent_events = session.query(Event).filter(Event.type == "reminder_sent").all()
        assert len(sent_events) == 1

        metrics = (
            session.query(OrgRequirementMetrics)
            .filter(OrgRequirementMetrics.org_id == org.id)
            .one()
        )
        assert metrics.reminders_sent_total == 1
    finally:
        session.close()
