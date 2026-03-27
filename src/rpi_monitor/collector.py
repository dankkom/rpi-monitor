#!/usr/bin/env python3
"""
rpi_monitor/collector.py  (v2)
Coleta todas as métricas disponíveis do Raspberry Pi 4.
"""

from __future__ import annotations

import json
import logging
import os
import re
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

TOP_N = int(os.getenv("RPIMON_TOP_N", "10"))   # quantos processos salvar no top


# ---------------------------------------------------------------------------
# Dataclasses
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
    syscalls: int
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
    slab_bytes: int
    usage_percent: float
    swap_total_bytes: int
    swap_used_bytes: int
    swap_free_bytes: int
    swap_usage_percent: float
    swap_sin: int
    swap_sout: int


@dataclass
class DiskMetrics:
    device: str
    mountpoint: str
    fstype: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    usage_percent: float
    inodes_total: int
    inodes_used: int
    inodes_free: int
    inodes_percent: float
    read_bytes: int
    write_bytes: int
    read_count: int
    write_count: int
    read_time_ms: int
    write_time_ms: int
    busy_time_ms: int


@dataclass
class SmartMetrics:
    device: str
    model: Optional[str]
    serial: Optional[str]
    firmware: Optional[str]
    capacity_bytes: Optional[int]
    smart_status: Optional[str]
    temperature_c: Optional[float]
    power_on_hours: Optional[int]
    power_cycle_count: Optional[int]
    reallocated_sectors: Optional[int]
    pending_sectors: Optional[int]
    uncorrectable_sectors: Optional[int]
    read_error_rate: Optional[int]
    seek_error_rate: Optional[int]
    spin_retry_count: Optional[int]
    udma_crc_errors: Optional[int]
    raw_json: Optional[dict]


@dataclass
class NetworkMetrics:
    interface: str
    is_up: bool
    speed_mbps: Optional[int]
    mtu: Optional[int]
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
    process_stopped: int
    process_zombie: int
    users_logged_in: int
    fd_open: int
    fd_max: int
    entropy_avail: int
    vm_pgfault: int
    vm_pgmajfault: int
    vm_swpins: int
    vm_swpouts: int


@dataclass
class TopProcess:
    rank_by: str
    rank: int
    pid: int
    name: str
    cpu_percent: float
    mem_percent: float
    mem_rss_bytes: int
    status: str
    num_threads: int
    username: str


@dataclass
class VcgencmdMetrics:
    clock_arm: Optional[int] = None
    clock_core: Optional[int] = None
    clock_h264: Optional[int] = None
    clock_isp: Optional[int] = None
    clock_v3d: Optional[int] = None
    clock_uart: Optional[int] = None
    clock_pwm: Optional[int] = None
    clock_emmc: Optional[int] = None
    clock_emmc2: Optional[int] = None
    clock_pixel: Optional[int] = None
    clock_vec: Optional[int] = None
    clock_hdmi: Optional[int] = None
    clock_dpi: Optional[int] = None
    volt_core: Optional[float] = None
    volt_sdram_c: Optional[float] = None
    volt_sdram_i: Optional[float] = None
    volt_sdram_p: Optional[float] = None
    mem_arm_bytes: Optional[int] = None
    mem_gpu_bytes: Optional[int] = None
    temp_celsius: Optional[float] = None


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
class UsbDevice:
    bus: str
    device_id: str
    vendor_id: Optional[str]
    product_id: Optional[str]
    manufacturer: Optional[str]
    product: Optional[str]
    speed: Optional[str]


