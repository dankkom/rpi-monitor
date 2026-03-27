#!/usr/bin/env python3
"""
collect.py — Entrypoint do cron job de monitoramento.

Uso:
    python collect.py
    python collect.py --dry-run   # imprime JSON, não salva no banco

Configuração via variáveis de ambiente (ou arquivo .env):
    RPIMON_DSN   — DSN do PostgreSQL (obrigatório)
                   ex: postgresql://rpimon:senha@localhost:5432/monitoramento
    RPIMON_LOG_LEVEL — DEBUG | INFO | WARNING  (default: INFO)
    RPIMON_LOG_FILE  — caminho do log file (default: stderr)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Suporte opcional a python-dotenv
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env")
except ImportError:
    pass

from rpi_monitor.collector import Snapshot, collect_snapshot, persist_snapshot


def _setup_logging(level: str, log_file: str | None) -> None:
    fmt = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    handlers: list[logging.Handler] = []

    if log_file:
        handlers.append(logging.FileHandler(log_file))
    else:
        handlers.append(logging.StreamHandler(sys.stderr))

    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)


def _snapshot_to_dict(snap: Snapshot) -> dict:
    """Serialização simples para --dry-run."""
    return {
        "hostname": snap.hostname,
        "collected_at": snap.collected_at.isoformat(),
        "cpu": snap.cpu.__dict__,
        "temperatures": [t.__dict__ for t in snap.temperatures],
        "memory": snap.memory.__dict__,
        "disks": [d.__dict__ for d in snap.disks],
        "network": [n.__dict__ for n in snap.network],
        "system": {
            **snap.system.__dict__,
            "boot_time": snap.system.boot_time.isoformat(),
        },
        "throttle": snap.throttle.__dict__ if snap.throttle else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="RPi Monitor — coleta e persiste métricas")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Coleta métricas e imprime JSON, sem gravar no banco",
    )
    args = parser.parse_args()

    log_level = os.getenv("RPIMON_LOG_LEVEL", "INFO")
    log_file = os.getenv("RPIMON_LOG_FILE")
    _setup_logging(log_level, log_file)

    log = logging.getLogger(__name__)

    try:
        log.debug("Iniciando coleta de snapshot...")
        snapshot = collect_snapshot()
        log.debug("Snapshot coletado para host=%s", snapshot.hostname)
    except Exception:
        log.exception("Falha ao coletar métricas")
        return 1

    if args.dry_run:
        print(json.dumps(_snapshot_to_dict(snapshot), indent=2, default=str))
        return 0

    dsn = os.getenv("RPIMON_DSN")
    if not dsn:
        log.error("Variável de ambiente RPIMON_DSN não definida")
        return 1

    try:
        persist_snapshot(snapshot, dsn)
    except Exception:
        log.exception("Falha ao persistir snapshot no PostgreSQL")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
