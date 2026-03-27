-- =============================================================================
-- Executar como superusuário (postgres) antes de rodar schema.sql
-- =============================================================================

-- 1. Cria o banco (se ainda não existir)
-- Execute fora de uma transação: psql -c "CREATE DATABASE monitoramento;"
-- ou descomente abaixo se executar com psql -f como superusuário:
-- CREATE DATABASE monitoramento;

-- 2. Conecte ao banco 'monitoramento' antes de continuar:
-- \c monitoramento

-- 3. Cria o role de aplicação com senha
CREATE ROLE rpimon LOGIN PASSWORD 'TROCAR_SENHA';

-- 4. Garante acesso ao schema (será criado pelo schema.sql)
GRANT USAGE ON SCHEMA rpi_monitor TO rpimon;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA rpi_monitor TO rpimon;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA rpi_monitor TO rpimon;

-- Garante que objetos futuros criados no schema também sejam acessíveis
ALTER DEFAULT PRIVILEGES IN SCHEMA rpi_monitor
    GRANT SELECT, INSERT ON TABLES TO rpimon;

ALTER DEFAULT PRIVILEGES IN SCHEMA rpi_monitor
    GRANT USAGE, SELECT ON SEQUENCES TO rpimon;