@dataclass
class Snapshot:
    hostname: str
    collected_at: datetime
    cpu: CpuMetrics
    temperatures: list[TemperatureReading]
    memory: MemoryMetrics
    disks: list[DiskMetrics]
    smart: list[SmartMetrics]
    network: list[NetworkMetrics]
    tcp_connections: dict[str, int]
    system: SystemMetrics
    top_processes: list[TopProcess]
    vcgencmd: Optional[VcgencmdMetrics]
    throttle: Optional[ThrottleMetrics]
    usb_devices: list[UsbDevice]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 3.0) -> Optional[str]:
    """Executa um comando e retorna stdout, ou None em caso de falha."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _read_file(path: str | Path) -> Optional[str]:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Coleta
# ---------------------------------------------------------------------------

EXCLUDED_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "proc", "sysfs",
               "debugfs", "configfs", "fusectl", "cgroup", "cgroup2",
               "pstore", "bpf", "tracefs"}
EXCLUDED_MNT_PREFIX = ("/sys", "/proc", "/dev", "/run/user", "/snap")


def _collect_cpu() -> CpuMetrics:
    usage = psutil.cpu_percent(interval=0.5)
    per_core = psutil.cpu_percent(interval=None, percpu=True)
    freq = psutil.cpu_freq()
    stats = psutil.cpu_stats()
    load = psutil.getloadavg()
    return CpuMetrics(
        usage_percent=usage,
        usage_per_core=per_core,
        frequency_mhz=freq.current if freq else None,
        frequency_min_mhz=freq.min if freq else None,
        frequency_max_mhz=freq.max if freq else None,
        ctx_switches=stats.ctx_switches,
        interrupts=stats.interrupts,
        soft_interrupts=stats.soft_interrupts,
        syscalls=getattr(stats, "syscalls", 0),
        load_avg_1m=load[0],
        load_avg_5m=load[1],
        load_avg_15m=load[2],
    )


def _collect_temperatures() -> list[TemperatureReading]:
    readings: list[TemperatureReading] = []
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            for zone, entries in temps.items():
                for i, e in enumerate(entries):
                    label = e.label or f"{zone}_{i}"
                    readings.append(TemperatureReading(zone=label, celsius=e.current))
    except AttributeError:
        pass

    if not readings:
        for zdir in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
            try:
                zone_type = (zdir / "type").read_text().strip()
                celsius = int((zdir / "temp").read_text().strip()) / 1000.0
                readings.append(TemperatureReading(zone=zone_type, celsius=celsius))
            except (OSError, ValueError):
                pass
    return readings


def _read_slab_bytes() -> int:
    """Lê Slab: de /proc/meminfo."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("Slab:"):
                return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


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
        slab_bytes=_read_slab_bytes(),
        usage_percent=vm.percent,
        swap_total_bytes=sw.total,
        swap_used_bytes=sw.used,
        swap_free_bytes=sw.free,
        swap_usage_percent=sw.percent,
        swap_sin=sw.sin,
        swap_sout=sw.sout,
    )


def _collect_disks() -> list[DiskMetrics]:
    io_counters = psutil.disk_io_counters(perdisk=True)
    results: list[DiskMetrics] = []

    for part in psutil.disk_partitions(all=False):
        if part.fstype in EXCLUDED_FS:
            continue
        if any(part.mountpoint.startswith(p) for p in EXCLUDED_MNT_PREFIX):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue

        dev_name = part.device.split("/")[-1]
        io = io_counters.get(dev_name)

        # Tenta inodes via os.statvfs
        try:
            sv = os.statvfs(part.mountpoint)
            inodes_total = sv.f_files
            inodes_free = sv.f_ffree
            inodes_used = inodes_total - inodes_free
            inodes_pct = (inodes_used / inodes_total * 100.0) if inodes_total > 0 else 0.0
        except OSError:
            inodes_total = inodes_used = inodes_free = 0
            inodes_pct = 0.0

        results.append(DiskMetrics(
            device=part.device,
            mountpoint=part.mountpoint,
            fstype=part.fstype,
            total_bytes=usage.total,
            used_bytes=usage.used,
            free_bytes=usage.free,
            usage_percent=usage.percent,
            inodes_total=inodes_total,
            inodes_used=inodes_used,
            inodes_free=inodes_free,
            inodes_percent=round(inodes_pct, 2),
            read_bytes=io.read_bytes if io else 0,
            write_bytes=io.write_bytes if io else 0,
            read_count=io.read_count if io else 0,
            write_count=io.write_count if io else 0,
            read_time_ms=io.read_time if io else 0,
            write_time_ms=io.write_time if io else 0,
            busy_time_ms=getattr(io, "busy_time", 0) if io else 0,
        ))
    return results


def _parse_smart_attr(attrs: list[dict], attr_id: int) -> Optional[int]:
    for a in attrs:
        if a.get("id") == attr_id:
            raw = a.get("raw", {})
            if isinstance(raw, dict):
                return raw.get("value")
            return None
    return None


