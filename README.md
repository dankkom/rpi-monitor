# rpi-monitor

Coleta métricas do Raspberry Pi 4 a cada minuto via cronjob e persiste num schema dedicado no PostgreSQL.

## Métricas coletadas

| Categoria      | Dados                                                                 |
|----------------|-----------------------------------------------------------------------|
| **CPU**        | uso total, uso por núcleo, frequência, load avg, ctx switches         |
| **Temperatura**| todas as thermal zones (`/sys/class/thermal/`)                        |
| **Memória**    | RAM (total/usada/livre/cache/buffers) + swap                          |
| **Disco**      | uso por partição + I/O acumulado (leitura/escrita)                    |
| **Rede**       | bytes/pacotes por interface, erros e drops                            |
| **Sistema**    | uptime, contagem de processos por estado, usuários logados            |
| **Throttle**   | flags do `vcgencmd get_throttled` (under-voltage, freq cap, etc.)     |

## Instalação

### 1. Banco de dados

```bash
# Como postgres
psql -U postgres -c "CREATE DATABASE monitoramento;"
psql -U postgres -d monitoramento -f sql/setup_user.sql
psql -U postgres -d monitoramento -f sql/schema.sql
```

### 2. Dependências Python

```bash
# No Raspberry Pi, com Python 3.11+
python3 -m venv /opt/rpi-monitor/venv
source /opt/rpi-monitor/venv/bin/activate
pip install -e .
```

### 3. Configuração

```bash
cp .env.example .env
# Edite .env com a senha correta do PostgreSQL
nano .env

# Cria diretório de log
sudo mkdir -p /var/log/rpi-monitor
sudo chown $USER:$USER /var/log/rpi-monitor
```

### 4. Cronjob

```bash
crontab -e
```

Adicione a linha:

```cron
* * * * * /opt/rpi-monitor/venv/bin/python /opt/rpi-monitor/collect.py >> /var/log/rpi-monitor/cron.log 2>&1
```

> **Dica:** se preferir logar via `RPIMON_LOG_FILE`, remova o `>>` do cron.

## Uso manual

```bash
# Dry-run: imprime JSON sem gravar no banco
python collect.py --dry-run | python -m json.tool

# Coleta real
python collect.py
```

## Estrutura do banco

```
rpi_monitor
├── hosts          — hostname (suporta múltiplos Pis)
├── metrics        — registro pai de cada coleta (FK para hosts)
├── cpu            — métricas de CPU
├── temperature    — leituras por thermal zone
├── memory         — RAM e swap
├── disk           — uso e I/O por partição
├── network        — tráfego por interface
├── system         — processos, uptime, usuários
├── throttle       — flags do vcgencmd
└── v_latest       — view com as métricas mais recentes
```

## Queries úteis

```sql
-- Últimas 10 coletas
SELECT * FROM rpi_monitor.v_latest LIMIT 10;

-- Temperatura máxima nas últimas 24h
SELECT MAX(celsius) AS max_temp
FROM rpi_monitor.temperature t
JOIN rpi_monitor.metrics m ON m.id = t.metric_id
WHERE t.zone = 'cpu-thermal'
  AND m.collected_at > now() - INTERVAL '24 hours';

-- Eventos de throttling
SELECT m.collected_at, th.*
FROM rpi_monitor.throttle th
JOIN rpi_monitor.metrics m ON m.id = th.metric_id
WHERE th.currently_throttled OR th.under_voltage
ORDER BY m.collected_at DESC;

-- Uso médio de CPU por hora
SELECT
    date_trunc('hour', m.collected_at) AS hora,
    ROUND(AVG(c.usage_percent)::numeric, 2) AS cpu_avg
FROM rpi_monitor.cpu c
JOIN rpi_monitor.metrics m ON m.id = c.metric_id
GROUP BY 1
ORDER BY 1 DESC
LIMIT 48;
```

## Retenção de dados

Para evitar crescimento ilimitado, adicione uma purge via cron ou pg_cron:

```sql
-- Remove coletas com mais de 90 dias
DELETE FROM rpi_monitor.metrics
WHERE collected_at < now() - INTERVAL '90 days';
```
