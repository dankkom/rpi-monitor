#!/usr/bin/env python3
"""
rpi_monitor/collector.py
Coleta métricas do Raspberry Pi 4 e persiste no PostgreSQL.
Projetado para rodar via cronjob a cada minuto.
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import psutil
import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclasses de coleta
# ---------------------------------------------------------------------------

@dataclass
class CpuMetrics:
    usage_percent: float
    usage_per_core: list[float]
    frequency_mhz: Optional[float]
    frequency_min_mhz: Optional[float]
    frequency_max_mhz: Optional[float]
    ctx_switches: int
    interrupts: int
    soft_interrupts: int
    load_avg_1m: float
    load_avg_5m: float
    load_avg_15m: float


@dataclass
class TemperatureReading:
    zone: str
    celsius: float


@dataclass
class MemoryMetrics:
    total_bytes: int
    available_bytes: int
    used_bytes: int
    free_bytes: int
    cached_bytes: int
    buffers_bytes: int
    shared_bytes: int
    usage_percent: float
    swap_total_bytes: int
    swap_used_bytes: int
    swap_free_bytes: int
    swap_usage_percent: float


@dataclass
class DiskPartitionMetrics:
    device: str
    mountpoint: str
    fstype: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    usage_percent: float
    read_bytes: int
    write_bytes: int
    read_count: int
    write_count: int
    read_time_ms: int
    write_time_ms: int


@dataclass
class NetworkInterfaceMetrics:
    interface: str
    bytes_sent: int
    bytes_recv: int
    packets_sent: int
    packets_recv: int
    errin: int
    errout: int
    dropin: int
    dropout: int


@dataclass
class SystemMetrics:
    boot_time: datetime
    uptime_seconds: int
    process_count: int
    process_running: int
    process_sleeping: int
    process_zombie: int
    users_logged_in: int


@dataclass
class ThrottleMetrics:
    raw_hex: str
    under_voltage: bool
    freq_capped: bool
    currently_throttled: bool
    soft_temp_limit: bool
    under_voltage_occurred: bool
    freq_capped_occurred: bool
    throttled_occurred: bool
    soft_temp_occurred: bool


@dataclass
class Snapshot:
    hostname: str
    collected_at: datetime
    cpu: CpuMetrics
    temperatures: list[TemperatureReading]
    memory: MemoryMetrics
    disks: list[DiskPartitionMetrics]
    network: list[NetworkInterfaceMetrics]
    system: SystemMetrics
    throttle: Optional[ThrottleMetrics]


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

EXCLUDED_FILESYSTEMS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs"}
EXCLUDED_MOUNTPOINTS_PREFIX = ("/sys", "/proc", "/dev", "/run/user", "/snap")


def _collect_cpu() -> CpuMetrics:
    usage_percent = psutil.cpu_percent(interval=0.5)
    per_core = psutil.cpu_percent(interval=None, percpu=True)

    freq = psutil.cpu_freq()
    freq_current = freq.current if freq else None
    freq_min = freq.min if freq else None
    freq_max = freq.max if freq else None

    stats = psutil.cpu_stats()
    load = psutil.getloadavg()

    return CpuMetrics(
        usage_percent=usage_percent,
        usage_per_core=per_core,
        frequency_mhz=freq_current,
        frequency_min_mhz=freq_min,
        frequency_max_mhz=freq_max,
        ctx_switches=stats.ctx_switches,
        interrupts=stats.interrupts,
        soft_interrupts=stats.soft_interrupts,
        load_avg_1m=load[0],
        load_avg_5m=load[1],
        load_avg_15m=load[2],
    )


def _collect_temperatures() -> list[TemperatureReading]:
    readings: list[TemperatureReading] = []

    # psutil lê /sys/class/thermal
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for zone, entries in temps.items():
                for entry in entries:
                    label = entry.label or zone
                    readings.append(TemperatureReading(zone=label, celsius=entry.current))
    except AttributeError:
        # sensors_temperatures pode não existir em algumas plataformas
        pass

    # Fallback direto para thermal_zone* (garante funcionar no RPi)
    if not readings:
        base = Path("/sys/class/thermal")
        for zone_dir in sorted(base.glob("thermal_zone*")):
            try:
                temp_raw = (zone_dir / "temp").read_text().strip()
                zone_type = (zone_dir / "type").read_text().strip()
                readings.append(
                    TemperatureReading(zone=zone_type, celsius=int(temp_raw) / 1000.0)
                )
            except (OSError, ValueError):
                continue

    return readings


def _collect_memory() -> MemoryMetrics:
    vm = psutil.virtual_memory()
    sw = psutil.swap_memory()
    return MemoryMetrics(
        total_bytes=vm.total,
        available_bytes=vm.available,
        used_bytes=vm.used,
        free_bytes=vm.free,
        cached_bytes=getattr(vm, "cached", 0),
        buffers_bytes=getattr(vm, "buffers", 0),
        shared_bytes=getattr(vm, "shared", 0),
        usage_percent=vm.percent,
        swap_total_bytes=sw.total,
        swap_used_bytes=sw.used,
        swap_free_bytes=sw.free,
        swap_usage_percent=sw.percent,
    )


def _collect_disks() -> list[DiskPartitionMetrics]:
    io_counters = psutil.disk_io_counters(perdisk=True)
    results = []

    for part in psutil.disk_partitions(all=False):
        if part.fstype in EXCLUDED_FILESYSTEMS:
            continue
        if any(part.mountpoint.startswith(p) for p in EXCLUDED_MOUNTPOINTS_PREFIX):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue

        # Tenta casar o device com o io_counter (ex: mmcblk0p2 -> mmcblk0p2)
        dev_name = part.device.split("/")[-1]
        io = io_counters.get(dev_name) or io_counters.get(
            dev_name.rstrip("0123456789"), None
        )

        results.append(
            DiskPartitionMetrics(
                device=part.device,
                mountpoint=part.mountpoint,
                fstype=part.fstype,
                total_bytes=usage.total,
                used_bytes=usage.used,
                free_bytes=usage.free,
                usage_percent=usage.percent,
                read_bytes=io.read_bytes if io else 0,
                write_bytes=io.write_bytes if io else 0,
                read_count=io.read_count if io else 0,
                write_count=io.write_count if io else 0,
                read_time_ms=io.read_time if io else 0,
                write_time_ms=io.write_time if io else 0,
            )
        )

    return results


def _collect_network() -> list[NetworkInterfaceMetrics]:
    net_io = psutil.net_io_counters(pernic=True)
    results = []
    for iface, counters in net_io.items():
        if iface == "lo":
            continue
        results.append(
            NetworkInterfaceMetrics(
                interface=iface,
                bytes_sent=counters.bytes_sent,
                bytes_recv=counters.bytes_recv,
                packets_sent=counters.packets_sent,
                packets_recv=counters.packets_recv,
                errin=counters.errin,
                errout=counters.errout,
                dropin=counters.dropin,
                dropout=counters.dropout,
            )
        )
    return results


def _collect_system() -> SystemMetrics:
    boot_ts = psutil.boot_time()
    boot_dt = datetime.fromtimestamp(boot_ts, tz=timezone.utc)
    now = datetime.now(tz=timezone.utc)
    uptime = int((now - boot_dt).total_seconds())

    procs = list(psutil.process_iter(["status"]))
    status_counts: dict[str, int] = {}
    for p in procs:
        try:
            s = p.info["status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    users = psutil.users()

    return SystemMetrics(
        boot_time=boot_dt,
        uptime_seconds=uptime,
        process_count=len(procs),
        process_running=status_counts.get(psutil.STATUS_RUNNING, 0),
        process_sleeping=status_counts.get(psutil.STATUS_SLEEPING, 0),
        process_zombie=status_counts.get(psutil.STATUS_ZOMBIE, 0),
        users_logged_in=len(users),
    )


def _parse_throttle(raw_hex: str) -> ThrottleMetrics:
    """Interpreta o bitmask retornado por vcgencmd get_throttled."""
    val = int(raw_hex, 16)
    return ThrottleMetrics(
        raw_hex=raw_hex,
        under_voltage=bool(val & (1 << 0)),
        freq_capped=bool(val & (1 << 1)),
        currently_throttled=bool(val & (1 << 2)),
        soft_temp_limit=bool(val & (1 << 3)),
        under_voltage_occurred=bool(val & (1 << 16)),
        freq_capped_occurred=bool(val & (1 << 17)),
        throttled_occurred=bool(val & (1 << 18)),
        soft_temp_occurred=bool(val & (1 << 19)),
    )


def _collect_throttle() -> Optional[ThrottleMetrics]:
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode == 0:
            # output: "throttled=0x50000"
            raw = result.stdout.strip().split("=")[1]
            return _parse_throttle(raw)
    except (FileNotFoundError, subprocess.TimeoutExpired, IndexError, ValueError):
        log.debug("vcgencmd não disponível ou falhou — throttle não coletado")
    return None


def collect_snapshot() -> Snapshot:
    return Snapshot(
        hostname=socket.gethostname(),
        collected_at=datetime.now(tz=timezone.utc),
        cpu=_collect_cpu(),
        temperatures=_collect_temperatures(),
        memory=_collect_memory(),
        disks=_collect_disks(),
        network=_collect_network(),
        system=_collect_system(),
        throttle=_collect_throttle(),
    )


# ---------------------------------------------------------------------------
# Persistência
# ---------------------------------------------------------------------------

def _upsert_host(conn: psycopg.Connection, hostname: str) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.hosts (hostname)
            VALUES (%(hostname)s)
            ON CONFLICT (hostname) DO UPDATE SET hostname = EXCLUDED.hostname
            RETURNING id
            """,
            {"hostname": hostname},
        )
        row = cur.fetchone()
        return row["id"]


