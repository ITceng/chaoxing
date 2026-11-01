#!/usr/bin/env python3
"""Collect secret-free host signals and run the backend's persisted alert check."""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


UTC = dt.timezone.utc


def emit(level: str, event_type: str, **fields: Any) -> None:
    record = {
        "timestamp": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "level": level,
        "service": "host-monitor",
        "event_type": event_type,
        **fields,
    }
    print(json.dumps(record, sort_keys=True, separators=(",", ":")), flush=True)


def read_env_value(path: Path, key: str) -> str:
    value = os.environ.get(key, "").strip()
    if value:
        return value
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        if line.startswith(f"{key}="):
            return line.partition("=")[2].strip().strip("\"'")
    return ""


def run(args: list[str], *, timeout: int = 12) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None


def cpu_percent() -> float | None:
    def ticks() -> tuple[int, int] | None:
        try:
            values = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
            if len(values) < 4:
                return None
            return sum(values), values[3] + (values[4] if len(values) > 4 else 0)
        except (OSError, ValueError, IndexError):
            return None
    first = ticks()
    time.sleep(0.15)
    second = ticks()
    if not first or not second or second[0] <= first[0]:
        return None
    total, idle = second[0] - first[0], second[1] - first[1]
    return round(max(0.0, min(100.0, 100.0 * (total - idle) / total)), 1)


def memory_percent() -> float | None:
    try:
        values = {}
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0])
        if values.get("MemTotal", 0) <= 0 or "MemAvailable" not in values:
            return None
        return round(100.0 * (values["MemTotal"] - values["MemAvailable"]) / values["MemTotal"], 1)
    except (OSError, ValueError, IndexError):
        return None


def disk_metrics(path: Path) -> dict[str, int | float | None]:
    try:
        usage = shutil.disk_usage(path)
        return {
            "total_bytes": usage.total,
            "free_bytes": usage.free,
            "used_percent": round(100.0 * usage.used / usage.total, 1) if usage.total else None,
        }
    except OSError:
        return {"total_bytes": None, "free_bytes": None, "used_percent": None}


def compose_command(system_dir: Path, env_file: Path, compose_file: Path, ops_file: Path,
                    *args: str) -> list[str]:
    return ["docker", "compose", "--project-directory", str(system_dir), "--env-file", str(env_file),
            "-f", str(compose_file), "-f", str(ops_file), *args]


def parse_percent(value: Any) -> float | None:
    if not isinstance(value, str) or not value.endswith("%"):
        return None
    try:
        parsed = float(value[:-1].strip())
        return round(parsed, 2) if 0 <= parsed <= 10000 else None
    except ValueError:
        return None


def parse_bytes(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    units = {"B": 1, "kB": 1000, "MB": 1000**2, "GB": 1000**3, "TB": 1000**4,
             "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3, "TiB": 1024**4}
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*(B|kB|MB|GB|TB|KiB|MiB|GiB|TiB)\s*", value)
    if not match:
        return None
    try:
        return int(float(match.group(1)) * units[match.group(2)])
    except (ValueError, TypeError):
        return None


def container_resources(container_ids: list[str]) -> dict[str, int | float | None]:
    empty = {"cpu_percent": None, "memory_used_bytes": None, "memory_limit_bytes": None,
             "memory_percent": None, "pids_total": None}
    if not container_ids:
        return empty
    result = run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *container_ids], timeout=15)
    if result is None or result.returncode:
        return empty
    cpu_values = []
    memory_used = memory_limit = pids = 0
    memory_rows = 0
    pids_rows = 0
    for line in result.stdout.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        cpu = parse_percent(row.get("CPUPerc"))
        if cpu is not None:
            cpu_values.append(cpu)
        usage = row.get("MemUsage", "")
        used_text, _, limit_text = usage.partition("/") if isinstance(usage, str) else ("", "", "")
        used_bytes, limit_bytes = parse_bytes(used_text), parse_bytes(limit_text)
        if used_bytes is not None and limit_bytes is not None:
            memory_used += used_bytes
            memory_limit += limit_bytes
            memory_rows += 1
        if "PIDs" in row:
            try:
                pids += max(0, int(row["PIDs"]))
                pids_rows += 1
            except (TypeError, ValueError):
                pass
    return {
        "cpu_percent": round(sum(cpu_values), 2) if cpu_values else None,
        "memory_used_bytes": memory_used if memory_rows else None,
        "memory_limit_bytes": memory_limit if memory_rows else None,
        "memory_percent": round(100.0 * memory_used / memory_limit, 2) if memory_rows and memory_limit else None,
        "pids_total": pids if pids_rows else None,
    }