def _collect_smart() -> list[SmartMetrics]:
    """
    Usa smartctl --scan + smartctl -x --json para cada dispositivo.
    Requer: sudo apt install smartmontools
    O processo precisa ter permissão; configure sudoers ou use setuid.
    """
    scan_out = _run(["smartctl", "--scan-open", "--json"], timeout=10)
    if not scan_out:
        return []

    try:
        scan_data = json.loads(scan_out)
    except json.JSONDecodeError:
        return []

    devices = [d.get("name") for d in scan_data.get("devices", []) if d.get("name")]
    results: list[SmartMetrics] = []

    for dev in devices:
        raw_out = _run(["smartctl", "-x", "--json", dev], timeout=15)
        if not raw_out:
            continue
        try:
            d = json.loads(raw_out)
        except json.JSONDecodeError:
            continue

        # Temperatura
        temp = None
        t_obj = d.get("temperature", {})
        if isinstance(t_obj, dict):
            temp = t_obj.get("current")

        # Status
        smart_status = None
        status_obj = d.get("smart_status", {})
        if isinstance(status_obj, dict):
            passed = status_obj.get("passed")
            if passed is True:
                smart_status = "PASSED"
            elif passed is False:
                smart_status = "FAILED"

        # Atributos ATA (HDDs/SSDs SATA)
        attrs = d.get("ata_smart_attributes", {}).get("table", [])

        # Power on hours — pode estar em ata_smart_attributes ou diretamente
        poh = d.get("power_on_time", {}).get("hours")
        if poh is None:
            poh = _parse_smart_attr(attrs, 9)

        pcc = d.get("power_cycle_count")
        if pcc is None:
            pcc = _parse_smart_attr(attrs, 12)

        # Capacidade
        cap = None
        user_cap = d.get("user_capacity", {})
        if isinstance(user_cap, dict):
            cap = user_cap.get("bytes")

        results.append(SmartMetrics(
            device=dev,
            model=d.get("model_name") or d.get("model_family"),
            serial=d.get("serial_number"),
            firmware=d.get("firmware_version"),
            capacity_bytes=cap,
            smart_status=smart_status,
            temperature_c=float(temp) if temp is not None else None,
            power_on_hours=poh,
            power_cycle_count=pcc,
            reallocated_sectors=_parse_smart_attr(attrs, 5),
            pending_sectors=_parse_smart_attr(attrs, 197),
            uncorrectable_sectors=_parse_smart_attr(attrs, 198),
            read_error_rate=_parse_smart_attr(attrs, 1),
            seek_error_rate=_parse_smart_attr(attrs, 7),
            spin_retry_count=_parse_smart_attr(attrs, 10),
            udma_crc_errors=_parse_smart_attr(attrs, 199),
            raw_json=d,
        ))

    return results


def _collect_network() -> list[NetworkMetrics]:
    io = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()
    results: list[NetworkMetrics] = []
    for iface, counters in io.items():
        if iface == "lo":
            continue
        st = stats.get(iface)
        results.append(NetworkMetrics(
            interface=iface,
            is_up=st.isup if st else False,
            speed_mbps=st.speed if st else None,
            mtu=st.mtu if st else None,
            bytes_sent=counters.bytes_sent,
            bytes_recv=counters.bytes_recv,
            packets_sent=counters.packets_sent,
            packets_recv=counters.packets_recv,
            errin=counters.errin,
            errout=counters.errout,
            dropin=counters.dropin,
            dropout=counters.dropout,
        ))
    return results


def _collect_tcp_connections() -> dict[str, int]:
    counts: dict[str, int] = {}
    try:
        for conn in psutil.net_connections(kind="tcp"):
            state = conn.status or "UNKNOWN"
            counts[state] = counts.get(state, 0) + 1
    except psutil.AccessDenied:
        log.debug("Sem permissão para net_connections — pulando TCP stats")
    return counts


def _read_vmstat() -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        for line in Path("/proc/vmstat").read_text().splitlines():
            parts = line.split()
            if len(parts) == 2:
                result[parts[0]] = int(parts[1])
    except OSError:
        pass
    return result