def _insert_metric(conn: psycopg.Connection, host_id: int, collected_at: datetime) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.metrics (host_id, collected_at)
            VALUES (%(host_id)s, %(collected_at)s)
            RETURNING id
            """,
            {"host_id": host_id, "collected_at": collected_at},
        )
        return cur.fetchone()["id"]


def _insert_cpu(conn: psycopg.Connection, metric_id: int, cpu: CpuMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.cpu (
                metric_id, usage_percent, usage_per_core,
                frequency_mhz, frequency_min_mhz, frequency_max_mhz,
                ctx_switches, interrupts, soft_interrupts,
                load_avg_1m, load_avg_5m, load_avg_15m
            ) VALUES (
                %(metric_id)s, %(usage_percent)s, %(usage_per_core)s,
                %(frequency_mhz)s, %(frequency_min_mhz)s, %(frequency_max_mhz)s,
                %(ctx_switches)s, %(interrupts)s, %(soft_interrupts)s,
                %(load_avg_1m)s, %(load_avg_5m)s, %(load_avg_15m)s
            )
            """,
            {
                "metric_id": metric_id,
                "usage_percent": cpu.usage_percent,
                "usage_per_core": cpu.usage_per_core,
                "frequency_mhz": cpu.frequency_mhz,
                "frequency_min_mhz": cpu.frequency_min_mhz,
                "frequency_max_mhz": cpu.frequency_max_mhz,
                "ctx_switches": cpu.ctx_switches,
                "interrupts": cpu.interrupts,
                "soft_interrupts": cpu.soft_interrupts,
                "load_avg_1m": cpu.load_avg_1m,
                "load_avg_5m": cpu.load_avg_5m,
                "load_avg_15m": cpu.load_avg_15m,
            },
        )


