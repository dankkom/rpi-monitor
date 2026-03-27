# rpi-monitor  (v2)

Coleta o maior número possível de métricas do Raspberry Pi 4 a cada minuto via cronjob
e persiste num schema dedicado no PostgreSQL.

## Métricas coletadas

| Tabela              | O que é coletado                                                          |
|---------------------|---------------------------------------------------------------------------|
| `cpu`               | uso total, por núcleo, frequência, load avg, ctx switches, syscalls       |
| `temperature`       | todas as thermal zones (`/sys/class/thermal/`)                            |
| `memory`            | RAM (total/usada/livre/cache/buffers/slab) + swap + swap I/O              |
| `disk`              | uso + inodes + I/O acumulado (leitura/escrita/busy_time) por partição     |
| `smart`             | saúde S.M.A.R.T. completa do HD externo/SSD: temperatura, horas ligado, setores ruins, erros |
| `network`           | bytes/pacotes/erros/drops por interface + speed + MTU + is_up             |
| `tcp_connections`   | contagem de conexões TCP por estado (ESTABLISHED, TIME_WAIT, LISTEN…)     |
| `system`            | uptime, processos por estado, file descriptors, entropy pool, vmstat      |
| `top_processes`     | top-N processos por CPU e por memória (configurável via `RPIMON_TOP_N`)   |
| `vcgencmd`          | clocks de 13 subsistemas (ARM, core, H264, ISP, V3D…), 4 voltagens, memória GPU, temperatura firmware |
| `throttle`          | 8 flags do `vcgencmd get_throttled` (under-voltage, freq cap, etc.)       |
| `usb_devices`       | dispositivos USB conectados (vendor, product, speed) via `/sys/bus/usb/`  |

## Dependências de sistema

```bash
# S.M.A.R.T. (obrigatório para monitorar HD externo)
sudo apt install smartmontools

# Permissão para smartctl sem sudo — adicione ao /etc/sudoers:
# seu_usuario ALL=(root) NOPASSWD: /usr/bin/smartctl
# Ou coloque o usuário no grupo disk:
sudo usermod -aG disk $USER
```

## Instalação

### 1. Banco de dados

```bash
psql -U postgres -c "CREATE DATABASE monitoramento;"
psql -U postgres -d monitoramento -f sql/setup_user.sql
psql -U postgres -d monitoramento -f sql/schema.sql
```

### 2. Python

```bash
python3 -m venv /opt/rpi-monitor/venv
source /opt/rpi-monitor/venv/bin/activate
pip install -e .
```

### 3. Configuração

```bash
cp .env.example .env
nano .env   # ajuste RPIMON_DSN

sudo mkdir -p /var/log/rpi-monitor
sudo chown $USER:$USER /var/log/rpi-monitor
```

### 4. Cronjob

```bash
crontab -e
```

```cron
* * * * * /opt/rpi-monitor/venv/bin/python /opt/rpi-monitor/collect.py
```

## Variáveis de ambiente

| Variável           | Descrição                              | Padrão  |
|--------------------|----------------------------------------|---------|
| `RPIMON_DSN`       | DSN PostgreSQL (obrigatório)           | —       |
| `RPIMON_LOG_LEVEL` | DEBUG / INFO / WARNING                 | INFO    |
| `RPIMON_LOG_FILE`  | Caminho do log (vazio = stderr)        | —       |
| `RPIMON_TOP_N`     | Quantos processos salvar no top        | 10      |

## Uso

```bash
# Dry-run — imprime JSON sem gravar
python collect.py --dry-run | python -m json.tool

# Coleta real
python collect.py
```

## HD externo / discos USB

O script detecta **automaticamente** todos os discos montados que não sejam
filesystems virtuais. Para um HD externo montado em `/mnt/hd`, ele coletará:

- Uso de espaço e inodes (tabela `disk`)
- I/O acumulado: bytes lidos/escritos, contagem de operações, tempo de busy
- S.M.A.R.T.: temperatura, horas de uso, setores realocados/pendentes/irrecuperáveis,
  erros de CRC USB (atributo 199), e o JSON completo do `smartctl` em `raw_json`

## Schema

```
rpi_monitor
├── hosts               hostname (suporta múltiplos Pis)
├── metrics             registro raiz de cada coleta
├── cpu                 métricas de CPU
├── temperature         leituras por thermal zone
├── memory              RAM, swap, slab
├── disk                uso + inodes + I/O por partição
├── smart               S.M.A.R.T. por dispositivo de bloco
├── network             tráfego por interface
├── tcp_connections     contagem TCP por estado
├── system              uptime, processos, fd, entropy, vmstat
├── top_processes       top-N por CPU e por memória
├── vcgencmd            clocks, voltagens, memória GPU (RPi específico)
├── throttle            flags de throttle (RPi específico)
├── usb_devices         dispositivos USB conectados
└── v_latest            view com resumo da coleta mais recente
```

## Queries úteis

```sql
-- Resumo das últimas 10 coletas
SELECT * FROM rpi_monitor.v_latest LIMIT 10;

-- Temperatura máxima nas últimas 24h
SELECT MAX(celsius) FROM rpi_monitor.temperature t
JOIN rpi_monitor.metrics m ON m.id = t.metric_id
WHERE t.zone = 'cpu-thermal' AND m.collected_at > now() - INTERVAL '24h';

-- Histórico de saúde do HD externo
SELECT m.collected_at, s.temperature_c, s.power_on_hours,
       s.reallocated_sectors, s.pending_sectors, s.smart_status
FROM rpi_monitor.smart s
JOIN rpi_monitor.metrics m ON m.id = s.metric_id
ORDER BY m.collected_at DESC LIMIT 100;

-- Erros de cabo/USB no HD (CRC errors — sinal de cabo ruim)
SELECT m.collected_at, s.udma_crc_errors
FROM rpi_monitor.smart s
JOIN rpi_monitor.metrics m ON m.id = s.metric_id
WHERE s.udma_crc_errors > 0
ORDER BY m.collected_at DESC;

-- Eventos de throttling
SELECT m.collected_at, th.currently_throttled, th.under_voltage, th.freq_capped
FROM rpi_monitor.throttle th
JOIN rpi_monitor.metrics m ON m.id = th.metric_id
WHERE th.currently_throttled OR th.under_voltage
ORDER BY m.collected_at DESC;

-- Frequência ARM ao longo do tempo (throttle reduz de 1800 para menos)
SELECT m.collected_at, vc.clock_arm / 1e6 AS arm_mhz, vc.volt_core
FROM rpi_monitor.vcgencmd vc
JOIN rpi_monitor.metrics m ON m.id = vc.metric_id
ORDER BY m.collected_at DESC LIMIT 60;

-- Top processo mais pesado ontem
SELECT tp.name, AVG(tp.cpu_percent) AS avg_cpu, MAX(tp.mem_rss_bytes)/1024/1024 AS max_rss_mb
FROM rpi_monitor.top_processes tp
JOIN rpi_monitor.metrics m ON m.id = tp.metric_id
WHERE tp.rank_by = 'cpu'
  AND m.collected_at BETWEEN now() - INTERVAL '2 days' AND now() - INTERVAL '1 day'
GROUP BY tp.name ORDER BY avg_cpu DESC LIMIT 10;
```

## Retenção

```sql
-- Remove dados com mais de 90 dias (cascata pelas FKs)
DELETE FROM rpi_monitor.metrics WHERE collected_at < now() - INTERVAL '90 days';

-- S.M.A.R.T. muda pouco — pode guardar mais tempo
-- Os dados SMART ficam automaticamente pois a FK é para metrics
```