def _collect_system() -> SystemMetrics:
    boot_ts = psutil.boot_time()
    boot_dt = datetime.fromtimestamp(boot_ts, tz=timezone.utc)
    uptime = int((datetime.now(tz=timezone.utc) - boot_dt).total_seconds())

    status_counts: dict[str, int] = {}
    procs = list(psutil.process_iter(["status"]))
    for p in procs:
        try:
            s = p.info["status"]
            status_counts[s] = status_counts.get(s, 0) + 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    # File descriptors
    fd_open = fd_max = 0
    fd_raw = _read_file("/proc/sys/fs/file-nr")
    if fd_raw:
        parts = fd_raw.split()
        if len(parts) >= 3:
            fd_open, fd_max = int(parts[0]), int(parts[2])

    # Entropy
    entropy = 0
    ent_raw = _read_file("/proc/sys/kernel/random/entropy_avail")
    if ent_raw:
        try:
            entropy = int(ent_raw)
        except ValueError:
            pass

    vmstat = _read_vmstat()

    return SystemMetrics(
        boot_time=boot_dt,
        uptime_seconds=uptime,
        process_count=len(procs),
        process_running=status_counts.get(psutil.STATUS_RUNNING, 0),
        process_sleeping=status_counts.get(psutil.STATUS_SLEEPING, 0),
        process_stopped=status_counts.get(psutil.STATUS_STOPPED, 0),
        process_zombie=status_counts.get(psutil.STATUS_ZOMBIE, 0),
        users_logged_in=len(psutil.users()),
        fd_open=fd_open,
        fd_max=fd_max,
        entropy_avail=entropy,
        vm_pgfault=vmstat.get("pgfault", 0),
        vm_pgmajfault=vmstat.get("pgmajfault", 0),
        vm_swpins=vmstat.get("pswpin", 0),
        vm_swpouts=vmstat.get("pswpout", 0),
    )


def _collect_top_processes() -> list[TopProcess]:
    procs: list[dict] = []
    for p in psutil.process_iter(
        ["pid", "name", "cpu_percent", "memory_percent",
         "memory_info", "status", "num_threads", "username"]
    ):
        try:
            info = p.info
            if info["cpu_percent"] is None:
                info["cpu_percent"] = 0.0
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    results: list[TopProcess] = []

    # Top por CPU
    by_cpu = sorted(procs, key=lambda x: x["cpu_percent"] or 0, reverse=True)[:TOP_N]
    for i, p in enumerate(by_cpu, 1):
        results.append(TopProcess(
            rank_by="cpu", rank=i,
            pid=p["pid"], name=p["name"] or "",
            cpu_percent=p["cpu_percent"] or 0.0,
            mem_percent=round(p["memory_percent"] or 0.0, 3),
            mem_rss_bytes=(p["memory_info"].rss if p["memory_info"] else 0),
            status=p["status"] or "",
            num_threads=p["num_threads"] or 0,
            username=p["username"] or "",
        ))

    # Top por memória
    by_mem = sorted(procs, key=lambda x: x["memory_percent"] or 0, reverse=True)[:TOP_N]
    for i, p in enumerate(by_mem, 1):
        results.append(TopProcess(
            rank_by="memory", rank=i,
            pid=p["pid"], name=p["name"] or "",
            cpu_percent=p["cpu_percent"] or 0.0,
            mem_percent=round(p["memory_percent"] or 0.0, 3),
            mem_rss_bytes=(p["memory_info"].rss if p["memory_info"] else 0),
            status=p["status"] or "",
            num_threads=p["num_threads"] or 0,
            username=p["username"] or "",
        ))

    return results


def _vcgencmd_clock(source: str) -> Optional[int]:
    out = _run(["vcgencmd", "measure_clock", source])
    if out:
        m = re.search(r"=(\d+)", out)
        if m:
            return int(m.group(1))
    return None


def _vcgencmd_volt(source: str) -> Optional[float]:
    out = _run(["vcgencmd", "measure_volts", source])
    if out:
        m = re.search(r"=([\d.]+)V", out)
        if m:
            return float(m.group(1))
    return None


def _vcgencmd_mem(part: str) -> Optional[int]:
    """Retorna bytes (converte de MiB)."""
    out = _run(["vcgencmd", "get_mem", part])
    if out:
        m = re.search(r"=(\d+)M", out)
        if m:
            return int(m.group(1)) * 1024 * 1024
    return None