def _insert_temperatures(
    conn: psycopg.Connection, metric_id: int, temps: list[TemperatureReading]
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rpi_monitor.temperature (metric_id, zone, celsius)
            VALUES (%(metric_id)s, %(zone)s, %(celsius)s)
            ON CONFLICT DO NOTHING
            """,
            [{"metric_id": metric_id, "zone": t.zone, "celsius": t.celsius} for t in temps],
        )


def _insert_memory(conn: psycopg.Connection, metric_id: int, mem: MemoryMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.memory (
                metric_id, total_bytes, available_bytes, used_bytes, free_bytes,
                cached_bytes, buffers_bytes, shared_bytes, usage_percent,
                swap_total_bytes, swap_used_bytes, swap_free_bytes, swap_usage_percent
            ) VALUES (
                %(metric_id)s, %(total_bytes)s, %(available_bytes)s, %(used_bytes)s,
                %(free_bytes)s, %(cached_bytes)s, %(buffers_bytes)s, %(shared_bytes)s,
                %(usage_percent)s, %(swap_total_bytes)s, %(swap_used_bytes)s,
                %(swap_free_bytes)s, %(swap_usage_percent)s
            )
            """,
            {"metric_id": metric_id, **mem.__dict__},
        )


def _insert_disks(
    conn: psycopg.Connection, metric_id: int, disks: list[DiskPartitionMetrics]
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rpi_monitor.disk (
                metric_id, device, mountpoint, fstype,
                total_bytes, used_bytes, free_bytes, usage_percent,
                read_bytes, write_bytes, read_count, write_count,
                read_time_ms, write_time_ms
            ) VALUES (
                %(metric_id)s, %(device)s, %(mountpoint)s, %(fstype)s,
                %(total_bytes)s, %(used_bytes)s, %(free_bytes)s, %(usage_percent)s,
                %(read_bytes)s, %(write_bytes)s, %(read_count)s, %(write_count)s,
                %(read_time_ms)s, %(write_time_ms)s
            )
            ON CONFLICT DO NOTHING
            """,
            [{"metric_id": metric_id, **d.__dict__} for d in disks],
        )


def _insert_network(
    conn: psycopg.Connection, metric_id: int, net: list[NetworkInterfaceMetrics]
) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rpi_monitor.network (
                metric_id, interface,
                bytes_sent, bytes_recv, packets_sent, packets_recv,
                errin, errout, dropin, dropout
            ) VALUES (
                %(metric_id)s, %(interface)s,
                %(bytes_sent)s, %(bytes_recv)s, %(packets_sent)s, %(packets_recv)s,
                %(errin)s, %(errout)s, %(dropin)s, %(dropout)s
            )
            ON CONFLICT DO NOTHING
            """,
            [{"metric_id": metric_id, **n.__dict__} for n in net],
        )


