-- =============================================================================
-- Schema: rpi_monitor  (v2 — expandido)
-- Monitoramento abrangente do Raspberry Pi 4 + discos externos
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS rpi_monitor;

-- ---------------------------------------------------------------------------
-- Hosts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.hosts (
    id          SERIAL PRIMARY KEY,
    hostname    TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Coleta raiz
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.metrics (
    id           BIGSERIAL PRIMARY KEY,
    host_id      INTEGER NOT NULL REFERENCES rpi_monitor.hosts(id),
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- CPU
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.cpu (
    metric_id           BIGINT PRIMARY KEY REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    usage_percent       NUMERIC(5,2),
    usage_per_core      NUMERIC(5,2)[],
    frequency_mhz       NUMERIC(8,2),
    frequency_min_mhz   NUMERIC(8,2),
    frequency_max_mhz   NUMERIC(8,2),
    ctx_switches        BIGINT,
    interrupts          BIGINT,
    soft_interrupts     BIGINT,
    syscalls            BIGINT,
    load_avg_1m         NUMERIC(6,3),
    load_avg_5m         NUMERIC(6,3),
    load_avg_15m        NUMERIC(6,3)
);

-- ---------------------------------------------------------------------------
-- Temperatura (todas as thermal zones + fontes externas)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.temperature (
    metric_id   BIGINT REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    zone        TEXT NOT NULL,
    celsius     NUMERIC(6,3),
    PRIMARY KEY (metric_id, zone)
);

-- ---------------------------------------------------------------------------
-- Memória RAM + swap
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.memory (
    metric_id           BIGINT PRIMARY KEY REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    total_bytes         BIGINT,
    available_bytes     BIGINT,
    used_bytes          BIGINT,
    free_bytes          BIGINT,
    cached_bytes        BIGINT,
    buffers_bytes       BIGINT,
    shared_bytes        BIGINT,
    slab_bytes          BIGINT,
    usage_percent       NUMERIC(5,2),
    swap_total_bytes    BIGINT,
    swap_used_bytes     BIGINT,
    swap_free_bytes     BIGINT,
    swap_usage_percent  NUMERIC(5,2),
    swap_sin            BIGINT,
    swap_sout           BIGINT
);

-- ---------------------------------------------------------------------------
-- Disco — uso de partição
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.disk (
    metric_id           BIGINT REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    device              TEXT NOT NULL,
    mountpoint          TEXT NOT NULL,
    fstype              TEXT,
    total_bytes         BIGINT,
    used_bytes          BIGINT,
    free_bytes          BIGINT,
    usage_percent       NUMERIC(5,2),
    inodes_total        BIGINT,
    inodes_used         BIGINT,
    inodes_free         BIGINT,
    inodes_percent      NUMERIC(5,2),
    read_bytes          BIGINT,
    write_bytes         BIGINT,
    read_count          BIGINT,
    write_count         BIGINT,
    read_time_ms        BIGINT,
    write_time_ms       BIGINT,
    busy_time_ms        BIGINT,
    PRIMARY KEY (metric_id, mountpoint)
);

-- ---------------------------------------------------------------------------
-- S.M.A.R.T. — saúde dos discos (HD externo, SSD, SD card via USB)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.smart (
    metric_id               BIGINT REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    device                  TEXT NOT NULL,
    model                   TEXT,
    serial                  TEXT,
    firmware                TEXT,
    capacity_bytes          BIGINT,
    smart_status            TEXT,
    temperature_c           NUMERIC(6,2),
    power_on_hours          BIGINT,
    power_cycle_count       BIGINT,
    reallocated_sectors     INTEGER,
    pending_sectors         INTEGER,
    uncorrectable_sectors   INTEGER,
    read_error_rate         BIGINT,
    seek_error_rate         BIGINT,
    spin_retry_count        INTEGER,
    udma_crc_errors         INTEGER,
    raw_json                JSONB,
    PRIMARY KEY (metric_id, device)
);

-- ---------------------------------------------------------------------------
-- Rede — I/O por interface
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.network (
    metric_id       BIGINT REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    interface       TEXT NOT NULL,
    is_up           BOOLEAN,
    speed_mbps      INTEGER,
    mtu             INTEGER,
    bytes_sent      BIGINT,
    bytes_recv      BIGINT,
    packets_sent    BIGINT,
    packets_recv    BIGINT,
    errin           BIGINT,
    errout          BIGINT,
    dropin          BIGINT,
    dropout         BIGINT,
    PRIMARY KEY (metric_id, interface)
);

-- ---------------------------------------------------------------------------
-- Conexões TCP por estado
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.tcp_connections (
    metric_id       BIGINT REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    state           TEXT NOT NULL,
    count           INTEGER,
    PRIMARY KEY (metric_id, state)
);

-- ---------------------------------------------------------------------------
-- Sistema geral
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.system (
    metric_id               BIGINT PRIMARY KEY REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    boot_time               TIMESTAMPTZ,
    uptime_seconds          BIGINT,
    process_count           INTEGER,
    process_running         INTEGER,
    process_sleeping        INTEGER,
    process_stopped         INTEGER,
    process_zombie          INTEGER,
    users_logged_in         INTEGER,
    fd_open                 BIGINT,
    fd_max                  BIGINT,
    entropy_avail           INTEGER,
    vm_pgfault              BIGINT,
    vm_pgmajfault           BIGINT,
    vm_swpins               BIGINT,
    vm_swpouts              BIGINT
);

-- ---------------------------------------------------------------------------
-- Top-N processos por CPU e memória
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.top_processes (
    metric_id       BIGINT REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    rank_by         TEXT NOT NULL,
    rank            SMALLINT NOT NULL,
    pid             INTEGER,
    name            TEXT,
    cpu_percent     NUMERIC(6,2),
    mem_percent     NUMERIC(5,2),
    mem_rss_bytes   BIGINT,
    status          TEXT,
    num_threads     INTEGER,
    username        TEXT,
    PRIMARY KEY (metric_id, rank_by, rank)
);

-- ---------------------------------------------------------------------------
-- GPU e clocks do VideoCore (via vcgencmd)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.vcgencmd (
    metric_id           BIGINT PRIMARY KEY REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    clock_arm           BIGINT,
    clock_core          BIGINT,
    clock_h264          BIGINT,
    clock_isp           BIGINT,
    clock_v3d           BIGINT,
    clock_uart          BIGINT,
    clock_pwm           BIGINT,
    clock_emmc          BIGINT,
    clock_emmc2         BIGINT,
    clock_pixel         BIGINT,
    clock_vec           BIGINT,
    clock_hdmi          BIGINT,
    clock_dpi           BIGINT,
    volt_core           NUMERIC(7,4),
    volt_sdram_c        NUMERIC(7,4),
    volt_sdram_i        NUMERIC(7,4),
    volt_sdram_p        NUMERIC(7,4),
    mem_arm_bytes       BIGINT,
    mem_gpu_bytes       BIGINT,
    temp_celsius        NUMERIC(6,3)
);

-- ---------------------------------------------------------------------------
-- Throttling do RPi
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.throttle (
    metric_id               BIGINT PRIMARY KEY REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    raw_hex                 TEXT,
    under_voltage           BOOLEAN,
    freq_capped             BOOLEAN,
    currently_throttled     BOOLEAN,
    soft_temp_limit         BOOLEAN,
    under_voltage_occurred  BOOLEAN,
    freq_capped_occurred    BOOLEAN,
    throttled_occurred      BOOLEAN,
    soft_temp_occurred      BOOLEAN
);

-- ---------------------------------------------------------------------------
-- Dispositivos USB conectados
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rpi_monitor.usb_devices (
    metric_id       BIGINT REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    bus             TEXT NOT NULL,
    device_id       TEXT NOT NULL,
    vendor_id       TEXT,
    product_id      TEXT,
    manufacturer    TEXT,
    product         TEXT,
    speed           TEXT,
    PRIMARY KEY (metric_id, bus, device_id)
);

-- ---------------------------------------------------------------------------
-- Índices
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_metrics_host_collected
    ON rpi_monitor.metrics (host_id, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_metrics_collected
    ON rpi_monitor.metrics (collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_smart_device
    ON rpi_monitor.smart (device, metric_id DESC);

-- ---------------------------------------------------------------------------
-- View de resumo rápido
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW rpi_monitor.v_latest AS
SELECT
    m.collected_at,
    h.hostname,
    c.usage_percent         AS cpu_percent,
    c.load_avg_1m,
    c.frequency_mhz         AS cpu_freq_mhz,
    ROUND((vc.clock_arm / 1e6)::numeric, 1) AS cpu_arm_mhz,
    t.celsius               AS cpu_temp_c,
    vc.temp_celsius         AS gpu_temp_c,
    vc.volt_core            AS core_volt_v,
    mem.usage_percent       AS mem_percent,
    ROUND(mem.used_bytes  / 1024.0^3, 2) AS mem_used_gb,
    ROUND(mem.total_bytes / 1024.0^3, 2) AS mem_total_gb,
    vc.mem_gpu_bytes / 1024^2            AS gpu_mem_mb,
    th.under_voltage,
    th.currently_throttled,
    sys.uptime_seconds,
    sys.process_count,
    sys.entropy_avail,
    sys.fd_open
FROM rpi_monitor.metrics m
JOIN  rpi_monitor.hosts h       ON h.id = m.host_id
LEFT JOIN rpi_monitor.cpu c     ON c.metric_id = m.id
LEFT JOIN rpi_monitor.temperature t
    ON t.metric_id = m.id AND t.zone = 'cpu-thermal'
LEFT JOIN rpi_monitor.memory mem    ON mem.metric_id = m.id
LEFT JOIN rpi_monitor.vcgencmd vc   ON vc.metric_id = m.id
LEFT JOIN rpi_monitor.throttle th   ON th.metric_id = m.id
LEFT JOIN rpi_monitor.system sys    ON sys.metric_id = m.id
ORDER BY m.collected_at DESC;
