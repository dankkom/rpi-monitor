-- =============================================================================
-- Schema: rpi_monitor
-- Monitoramento de métricas do Raspberry Pi 4
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS rpi_monitor;

-- Metadados do host (suporta múltiplos Pis)
CREATE TABLE IF NOT EXISTS rpi_monitor.hosts (
    id          SERIAL PRIMARY KEY,
    hostname    TEXT NOT NULL UNIQUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tabela principal de coletas
CREATE TABLE IF NOT EXISTS rpi_monitor.metrics (
    id          BIGSERIAL PRIMARY KEY,
    host_id     INTEGER NOT NULL REFERENCES rpi_monitor.hosts(id),
    collected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- CPU
CREATE TABLE IF NOT EXISTS rpi_monitor.cpu (
    metric_id           BIGINT NOT NULL REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    usage_percent       NUMERIC(5,2),        -- uso total (%)
    usage_per_core      NUMERIC(5,2)[],      -- uso por núcleo (%)
    frequency_mhz       NUMERIC(8,2),        -- frequência atual
    frequency_min_mhz   NUMERIC(8,2),
    frequency_max_mhz   NUMERIC(8,2),
    ctx_switches        BIGINT,              -- trocas de contexto acumuladas
    interrupts          BIGINT,
    soft_interrupts     BIGINT,
    load_avg_1m         NUMERIC(6,3),
    load_avg_5m         NUMERIC(6,3),
    load_avg_15m        NUMERIC(6,3),
    PRIMARY KEY (metric_id)
);

-- Temperatura (thermal zones)
CREATE TABLE IF NOT EXISTS rpi_monitor.temperature (
    metric_id   BIGINT NOT NULL REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    zone        TEXT NOT NULL,               -- ex: "cpu-thermal", "gpu"
    celsius     NUMERIC(6,3),
    PRIMARY KEY (metric_id, zone)
);

-- Memória RAM
CREATE TABLE IF NOT EXISTS rpi_monitor.memory (
    metric_id           BIGINT NOT NULL REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    total_bytes         BIGINT,
    available_bytes     BIGINT,
    used_bytes          BIGINT,
    free_bytes          BIGINT,
    cached_bytes        BIGINT,
    buffers_bytes       BIGINT,
    shared_bytes        BIGINT,
    usage_percent       NUMERIC(5,2),
    swap_total_bytes    BIGINT,
    swap_used_bytes     BIGINT,
    swap_free_bytes     BIGINT,
    swap_usage_percent  NUMERIC(5,2),
    PRIMARY KEY (metric_id)
);

-- Disco (por partição)
CREATE TABLE IF NOT EXISTS rpi_monitor.disk (
    metric_id           BIGINT NOT NULL REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    device              TEXT NOT NULL,       -- ex: "/dev/mmcblk0p2"
    mountpoint          TEXT NOT NULL,
    fstype              TEXT,
    total_bytes         BIGINT,
    used_bytes          BIGINT,
    free_bytes          BIGINT,
    usage_percent       NUMERIC(5,2),
    read_bytes          BIGINT,              -- acumulado desde boot
    write_bytes         BIGINT,
    read_count          BIGINT,
    write_count         BIGINT,
    read_time_ms        BIGINT,
    write_time_ms       BIGINT,
    PRIMARY KEY (metric_id, mountpoint)
);

-- Rede (por interface)
CREATE TABLE IF NOT EXISTS rpi_monitor.network (
    metric_id           BIGINT NOT NULL REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    interface           TEXT NOT NULL,       -- ex: "eth0", "wlan0"
    bytes_sent          BIGINT,              -- acumulado desde boot
    bytes_recv          BIGINT,
    packets_sent        BIGINT,
    packets_recv        BIGINT,
    errin               BIGINT,
    errout              BIGINT,
    dropin              BIGINT,
    dropout             BIGINT,
    PRIMARY KEY (metric_id, interface)
);

-- Sistema geral
CREATE TABLE IF NOT EXISTS rpi_monitor.system (
    metric_id           BIGINT NOT NULL REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    boot_time           TIMESTAMPTZ,
    uptime_seconds      BIGINT,
    process_count       INTEGER,
    process_running     INTEGER,
    process_sleeping    INTEGER,
    process_zombie      INTEGER,
    users_logged_in     INTEGER,
    PRIMARY KEY (metric_id)
);

-- Throttling do Raspberry Pi (flags de under-voltage / throttle)
-- Lido via vcgencmd get_throttled (retorna bitmask hex)
CREATE TABLE IF NOT EXISTS rpi_monitor.throttle (
    metric_id               BIGINT NOT NULL REFERENCES rpi_monitor.metrics(id) ON DELETE CASCADE,
    raw_hex                 TEXT,            -- valor bruto, ex: "0x50000"
    under_voltage           BOOLEAN,         -- bit 0
    freq_capped             BOOLEAN,         -- bit 1
    currently_throttled     BOOLEAN,         -- bit 2
    soft_temp_limit         BOOLEAN,         -- bit 3
    under_voltage_occurred  BOOLEAN,         -- bit 16
    freq_capped_occurred    BOOLEAN,         -- bit 17
    throttled_occurred      BOOLEAN,         -- bit 18
    soft_temp_occurred      BOOLEAN,         -- bit 19
    PRIMARY KEY (metric_id)
);

-- Índices para queries por tempo e host
CREATE INDEX IF NOT EXISTS idx_metrics_host_collected
    ON rpi_monitor.metrics (host_id, collected_at DESC);

CREATE INDEX IF NOT EXISTS idx_metrics_collected
    ON rpi_monitor.metrics (collected_at DESC);

-- View conveniente para leitura de temperatura + CPU
CREATE OR REPLACE VIEW rpi_monitor.v_latest AS
SELECT
    m.collected_at,
    h.hostname,
    c.usage_percent        AS cpu_percent,
    c.load_avg_1m,
    c.load_avg_5m,
    c.frequency_mhz        AS cpu_freq_mhz,
    t.celsius              AS cpu_temp_c,
    mem.usage_percent      AS mem_percent,
    ROUND(mem.used_bytes / 1024.0^3, 2) AS mem_used_gb,
    ROUND(mem.total_bytes / 1024.0^3, 2) AS mem_total_gb,
    th.under_voltage,
    th.currently_throttled
FROM rpi_monitor.metrics m
JOIN rpi_monitor.hosts h ON h.id = m.host_id
LEFT JOIN rpi_monitor.cpu c ON c.metric_id = m.id
LEFT JOIN rpi_monitor.temperature t ON t.metric_id = m.id AND t.zone = 'cpu-thermal'
LEFT JOIN rpi_monitor.memory mem ON mem.metric_id = m.id
LEFT JOIN rpi_monitor.throttle th ON th.metric_id = m.id
ORDER BY m.collected_at DESC;