def _insert_system(conn: psycopg.Connection, metric_id: int, sys: SystemMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.system (
                metric_id, boot_time, uptime_seconds, process_count,
                process_running, process_sleeping, process_zombie, users_logged_in
            ) VALUES (
                %(metric_id)s, %(boot_time)s, %(uptime_seconds)s, %(process_count)s,
                %(process_running)s, %(process_sleeping)s, %(process_zombie)s,
                %(users_logged_in)s
            )
            """,
            {"metric_id": metric_id, **sys.__dict__},
        )


def _insert_throttle(
    conn: psycopg.Connection, metric_id: int, th: ThrottleMetrics
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.throttle (
                metric_id, raw_hex,
                under_voltage, freq_capped, currently_throttled, soft_temp_limit,
                under_voltage_occurred, freq_capped_occurred,
                throttled_occurred, soft_temp_occurred
            ) VALUES (
                %(metric_id)s, %(raw_hex)s,
                %(under_voltage)s, %(freq_capped)s, %(currently_throttled)s,
                %(soft_temp_limit)s, %(under_voltage_occurred)s,
                %(freq_capped_occurred)s, %(throttled_occurred)s, %(soft_temp_occurred)s
            )
            """,
            {"metric_id": metric_id, **th.__dict__},
        )


def persist_snapshot(snapshot: Snapshot, dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn:
        host_id = _upsert_host(conn, snapshot.hostname)
        metric_id = _insert_metric(conn, host_id, snapshot.collected_at)

        _insert_cpu(conn, metric_id, snapshot.cpu)
        _insert_temperatures(conn, metric_id, snapshot.temperatures)
        _insert_memory(conn, metric_id, snapshot.memory)
        _insert_disks(conn, metric_id, snapshot.disks)
        _insert_network(conn, metric_id, snapshot.network)
        _insert_system(conn, metric_id, snapshot.system)

        if snapshot.throttle:
            _insert_throttle(conn, metric_id, snapshot.throttle)

        conn.commit()
        log.info(
            "Snapshot salvo: metric_id=%d host=%s collected_at=%s",
            metric_id,
            snapshot.hostname,
            snapshot.collected_at.isoformat(),
        )