def compose_service_state(base: list[str], name: str) -> dict[str, Any]:
    result = run([*base, "ps", "-q", name])
    if result is None or result.returncode:
        return {"state": "unknown", "instances": None}
    container_ids = [item for item in result.stdout.splitlines() if item.strip()]
    if not container_ids:
        return {"state": "not_deployed", "instances": 0, "resources": container_resources([])}
    states = []
    for container_id in container_ids[:16]:
        inspected = run(["docker", "inspect", "--format", "{{json .State}}", container_id])
        if inspected is None or inspected.returncode:
            states.append("unknown")
            continue
        try:
            state = json.loads(inspected.stdout)
        except json.JSONDecodeError:
            states.append("unknown")
            continue
        status = str(state.get("Status", "unknown"))
        health = (state.get("Health") or {}).get("Status")
        if status != "running" or health == "unhealthy":
            states.append("unhealthy")
        elif health == "starting":
            states.append("starting")
        else:
            states.append("running")
    overall = ("unhealthy" if "unhealthy" in states else "unknown" if "unknown" in states
               else "starting" if "starting" in states else "running")
    return {"state": overall, "instances": len(container_ids), "resources": container_resources(container_ids)}


def readiness(edge_port: str) -> dict[str, Any]:
    start = time.monotonic()
    request = urllib.request.Request(
        f"http://127.0.0.1:{edge_port}/api/v1/health/ready", headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = response.read(8192)
            parsed = json.loads(body)
            state = "ready" if response.status == 200 and parsed.get("status") == "ready" else "not_ready"
            return {"state": state, "http_status": response.status,
                    "latency_ms": round((time.monotonic() - start) * 1000, 2)}
    except urllib.error.HTTPError as exc:
        return {"state": "not_ready", "http_status": int(exc.code),
                "latency_ms": round((time.monotonic() - start) * 1000, 2)}
    except (OSError, TimeoutError, ValueError):
        return {"state": "not_ready", "http_status": None,
                "latency_ms": round((time.monotonic() - start) * 1000, 2)}


def latest_backup(backup_dir: Path) -> dict[str, Any]:
    if backup_dir.is_symlink() or (backup_dir.exists() and not backup_dir.is_dir()):
        return {"state": "unknown", "latest_at": None, "archive_name": None,
                "archive_bytes": None, "age_seconds": None, "retained_count": None}
    manifests = []
    try:
        for sidecar in backup_dir.glob("unified_learning_*.dump.age.json"):
            if sidecar.is_symlink() or not sidecar.is_file() or sidecar.stat().st_size > 8192:
                continue
            try:
                value = json.loads(sidecar.read_text(encoding="utf-8"))
                archive_name = value.get("archive")
                if (value.get("manifest_version") != 1 or not isinstance(archive_name, str)
                        or Path(archive_name).name != archive_name or sidecar.name != f"{archive_name}.json"):
                    continue
                archive = backup_dir / archive_name
                if archive.is_symlink() or not archive.is_file():
                    continue
                stat_result = archive.stat()
                if stat_result.st_size != value.get("archive_bytes"):
                    continue
                created = dt.datetime.fromisoformat(str(value["created_at_utc"]).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    continue
                manifests.append((created.astimezone(UTC), archive_name, stat_result.st_size))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
    except OSError:
        pass
    if not manifests:
        return {"state": "missing", "latest_at": None, "archive_name": None,
                "archive_bytes": None, "age_seconds": None, "retained_count": 0}
    created, archive_name, size = max(manifests)
    age = max(0, int((dt.datetime.now(UTC) - created).total_seconds()))
    return {"state": "available", "latest_at": created.isoformat().replace("+00:00", "Z"),
            "archive_name": archive_name, "archive_bytes": size, "age_seconds": age,
            "retained_count": len(manifests)}


def backup_service_state() -> dict[str, Any]:
    result = run(["systemctl", "show", "unified-learning-backup.service", "--property=Result",
                  "--property=ExecMainStatus", "--property=ExecMainExitTimestamp", "--value"])
    if result is None or result.returncode:
        return {"state": "unknown", "result": None, "exit_status": None, "finished_at": None}
    values = result.stdout.splitlines()
    values += [""] * (3 - len(values))
    result_text, exit_status, finished = values[:3]
    try:
        exit_code = int(exit_status)
    except ValueError:
        exit_code = None
    finished_iso = None
    for format_string in ("%a %Y-%m-%d %H:%M:%S %Z", "%Y-%m-%d %H:%M:%S %Z"):
        try:
            finished_iso = dt.datetime.strptime(finished, format_string).replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")
            break
        except ValueError:
            continue
    return {"state": "failed" if result_text in {"failed", "exit-code", "signal"} or (exit_code not in (None, 0)) else "ok",
            "result": result_text[:32] or "unknown", "exit_status": exit_code,
            "finished_at": finished_iso}


def certificate_days(env_path: Path) -> dict[str, Any]:
    cert_path = read_env_value(env_path, "TLS_FULLCHAIN_PATH")
    if not cert_path or not Path(cert_path).is_file():
        return {"state": "unknown", "days_remaining": None}
    result = run(["openssl", "x509", "-in", cert_path, "-noout", "-enddate"], timeout=8)
    if result is None or result.returncode:
        return {"state": "unknown", "days_remaining": None}
    try:
        not_after = result.stdout.strip().partition("=")[2]
        expiry = dt.datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        days = int((expiry - dt.datetime.now(UTC)).total_seconds() // 86400)
        return {"state": "expired" if days < 0 else "valid", "days_remaining": days}
    except ValueError:
        return {"state": "unknown", "days_remaining": None}


def write_snapshot(path: Path, snapshot: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.parent.is_symlink() or path.is_symlink():
        raise RuntimeError("metrics path must not traverse a symlink")
    fd, temporary = tempfile.mkstemp(prefix=".host-metrics-", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o644)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def main() -> int:
    system_dir = Path(os.environ.get("SYSTEM_DIR", Path(__file__).resolve().parent.parent)).resolve()
    deploy_dir = Path(os.environ.get("DEPLOY_DIR", system_dir / "deploy")).resolve()
    env_file = Path(os.environ.get("ENV_FILE", deploy_dir / ".env.production"))
    nginx_env = Path(os.environ.get("NGINX_ENV_FILE", deploy_dir / "nginx" / ".env.nginx"))
    metrics_override = os.environ.get("MONITOR_METRICS_FILE", "").strip()
    if not metrics_override:
        metrics_override = os.environ.get("HOST_METRICS_FILE", "").strip() or read_env_value(
            env_file, "HOST_METRICS_FILE"
        )
    metrics_path = Path(metrics_override or "/var/lib/unified-learning/host-metrics.json")
    backup_dir = Path(os.environ.get("BACKUP_DIR", deploy_dir / "backups"))
    compose_file = system_dir / "docker-compose.prod.yml"
    ops_file = system_dir / "docker-compose.ops.yml"
    if not env_file.is_file() or not compose_file.is_file() or not ops_file.is_file():
        emit("ERROR", "host_monitor.preflight_failed", reason="required_configuration_missing")
        return 2
    try:
        metrics_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        if metrics_path.parent.is_symlink() or metrics_path.is_symlink():
            raise OSError("metrics path symlink refused")
        os.chmod(metrics_path.parent, 0o755)
        if not metrics_path.exists():
            write_snapshot(metrics_path, {
                "schema_version": 1,
                "captured_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "monitor": {"ops_check_state": "initializing", "ops_check_at": None},
            })
    except OSError:
        emit("ERROR", "host_monitor.snapshot_write_failed", reason="metrics_path_unavailable")
        return 2
    edge_port = read_env_value(env_file, "EDGE_PORT") or "8080"
    if not edge_port.isdigit() or not 1 <= int(edge_port) <= 65535:
        edge_port = "8080"

    base = compose_command(system_dir, env_file, compose_file, ops_file)
    service_names = ("postgres", "backend", "edge", "worker-chaoxing", "worker-zhihuishu", "worker-yinghua")
    services = {name: compose_service_state(base, name) for name in service_names}
    nginx = run(["systemctl", "is-active", "nginx"], timeout=5)
    nginx_state = (nginx.stdout.strip() if nginx and nginx.returncode == 0 else
                   "inactive" if nginx and nginx.returncode == 3 else "unknown")
    backup = latest_backup(backup_dir)
    backup["service"] = backup_service_state()
    captured = dt.datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        load_1m = round(os.getloadavg()[0], 2)
    except (OSError, AttributeError):
        load_1m = None
    snapshot = {
        "schema_version": 1,
        "captured_at": captured,
        "host": {"cpu_percent": cpu_percent(), "memory_percent": memory_percent(),
                 "load_1m": load_1m, "root_disk": disk_metrics(Path("/")),
                 "backup_disk": disk_metrics(backup_dir) if backup_dir.exists() and backup_dir.is_dir() and not backup_dir.is_symlink() else
                               {"total_bytes": None, "free_bytes": None, "used_percent": None}},
        "services": {"nginx": {"state": nginx_state}, **services},
        "readiness": readiness(edge_port),
        "backup": backup,
        "certificate": certificate_days(nginx_env),
        "monitor": {"ops_check_state": "pending", "ops_check_at": None},
    }
    try:
        write_snapshot(metrics_path, snapshot)
    except OSError:
        emit("ERROR", "host_monitor.snapshot_write_failed", reason="metrics_path_unavailable")
        return 2

    checked = run([*base, "exec", "-T", "backend", "flask", "--app", "unified_system.app:create_app", "ops-check"], timeout=40)
    snapshot["monitor"] = {
        "ops_check_state": "ok" if checked and checked.returncode == 0 else "failed",
        "ops_check_at": dt.datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    try:
        write_snapshot(metrics_path, snapshot)
    except OSError:
        emit("ERROR", "host_monitor.snapshot_write_failed", reason="metrics_path_update_unavailable")
        return 2
    if checked is None or checked.returncode:
        emit("ERROR", "host_monitor.ops_check_failed", reason="backend_alert_check_unavailable")
        return 1
    emit("INFO", "host_monitor.snapshot_written", ops_check="ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