def _vcgencmd_temp() -> Optional[float]:
    out = _run(["vcgencmd", "measure_temp"])
    if out:
        m = re.search(r"=([\d.]+)", out)
        if m:
            return float(m.group(1))
    return None


def _collect_vcgencmd() -> Optional[VcgencmdMetrics]:
    # Testa se vcgencmd está disponível
    if _run(["vcgencmd", "version"]) is None:
        return None

    CLOCKS = ["arm", "core", "h264", "isp", "v3d", "uart", "pwm",
              "emmc", "emmc2", "pixel", "vec", "hdmi", "dpi"]
    VOLTS = ["core", "sdram_c", "sdram_i", "sdram_p"]

    vc = VcgencmdMetrics()
    for clk in CLOCKS:
        setattr(vc, f"clock_{clk}", _vcgencmd_clock(clk))
    for v in VOLTS:
        setattr(vc, f"volt_{v.replace('sdram_', 'sdram_')}", _vcgencmd_volt(v))

    # Normaliza os nomes dos atributos de voltagem
    vc.volt_core    = _vcgencmd_volt("core")
    vc.volt_sdram_c = _vcgencmd_volt("sdram_c")
    vc.volt_sdram_i = _vcgencmd_volt("sdram_i")
    vc.volt_sdram_p = _vcgencmd_volt("sdram_p")

    vc.mem_arm_bytes = _vcgencmd_mem("arm")
    vc.mem_gpu_bytes = _vcgencmd_mem("gpu")
    vc.temp_celsius  = _vcgencmd_temp()

    return vc


def _parse_throttle(raw_hex: str) -> ThrottleMetrics:
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
    out = _run(["vcgencmd", "get_throttled"])
    if out:
        try:
            raw = out.split("=")[1]
            return _parse_throttle(raw)
        except (IndexError, ValueError):
            pass
    return None


def _collect_usb_devices() -> list[UsbDevice]:
    """Lê dispositivos USB de /sys/bus/usb/devices/."""
    results: list[UsbDevice] = []
    base = Path("/sys/bus/usb/devices")
    if not base.exists():
        return results

    def _read(p: Path) -> Optional[str]:
        try:
            return p.read_text().strip()
        except OSError:
            return None

    for dev_dir in sorted(base.iterdir()):
        # Ignora entradas sem idVendor (são hubs internos / raízes)
        vendor_id = _read(dev_dir / "idVendor")
        if not vendor_id:
            continue
        product_id = _read(dev_dir / "idProduct")
        bus = _read(dev_dir / "busnum") or dev_dir.name
        dev_id = _read(dev_dir / "devnum") or dev_dir.name

        results.append(UsbDevice(
            bus=bus,
            device_id=dev_id,
            vendor_id=vendor_id,
            product_id=product_id,
            manufacturer=_read(dev_dir / "manufacturer"),
            product=_read(dev_dir / "product"),
            speed=_read(dev_dir / "speed"),
        ))
    return results


def collect_snapshot() -> Snapshot:
    return Snapshot(
        hostname=socket.gethostname(),
        collected_at=datetime.now(tz=timezone.utc),
        cpu=_collect_cpu(),
        temperatures=_collect_temperatures(),
        memory=_collect_memory(),
        disks=_collect_disks(),
        smart=_collect_smart(),
        network=_collect_network(),
        tcp_connections=_collect_tcp_connections(),
        system=_collect_system(),
        top_processes=_collect_top_processes(),
        vcgencmd=_collect_vcgencmd(),
        throttle=_collect_throttle(),
        usb_devices=_collect_usb_devices(),
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
        return cur.fetchone()["id"]


def _insert_metric(conn: psycopg.Connection, host_id: int, ts: datetime) -> int:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "INSERT INTO rpi_monitor.metrics (host_id, collected_at) VALUES (%s, %s) RETURNING id",
            (host_id, ts),
        )
        return cur.fetchone()["id"]


