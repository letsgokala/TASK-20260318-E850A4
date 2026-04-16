"""Tests for metrics endpoint, notifications, and alert threshold access control."""
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import AlertThreshold, ComparisonOp, Notification, Severity
from tests.conftest import make_token


# ── Metrics access control ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_accessible_to_reviewer(client: AsyncClient, reviewer_user):
    """Reviewer may access the metrics endpoint."""
    resp = await client.get("/api/v1/metrics", headers=make_token(reviewer_user))
    assert resp.status_code == 200
    data = resp.json()
    assert "approval_rate" in data
    assert "correction_rate" in data
    assert "overspending_rate" in data


@pytest.mark.asyncio
async def test_metrics_accessible_to_finance(client: AsyncClient, finance_user):
    """Financial admin may access the metrics endpoint."""
    resp = await client.get("/api/v1/metrics", headers=make_token(finance_user))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_accessible_to_admin(client: AsyncClient, admin_user):
    """System admin may access the metrics endpoint."""
    resp = await client.get("/api/v1/metrics", headers=make_token(admin_user))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_metrics_blocked_for_applicant(client: AsyncClient, applicant_user):
    """Applicants must not access the metrics endpoint."""
    resp = await client.get("/api/v1/metrics", headers=make_token(applicant_user))
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_metrics_empty_database_returns_zero_rates(client: AsyncClient, reviewer_user):
    """With no registrations, all metric rates must be 0."""
    resp = await client.get("/api/v1/metrics", headers=make_token(reviewer_user))
    assert resp.status_code == 200
    data = resp.json()
    for metric_name in ("approval_rate", "correction_rate", "overspending_rate"):
        metric = data[metric_name]
        assert float(metric["value"]) == 0.0, f"{metric_name} should be 0.0 with no data"
        assert metric["breached"] is False


# ── Alert thresholds access control ───────────────────────────────────────

@pytest.mark.asyncio
async def test_alert_thresholds_blocked_for_non_admin(
    client: AsyncClient, reviewer_user, finance_user, applicant_user
):
    """Only system_admin may list alert thresholds."""
    for user in (reviewer_user, finance_user, applicant_user):
        resp = await client.get("/api/v1/alert-thresholds", headers=make_token(user))
        assert resp.status_code == 403, (
            f"Expected 403 for role {user.role}, got {resp.status_code}"
        )


@pytest.mark.asyncio
async def test_alert_thresholds_accessible_to_admin(client: AsyncClient, admin_user):
    """System admin may list alert thresholds (returns a list, possibly empty)."""
    resp = await client.get("/api/v1/alert-thresholds", headers=make_token(admin_user))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_update_alert_threshold_blocked_for_non_admin(
    client: AsyncClient, reviewer_user, db_session: AsyncSession, admin_user
):
    """Only system_admin may update alert thresholds."""
    threshold = AlertThreshold(
        metric_name="approval_rate",
        threshold_value=0.5,
        comparison=ComparisonOp.LT,
        updated_by=admin_user.id,
    )
    db_session.add(threshold)
    await db_session.commit()
    await db_session.refresh(threshold)

    resp = await client.put(
        f"/api/v1/alert-thresholds/{threshold.id}",
        json={"threshold_value": 0.3, "comparison": "lt"},
        headers=make_token(reviewer_user),
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_alert_threshold_by_admin(
    client: AsyncClient, admin_user, db_session: AsyncSession
):
    """System admin can update an existing alert threshold."""
    threshold = AlertThreshold(
        metric_name="correction_rate",
        threshold_value=0.4,
        comparison=ComparisonOp.GT,
        updated_by=admin_user.id,
    )
    db_session.add(threshold)
    await db_session.commit()
    await db_session.refresh(threshold)

    resp = await client.put(
        f"/api/v1/alert-thresholds/{threshold.id}",
        json={"threshold_value": 0.6, "comparison": "gt"},
        headers=make_token(admin_user),
    )
    assert resp.status_code == 200
    data = resp.json()
    assert float(data["threshold_value"]) == 0.6


# ── Notifications access control and scoping ──────────────────────────────

@pytest.mark.asyncio
async def test_notifications_returns_list_for_reviewer(client: AsyncClient, reviewer_user):
    """Reviewer gets a list of notifications (may be empty)."""
    resp = await client.get("/api/v1/notifications", headers=make_token(reviewer_user))
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_applicant_only_sees_own_notifications(
    client: AsyncClient, applicant_user, db_session: AsyncSession
):
    """Applicant must only see their own notifications, not global alerts."""
    # Global alert notification (user_id=None, visible to management only)
    global_notif = Notification(
        message="Threshold breached",
        severity=Severity.WARNING,
        user_id=None,
    )
    # Applicant-specific notification
    own_notif = Notification(
        message="Submitted successfully",
        severity=Severity.WARNING,
        user_id=applicant_user.id,
    )
    db_session.add(global_notif)
    db_session.add(own_notif)
    await db_session.commit()

    resp = await client.get("/api/v1/notifications", headers=make_token(applicant_user))
    assert resp.status_code == 200
    data = resp.json()
    ids = [n["id"] for n in data]
    assert global_notif.id not in ids, "Applicant must not see global alert notifications"
    assert own_notif.id in ids, "Applicant must see their own notifications"


@pytest.mark.asyncio
async def test_reviewer_sees_global_notifications(
    client: AsyncClient, reviewer_user, db_session: AsyncSession
):
    """Reviewer sees global alert notifications (user_id=None)."""
    global_notif = Notification(
        message="overspending_rate exceeded threshold",
        severity=Severity.CRITICAL,
        user_id=None,
    )
    db_session.add(global_notif)
    await db_session.commit()

    resp = await client.get("/api/v1/notifications", headers=make_token(reviewer_user))
    assert resp.status_code == 200
    ids = [n["id"] for n in resp.json()]
    assert global_notif.id in ids, "Reviewer must see global alert notifications"


@pytest.mark.asyncio
async def test_mark_notification_read(
    client: AsyncClient, applicant_user, db_session: AsyncSession
):
    """Marking a notification as read must return 204."""
    notif = Notification(
        message="Hello",
        severity=Severity.WARNING,
        user_id=applicant_user.id,
    )
    db_session.add(notif)
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/notifications/{notif.id}/read",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_mark_global_notification_read_blocked_for_applicant(
    client: AsyncClient, applicant_user, db_session: AsyncSession
):
    """Applicant must not mark global (management) notifications as read."""
    global_notif = Notification(
        message="Alert",
        severity=Severity.WARNING,
        user_id=None,
    )
    db_session.add(global_notif)
    await db_session.commit()

    resp = await client.put(
        f"/api/v1/notifications/{global_notif.id}/read",
        headers=make_token(applicant_user),
    )
    assert resp.status_code == 403
