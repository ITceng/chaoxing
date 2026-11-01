from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from unified_system.logging_utils import _redact_message
from unified_system.models import (
    AIProviderConfig, AIUsageLog, OperationalAlert, Platform, Task, WorkerInstance, utcnow,
)


UTC = timezone.utc
_ALERT_CODE_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
_SAFE_DETAIL_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SENSITIVE_DETAIL_KEY_RE = re.compile(r"(?i)(password|passwd|cookie|authorization|api[_-]?key|token|secret|credential|session)")


def _reject_json_constant(_value: str):
    raise ValueError("non-finite JSON number")


def iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def read_host_metrics(path: str | Path, *, now: datetime | None = None,
                      max_age_seconds: int = 180) -> dict[str, Any]:
    """Read a bounded, regular, no-follow host snapshot; stale values remain labeled stale."""
    result: dict[str, Any] = {"state": "missing", "captured_at": None, "age_seconds": None, "snapshot": {}}
    if not str(path or "").strip():
        return result
    try:
        fd = os.open(Path(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return result
    except OSError:
        result["state"] = "unavailable"
        return result
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
            result["state"] = "invalid"
            return result
        with os.fdopen(fd, "r", encoding="utf-8", closefd=False) as handle:
            serialized = handle.read(65537)
        if len(serialized) > 65536:
            result["state"] = "invalid"
            return result
        payload = json.loads(serialized, parse_constant=_reject_json_constant)
    except (OSError, UnicodeDecodeError, ValueError):
        result["state"] = "invalid"
        return result
    finally:
        os.close(fd)
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        result["state"] = "invalid"
        return result
    captured = parse_timestamp(payload.get("captured_at"))
    if captured is None:
        result["state"] = "invalid"
        return result
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    age = int((current.astimezone(UTC) - captured).total_seconds())
    if age < -60:
        result["state"] = "invalid"
        return result
    result.update({
        "state": "ok" if age <= max_age_seconds else "stale",
        "captured_at": iso_utc(captured),
        "age_seconds": max(0, age),
        "snapshot": payload,
    })
    return result


def _safe_details(value: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, item in (value or {}).items():
        if (not isinstance(key, str) or not _SAFE_DETAIL_KEY_RE.fullmatch(key)
                or _SENSITIVE_DETAIL_KEY_RE.search(key)):
            continue
        if item is None or isinstance(item, bool) or (isinstance(item, (int, float)) and math.isfinite(float(item))):
            safe[key] = item
        elif isinstance(item, str):
            safe[key] = _redact_message(item)[:128]
    return safe


@dataclass(frozen=True)
class AlertSignal:
    code: str
    severity: str
    title: str
    details: dict[str, Any]
    scope: str = "system"

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.code}\0{self.scope}".encode("utf-8")).hexdigest()


class AlertNotifier(Protocol):
    def notify(self, event: str, alert: OperationalAlert) -> None: ...


class JsonLogNotifier:
    """Local notification sink; Docker/journald log rotation and operator forwarding apply."""

    def __init__(self, logger):
        self.logger = logger

    def notify(self, event: str, alert: OperationalAlert) -> None:
        severity_level = {
            "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50,
        }.get(alert.severity, 30)
        level = 20 if event == "ops.alert.recovered" else severity_level
        self.logger.log(level, "operational alert state changed", extra={
            "event_type": event,
            "alert_code": alert.code,
            "severity": alert.severity,
            "fingerprint": alert.fingerprint,
        })


class AlertManager:
    def __init__(self, db: Session, notifier: AlertNotifier, cooldown_seconds: int = 1800):
        if not 60 <= cooldown_seconds <= 86400:
            raise ValueError("alert cooldown must be between 60 and 86400 seconds")
        self.db = db
        self.notifier = notifier
        self.cooldown = timedelta(seconds=cooldown_seconds)

    def _notify(self, event: str, alert: OperationalAlert, now: datetime) -> None:
        try:
            self.notifier.notify(event, alert)
            alert.last_notified_at = now
            alert.last_notification_error_type = ""
        except Exception as exc:  # external notifier failures must not leak payloads or stop reconciliation
            alert.notification_failures += 1
            alert.last_notification_error_type = type(exc).__name__[:64]
            logger = getattr(self.notifier, "logger", None)
            if logger is not None:
                try:
                    logger.error("operational alert notification failed", extra={
                        "event_type": "ops.alert.notification_failed",
                        "alert_code": alert.code,
                        "severity": alert.severity,
                        "fingerprint": alert.fingerprint,
                        "error_type": type(exc).__name__,
                    })
                except Exception:
                    pass

    def reconcile(self, signals: list[AlertSignal], *, now: datetime | None = None) -> dict[str, int]:
        current = now or utcnow()
        normalized: dict[str, AlertSignal] = {}
        for signal in signals:
            if (not _ALERT_CODE_RE.fullmatch(signal.code) or signal.severity not in {"INFO", "WARNING", "ERROR", "CRITICAL"}
                    or not signal.title or len(signal.title) > 160):
                continue
            normalized[signal.fingerprint] = signal
        stats = {"active": 0, "opened": 0, "notified": 0, "recovered": 0}
        existing = {item.fingerprint: item for item in self.db.scalars(select(OperationalAlert)).all()}
        for fingerprint, signal in normalized.items():
            alert = existing.get(fingerprint)
            was_open = alert is not None and alert.state == "active"
            severity_changed = was_open and alert.severity != signal.severity
            reopened = alert is not None and alert.state == "resolved"
            if alert is None:
                alert = OperationalAlert(
                    fingerprint=fingerprint, code=signal.code, severity=signal.severity,
                    state="active", title=signal.title, details=_safe_details(signal.details),
                    first_seen_at=current, last_seen_at=current, occurrence_count=1,
                )
                self.db.add(alert)
                self.db.flush()
                stats["opened"] += 1
            else:
                alert.code = signal.code
                alert.severity = signal.severity
                alert.title = signal.title
                alert.details = _safe_details(signal.details)
                alert.last_seen_at = current
                alert.occurrence_count += 1
                alert.state = "active"
                alert.resolved_at = None
            due = (not was_open or alert.last_notified_at is None
                   or current - alert.last_notified_at >= self.cooldown or severity_changed)
            if due:
                event = "ops.alert.reopened" if reopened else "ops.alert.opened" if not was_open else "ops.alert.reminder"
                self._notify(event, alert, current)
                stats["notified"] += 1
            stats["active"] += 1

        for fingerprint, alert in existing.items():
            if alert.state != "active" or fingerprint in normalized:
                continue
            alert.state = "resolved"
            alert.resolved_at = current
            alert.last_seen_at = current
            self._notify("ops.alert.recovered", alert, current)
            stats["recovered"] += 1
        retention_cutoff = current - timedelta(days=180)
        self.db.execute(delete(OperationalAlert).where(
            OperationalAlert.state == "resolved", OperationalAlert.resolved_at < retention_cutoff,
        ))
        return stats


def _field(snapshot: dict[str, Any], path: tuple[str, ...], default=None):
    value: Any = snapshot
    for key in path:
        if not isinstance(value, dict):
            return default
        value = value.get(key)
    return default if value is None else value


def _alert_signals(db: Session, metrics: dict[str, Any], now: datetime, worker_ttl: int = 90) -> list[AlertSignal]:
    signals: list[AlertSignal] = []
    snapshot = metrics.get("snapshot") if isinstance(metrics.get("snapshot"), dict) else {}
    host_state = metrics.get("state")
    if host_state != "ok":
        signals.append(AlertSignal("host.metrics_unavailable", "WARNING", "Host monitoring snapshot unavailable or stale",
                                   {"state": str(host_state), "age_seconds": metrics.get("age_seconds")}))
    monitor_state = _field(snapshot, ("monitor", "ops_check_state"), "unknown")
    if monitor_state == "failed":
        signals.append(AlertSignal("ops.alert_evaluation_failed", "ERROR", "The prior scheduled alert evaluation failed",
                                   {"monitor_state": "failed"}))
    readiness = _field(snapshot, ("readiness",), {})
    if isinstance(readiness, dict) and readiness.get("state") == "not_ready":
        signals.append(AlertSignal("backend.readiness_failed", "CRITICAL", "Backend/database readiness probe failed",
                                   {"http_status": readiness.get("http_status")}))
    for service in ("postgres", "backend", "edge"):
        state = _field(snapshot, ("services", service, "state"), "unknown")
        if state in {"unhealthy", "inactive", "failed"}:
            signals.append(AlertSignal(f"service.{service}.unhealthy", "ERROR", f"Required service {service} is unhealthy",
                                       {"service_state": state}, scope=service))
    nginx_state = _field(snapshot, ("services", "nginx", "state"), "unknown")
    if nginx_state in {"inactive", "failed"}:
        signals.append(AlertSignal("service.host_nginx.inactive", "ERROR", "Host Nginx service is inactive",
                                   {"service_state": nginx_state}))

    for disk_name in ("root_disk", "backup_disk"):
        percent = _field(snapshot, ("host", disk_name, "used_percent"))
        if isinstance(percent, (int, float)):
            severity = "CRITICAL" if percent >= 95 else "WARNING" if percent >= 85 else ""
            if severity:
                signals.append(AlertSignal(f"disk.{disk_name}.high", severity, f"{disk_name.replace('_', ' ')} usage is high",
                                           {"used_percent": round(float(percent), 1)}, scope=disk_name))
    memory = _field(snapshot, ("host", "memory_percent"))
    if isinstance(memory, (int, float)) and memory >= 95:
        signals.append(AlertSignal("host.memory.critical", "CRITICAL", "Host memory utilization is critical",
                                   {"used_percent": round(float(memory), 1)}))
    elif isinstance(memory, (int, float)) and memory >= 88:
        signals.append(AlertSignal("host.memory.high", "WARNING", "Host memory utilization is high",
                                   {"used_percent": round(float(memory), 1)}))
    cpu = _field(snapshot, ("host", "cpu_percent"))
    if isinstance(cpu, (int, float)) and cpu >= 98:
        signals.append(AlertSignal("host.cpu.critical", "CRITICAL", "Host CPU utilization is critical",
                                   {"used_percent": round(float(cpu), 1)}))
    elif isinstance(cpu, (int, float)) and cpu >= 90:
        signals.append(AlertSignal("host.cpu.high", "WARNING", "Host CPU utilization is high",
                                   {"used_percent": round(float(cpu), 1)}))

    backup = _field(snapshot, ("backup",), {})
    if isinstance(backup, dict):
        backup_service = backup.get("service") if isinstance(backup.get("service"), dict) else {}
        if backup_service.get("state") == "failed":
            signals.append(AlertSignal("backup.last_run_failed", "ERROR", "Most recent scheduled backup service run failed",
                                       {"exit_status": backup_service.get("exit_status")}))
        age = backup.get("age_seconds")
        if backup.get("state") == "missing":
            signals.append(AlertSignal("backup.never_succeeded", "WARNING", "No backup archive and manifest are available", {}))
        elif isinstance(age, int) and age >= 72 * 3600:
            signals.append(AlertSignal("backup.overdue", "CRITICAL", "Latest verified backup is more than 72 hours old",
                                       {"age_hours": age // 3600}))
        elif isinstance(age, int) and age >= 36 * 3600:
            signals.append(AlertSignal("backup.overdue", "WARNING", "Latest verified backup is more than 36 hours old",
                                       {"age_hours": age // 3600}))
    certificate_days = _field(snapshot, ("certificate", "days_remaining"))
    if isinstance(certificate_days, int) and certificate_days < 0:
        signals.append(AlertSignal("tls.certificate_expired", "CRITICAL", "Configured TLS certificate has expired",
                                   {"days_remaining": certificate_days}))
    elif isinstance(certificate_days, int) and certificate_days <= 7:
        signals.append(AlertSignal("tls.certificate_expiry", "CRITICAL", "TLS certificate expires within seven days",
                                   {"days_remaining": certificate_days}))
    elif isinstance(certificate_days, int) and certificate_days <= 30:
        signals.append(AlertSignal("tls.certificate_expiry", "WARNING", "TLS certificate expires within 30 days",
                                   {"days_remaining": certificate_days}))

    active_platforms = db.scalars(select(Platform).where(Platform.integration_status == "active")).all()
    if active_platforms:
        cutoff = now - timedelta(seconds=worker_ttl)
        for platform in active_platforms:
            total = db.scalar(select(func.count(WorkerInstance.id)).where(WorkerInstance.platform_id == platform.id)) or 0
            online = db.scalar(select(func.count(WorkerInstance.id)).where(
                WorkerInstance.platform_id == platform.id,
                WorkerInstance.status == "online",
                WorkerInstance.last_seen_at >= cutoff,
            )) or 0
            if online == 0:
                signals.append(AlertSignal("worker.no_fresh_heartbeat", "ERROR" if total else "WARNING",
                                           "Active platform has no Worker with a fresh heartbeat",
                                           {"worker_count": total, "online_count": 0}, scope=platform.key))

    recent_cutoff = now - timedelta(minutes=15)
    failed_tasks = db.scalar(select(func.count(Task.id)).where(
        Task.status.in_(("failed", "timed_out")), Task.finished_at >= recent_cutoff,
    )) or 0
    if failed_tasks >= 5:
        signals.append(AlertSignal("tasks.failure_burst", "ERROR", "Five or more platform tasks failed in the last 15 minutes",
                                   {"failure_count": int(failed_tasks)}))
    queued = db.scalar(select(func.count(Task.id)).where(Task.status == "queued")) or 0
    oldest = db.scalar(select(func.min(Task.created_at)).where(Task.status == "queued"))
    if queued >= 50:
        signals.append(AlertSignal("tasks.queue_backlog", "WARNING", "Task queue backlog is at least 50 tasks",
                                   {"queued_count": int(queued)}))
    if oldest is not None and (now - oldest).total_seconds() >= 3600:
        signals.append(AlertSignal("tasks.queue_stalled", "ERROR", "The oldest queued task has waited over one hour",
                                   {"wait_minutes": int((now - oldest).total_seconds() // 60)}))

    enabled_ai = db.scalar(select(func.count(AIProviderConfig.id)).where(AIProviderConfig.enabled.is_(True))) or 0
    ten_min_ago = now - timedelta(minutes=10)
    ai_failures = db.scalar(select(func.count(AIUsageLog.id)).where(
        AIUsageLog.created_at >= ten_min_ago, AIUsageLog.status.in_(("failed", "error")),
    )) or 0
    if enabled_ai and ai_failures >= 3:
        signals.append(AlertSignal("ai.provider.failure_burst", "WARNING", "At least three AI provider usage failures were recorded in ten minutes",
                                   {"failure_count": int(ai_failures), "enabled_provider_count": int(enabled_ai)}))
    return signals


def build_ops_overview(db: Session, metrics_path: str | Path, *, now: datetime | None = None,
                       worker_ttl_seconds: int = 90) -> dict[str, Any]:
    current = now or utcnow()
    if current.tzinfo is not None:
        current = current.astimezone(UTC).replace(tzinfo=None)
    metrics = read_host_metrics(metrics_path, now=current.replace(tzinfo=UTC))
    snapshot = metrics.get("snapshot") if isinstance(metrics.get("snapshot"), dict) else {}

    task_statuses = {str(status): int(count) for status, count in db.execute(
        select(Task.status, func.count(Task.id)).group_by(Task.status)
    ).all()}
    queued = int(task_statuses.get("queued", 0))
    oldest = db.scalar(select(func.min(Task.created_at)).where(Task.status == "queued"))
    oldest_minutes = round(max(0.0, (current - oldest).total_seconds() / 60), 1) if oldest else None
    failure_cutoff = current - timedelta(minutes=15)
    task_failures = int(db.scalar(select(func.count(Task.id)).where(
        Task.status.in_(("failed", "timed_out")), Task.finished_at >= failure_cutoff,
    )) or 0)

    platforms = db.scalars(select(Platform).order_by(Platform.name)).all()
    worker_cutoff = current - timedelta(seconds=worker_ttl_seconds)
    worker_rows = db.execute(select(WorkerInstance.platform_id, WorkerInstance.status, WorkerInstance.last_seen_at)).all()
    platform_workers: dict[str, list] = {}
    for platform_id, status, seen in worker_rows:
        platform_workers.setdefault(platform_id, []).append((status, seen))
    platform_summary = []
    total_workers = online_workers = 0
    active_missing = 0
    for platform in platforms:
        records = platform_workers.get(platform.id, [])
        online = sum(status == "online" and seen is not None and seen >= worker_cutoff for status, seen in records)
        total_workers += len(records)
        online_workers += online
        if platform.integration_status == "active" and online == 0:
            active_missing += 1
        state = "not_integrated" if platform.integration_status != "active" else "online" if online else "offline"
        platform_summary.append({
            "key": platform.key, "name": platform.name,
            "integration_status": platform.integration_status,
            "worker_count": len(records), "online_workers": int(online), "state": state,
            "last_heartbeat_at": iso_utc(max((seen for _, seen in records if seen is not None), default=None)),
        })
    worker_state = "not_integrated" if not any(p.integration_status == "active" for p in platforms) else (
        "degraded" if active_missing else "online"
    )

    ai_enabled = int(db.scalar(select(func.count(AIProviderConfig.id)).where(AIProviderConfig.enabled.is_(True))) or 0)
    day_cutoff = current - timedelta(hours=24)
    usage = db.execute(select(
        func.count(AIUsageLog.id), func.coalesce(func.sum(AIUsageLog.input_tokens), 0),
        func.coalesce(func.sum(AIUsageLog.output_tokens), 0),
    ).where(AIUsageLog.created_at >= day_cutoff)).one()
    usage_rows, input_tokens, output_tokens = int(usage[0] or 0), int(usage[1] or 0), int(usage[2] or 0)
    ai_state = "measured" if usage_rows else "no_usage_records" if ai_enabled else "not_integrated"
    ai_failure_cutoff = current - timedelta(minutes=10)
    ai_failures = int(db.scalar(select(func.count(AIUsageLog.id)).where(
        AIUsageLog.created_at >= ai_failure_cutoff, AIUsageLog.status.in_(("failed", "error")),
    )) or 0)
    active_alerts = db.scalars(select(OperationalAlert).where(OperationalAlert.state == "active")
                               .order_by(OperationalAlert.severity.desc(), OperationalAlert.last_seen_at.desc()).limit(100)).all()
    recent_alerts = db.scalars(select(OperationalAlert).where(OperationalAlert.state == "resolved")
                               .order_by(OperationalAlert.resolved_at.desc()).limit(25)).all()

    host = snapshot.get("host") if isinstance(snapshot.get("host"), dict) else {}

    def safe_number(value, *, integer=False):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            return None
        return int(value) if integer else round(float(value), 2)

    def safe_disk(value):
        if not isinstance(value, dict):
            return {"total_bytes": None, "free_bytes": None, "used_percent": None}
        return {"total_bytes": safe_number(value.get("total_bytes"), integer=True),
                "free_bytes": safe_number(value.get("free_bytes"), integer=True),
                "used_percent": safe_number(value.get("used_percent"))}

    host_metrics = {
        "state": metrics["state"], "captured_at": metrics["captured_at"], "age_seconds": metrics["age_seconds"],
        "cpu_percent": safe_number(host.get("cpu_percent")),
        "memory_percent": safe_number(host.get("memory_percent")),
        "load_1m": safe_number(host.get("load_1m")),
        "root_disk": safe_disk(host.get("root_disk")),
        "backup_disk": safe_disk(host.get("backup_disk")),
    }
    backup = snapshot.get("backup") if isinstance(snapshot.get("backup"), dict) else {}
    backup_service = backup.get("service") if isinstance(backup.get("service"), dict) else {}
    certificate = snapshot.get("certificate") if isinstance(snapshot.get("certificate"), dict) else {}
    readiness = snapshot.get("readiness") if isinstance(snapshot.get("readiness"), dict) else {}
    monitor = snapshot.get("monitor") if isinstance(snapshot.get("monitor"), dict) else {}
    services = snapshot.get("services") if isinstance(snapshot.get("services"), dict) else {}
    public_services = {}
    for key in ("nginx", "postgres", "backend", "edge", "worker-chaoxing", "worker-zhihuishu", "worker-yinghua"):
        value = services.get(key)
        if not isinstance(value, dict):
            public_services[key] = {"state": "unknown", "instances": None}
            continue
        state = value.get("state")
        if state not in {"running", "starting", "unhealthy", "not_deployed", "active", "inactive", "failed", "unknown"}:
            state = "unknown"
        resources = value.get("resources") if isinstance(value.get("resources"), dict) else {}
        public_services[key] = {
            "state": state,
            "instances": safe_number(value.get("instances"), integer=True),
            "resources": {
                "cpu_percent": safe_number(resources.get("cpu_percent")),
                "memory_used_bytes": safe_number(resources.get("memory_used_bytes"), integer=True),
                "memory_limit_bytes": safe_number(resources.get("memory_limit_bytes"), integer=True),
                "memory_percent": safe_number(resources.get("memory_percent")),
                "pids_total": safe_number(resources.get("pids_total"), integer=True),
            },
        }
    readiness_state = readiness.get("state") if readiness.get("state") in {"ready", "not_ready", "unknown"} else "unknown"
    backup_state = backup.get("state") if backup.get("state") in {"available", "missing", "unknown"} else "unknown"
    certificate_state = certificate.get("state") if certificate.get("state") in {"valid", "expired", "unknown"} else "unknown"
    return {
        "generated_at": iso_utc(current),
        "monitor": {"ops_check_state": monitor.get("ops_check_state") if monitor.get("ops_check_state") in {"ok", "failed", "pending", "initializing"} else "unknown",
                    "ops_check_at": iso_utc(parse_timestamp(monitor.get("ops_check_at"))) if parse_timestamp(monitor.get("ops_check_at")) else None},
        "host": host_metrics,
        "services": public_services,
        "readiness": {"state": readiness_state,
                      "http_status": safe_number(readiness.get("http_status"), integer=True),
                      "latency_ms": safe_number(readiness.get("latency_ms"))},
        "backup": {"state": backup_state,
                   "latest_at": iso_utc(parse_timestamp(backup.get("latest_at"))) if parse_timestamp(backup.get("latest_at")) else None,
                   "archive_bytes": safe_number(backup.get("archive_bytes"), integer=True),
                   "age_seconds": safe_number(backup.get("age_seconds"), integer=True),
                   "retained_count": safe_number(backup.get("retained_count"), integer=True),
                   "service_state": backup_service.get("state") if backup_service.get("state") in {"ok", "failed", "unknown"} else "unknown",
                   "service_exit_status": safe_number(backup_service.get("exit_status"), integer=True),
                   "service_finished_at": iso_utc(parse_timestamp(backup_service.get("finished_at"))) if parse_timestamp(backup_service.get("finished_at")) else None},
        "certificate": {"state": certificate_state,
                        "days_remaining": safe_number(certificate.get("days_remaining"), integer=True)},
        "tasks": {"statuses": task_statuses, "queued": queued, "oldest_queued_minutes": oldest_minutes,
                  "failed_last_15m": task_failures},
        "workers": {"state": worker_state, "total": total_workers, "online": online_workers,
                    "offline": total_workers - online_workers, "active_platforms_without_worker": active_missing,
                    "platforms": platform_summary},
        "ai_usage": {"state": ai_state, "enabled_providers": ai_enabled, "records_last_24h": usage_rows,
                     "input_tokens_last_24h": input_tokens if usage_rows else None,
                     "output_tokens_last_24h": output_tokens if usage_rows else None,
                     "failures_last_10m": ai_failures},
        "alerts": {"active": [_alert_public(item) for item in active_alerts],
                    "recently_resolved": [_alert_public(item) for item in recent_alerts]},
    }


def _alert_public(alert: OperationalAlert) -> dict[str, Any]:
    return {
        "id": alert.id, "code": alert.code, "severity": alert.severity, "state": alert.state,
        "title": alert.title, "details": _safe_details(alert.details),
        "first_seen_at": iso_utc(alert.first_seen_at), "last_seen_at": iso_utc(alert.last_seen_at),
        "last_notified_at": iso_utc(alert.last_notified_at), "resolved_at": iso_utc(alert.resolved_at),
        "occurrence_count": alert.occurrence_count,
        "notification_failures": alert.notification_failures,
    }


def evaluate_and_persist_alerts(db: Session, metrics_path: str | Path, notifier: AlertNotifier,
                                *, now: datetime | None = None, cooldown_seconds: int = 1800,
                                worker_ttl_seconds: int = 90) -> dict[str, int]:
    current = now or utcnow()
    if current.tzinfo is not None:
        current = current.astimezone(UTC).replace(tzinfo=None)
    metrics = read_host_metrics(metrics_path, now=current.replace(tzinfo=UTC))
    signals = _alert_signals(db, metrics, current, worker_ttl_seconds)
    stats = AlertManager(db, notifier, cooldown_seconds).reconcile(signals, now=current)
    db.commit()
    return stats