def _insert_cpu(conn: psycopg.Connection, mid: int, c: CpuMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.cpu VALUES (
                %(metric_id)s, %(usage_percent)s, %(usage_per_core)s,
                %(frequency_mhz)s, %(frequency_min_mhz)s, %(frequency_max_mhz)s,
                %(ctx_switches)s, %(interrupts)s, %(soft_interrupts)s, %(syscalls)s,
                %(load_avg_1m)s, %(load_avg_5m)s, %(load_avg_15m)s
            )
            """,
            {"metric_id": mid, **c.__dict__},
        )


def _insert_temperatures(conn: psycopg.Connection, mid: int, temps: list[TemperatureReading]) -> None:
    if not temps:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO rpi_monitor.temperature VALUES (%(metric_id)s, %(zone)s, %(celsius)s) ON CONFLICT DO NOTHING",
            [{"metric_id": mid, **t.__dict__} for t in temps],
        )


def _insert_memory(conn: psycopg.Connection, mid: int, m: MemoryMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.memory VALUES (
                %(metric_id)s, %(total_bytes)s, %(available_bytes)s, %(used_bytes)s,
                %(free_bytes)s, %(cached_bytes)s, %(buffers_bytes)s, %(shared_bytes)s,
                %(slab_bytes)s, %(usage_percent)s, %(swap_total_bytes)s, %(swap_used_bytes)s,
                %(swap_free_bytes)s, %(swap_usage_percent)s, %(swap_sin)s, %(swap_sout)s
            )
            """,
            {"metric_id": mid, **m.__dict__},
        )


def _insert_disks(conn: psycopg.Connection, mid: int, disks: list[DiskMetrics]) -> None:
    if not disks:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rpi_monitor.disk VALUES (
                %(metric_id)s, %(device)s, %(mountpoint)s, %(fstype)s,
                %(total_bytes)s, %(used_bytes)s, %(free_bytes)s, %(usage_percent)s,
                %(inodes_total)s, %(inodes_used)s, %(inodes_free)s, %(inodes_percent)s,
                %(read_bytes)s, %(write_bytes)s, %(read_count)s, %(write_count)s,
                %(read_time_ms)s, %(write_time_ms)s, %(busy_time_ms)s
            ) ON CONFLICT DO NOTHING
            """,
            [{"metric_id": mid, **d.__dict__} for d in disks],
        )


def _insert_smart(conn: psycopg.Connection, mid: int, smart_list: list[SmartMetrics]) -> None:
    if not smart_list:
        return
    with conn.cursor() as cur:
        for s in smart_list:
            cur.execute(
                """
                INSERT INTO rpi_monitor.smart VALUES (
                    %(metric_id)s, %(device)s, %(model)s, %(serial)s, %(firmware)s,
                    %(capacity_bytes)s, %(smart_status)s, %(temperature_c)s,
                    %(power_on_hours)s, %(power_cycle_count)s, %(reallocated_sectors)s,
                    %(pending_sectors)s, %(uncorrectable_sectors)s, %(read_error_rate)s,
                    %(seek_error_rate)s, %(spin_retry_count)s, %(udma_crc_errors)s,
                    %(raw_json)s
                ) ON CONFLICT DO NOTHING
                """,
                {
                    "metric_id": mid,
                    **{k: v for k, v in s.__dict__.items() if k != "raw_json"},
                    "raw_json": json.dumps(s.raw_json) if s.raw_json else None,
                },
            )


def _insert_network(conn: psycopg.Connection, mid: int, nets: list[NetworkMetrics]) -> None:
    if not nets:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rpi_monitor.network VALUES (
                %(metric_id)s, %(interface)s, %(is_up)s, %(speed_mbps)s, %(mtu)s,
                %(bytes_sent)s, %(bytes_recv)s, %(packets_sent)s, %(packets_recv)s,
                %(errin)s, %(errout)s, %(dropin)s, %(dropout)s
            ) ON CONFLICT DO NOTHING
            """,
            [{"metric_id": mid, **n.__dict__} for n in nets],
        )


def _insert_tcp(conn: psycopg.Connection, mid: int, tcp: dict[str, int]) -> None:
    if not tcp:
        return
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO rpi_monitor.tcp_connections VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            [(mid, state, count) for state, count in tcp.items()],
        )


