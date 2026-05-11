from __future__ import annotations

import services.agents.status as agent_status
import services.ops.watchdog as script_watchdog


def test_service_status_snapshot_includes_agent_events(monkeypatch) -> None:
    active_services = {
        "montanablotter.service",
        "nginx.service",
        "redis-server.service",
        "montanablotter-rq-ingestion.service",
        "montanablotter-rq-parsing.service",
        "montanablotter-rq-publishing.service",
        "montanablotter-agent-events.service",
    }

    monkeypatch.setattr(
        agent_status,
        "systemd_is_active",
        lambda service_name: service_name in active_services,
    )

    rows = agent_status.service_status_snapshot()

    labels = {row["label"]: row for row in rows}
    assert "Agent Events" in labels
    assert labels["Agent Events"]["service"] == "montanablotter-agent-events.service"
    assert labels["Agent Events"]["active"] is True


def test_run_watchdog_includes_agent_events_service(monkeypatch) -> None:
    monkeypatch.setattr(
        script_watchdog,
        "_check_systemd_service",
        lambda: {"name": "montanablotter.service", "kind": "service", "status": "ok", "details": "ok"},
    )
    monkeypatch.setattr(
        script_watchdog,
        "_check_web_service",
        lambda: {"name": "web", "kind": "service", "status": "ok", "details": "ok"},
    )
    monkeypatch.setattr(
        script_watchdog,
        "_check_agent_events_service",
        lambda: {"name": "montanablotter-agent-events.service", "kind": "service", "status": "ok", "details": "ok"},
        raising=False,
    )
    monkeypatch.setattr(script_watchdog, "_check_job", lambda job, now: {"name": job.name, "kind": "job", "status": "ok"})
    monkeypatch.setattr(script_watchdog, "_check_state_job", lambda job, now: {"name": job.name, "kind": "job", "status": "ok"})

    _, payload = script_watchdog.run_watchdog()

    assert payload["service_count"] == 3
    assert "montanablotter-agent-events.service" in [item["name"] for item in payload["service_checks"]]