def _insert_system(conn: psycopg.Connection, mid: int, s: SystemMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.system VALUES (
                %(metric_id)s, %(boot_time)s, %(uptime_seconds)s, %(process_count)s,
                %(process_running)s, %(process_sleeping)s, %(process_stopped)s,
                %(process_zombie)s, %(users_logged_in)s,
                %(fd_open)s, %(fd_max)s, %(entropy_avail)s,
                %(vm_pgfault)s, %(vm_pgmajfault)s, %(vm_swpins)s, %(vm_swpouts)s
            )
            """,
            {"metric_id": mid, **s.__dict__},
        )


def _insert_top_processes(conn: psycopg.Connection, mid: int, procs: list[TopProcess]) -> None:
    if not procs:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rpi_monitor.top_processes VALUES (
                %(metric_id)s, %(rank_by)s, %(rank)s, %(pid)s, %(name)s,
                %(cpu_percent)s, %(mem_percent)s, %(mem_rss_bytes)s,
                %(status)s, %(num_threads)s, %(username)s
            ) ON CONFLICT DO NOTHING
            """,
            [{"metric_id": mid, **p.__dict__} for p in procs],
        )


def _insert_vcgencmd(conn: psycopg.Connection, mid: int, vc: VcgencmdMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.vcgencmd VALUES (
                %(metric_id)s,
                %(clock_arm)s, %(clock_core)s, %(clock_h264)s, %(clock_isp)s,
                %(clock_v3d)s, %(clock_uart)s, %(clock_pwm)s, %(clock_emmc)s,
                %(clock_emmc2)s, %(clock_pixel)s, %(clock_vec)s, %(clock_hdmi)s,
                %(clock_dpi)s,
                %(volt_core)s, %(volt_sdram_c)s, %(volt_sdram_i)s, %(volt_sdram_p)s,
                %(mem_arm_bytes)s, %(mem_gpu_bytes)s, %(temp_celsius)s
            )
            """,
            {"metric_id": mid, **vc.__dict__},
        )


def _insert_throttle(conn: psycopg.Connection, mid: int, th: ThrottleMetrics) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO rpi_monitor.throttle VALUES (
                %(metric_id)s, %(raw_hex)s,
                %(under_voltage)s, %(freq_capped)s, %(currently_throttled)s,
                %(soft_temp_limit)s, %(under_voltage_occurred)s,
                %(freq_capped_occurred)s, %(throttled_occurred)s, %(soft_temp_occurred)s
            )
            """,
            {"metric_id": mid, **th.__dict__},
        )


def _insert_usb(conn: psycopg.Connection, mid: int, devices: list[UsbDevice]) -> None:
    if not devices:
        return
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO rpi_monitor.usb_devices VALUES (
                %(metric_id)s, %(bus)s, %(device_id)s, %(vendor_id)s, %(product_id)s,
                %(manufacturer)s, %(product)s, %(speed)s
            ) ON CONFLICT DO NOTHING
            """,
            [{"metric_id": mid, **u.__dict__} for u in devices],
        )


def persist_snapshot(snapshot: Snapshot, dsn: str) -> None:
    with psycopg.connect(dsn, autocommit=False) as conn:
        host_id = _upsert_host(conn, snapshot.hostname)
        mid = _insert_metric(conn, host_id, snapshot.collected_at)

        _insert_cpu(conn, mid, snapshot.cpu)
        _insert_temperatures(conn, mid, snapshot.temperatures)
        _insert_memory(conn, mid, snapshot.memory)
        _insert_disks(conn, mid, snapshot.disks)
        _insert_smart(conn, mid, snapshot.smart)
        _insert_network(conn, mid, snapshot.network)
        _insert_tcp(conn, mid, snapshot.tcp_connections)
        _insert_system(conn, mid, snapshot.system)
        _insert_top_processes(conn, mid, snapshot.top_processes)

        if snapshot.vcgencmd:
            _insert_vcgencmd(conn, mid, snapshot.vcgencmd)
        if snapshot.throttle:
            _insert_throttle(conn, mid, snapshot.throttle)

        _insert_usb(conn, mid, snapshot.usb_devices)

        conn.commit()
        log.info(
            "Snapshot salvo: metric_id=%d host=%s at=%s smart=%d usb=%d",
            mid, snapshot.hostname,
            snapshot.collected_at.isoformat(),
            len(snapshot.smart),
            len(snapshot.usb_devices),
        )
