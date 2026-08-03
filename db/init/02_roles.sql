-- Service role: owns all app-managed schemas, used by mcp/worker/viewer control planes.
CREATE ROLE geodata_app LOGIN PASSWORD 'geodata_app';
GRANT CREATE ON DATABASE geodata TO geodata_app;

-- Agent read path (the `query` tool).
CREATE ROLE agent_ro LOGIN PASSWORD 'agent_ro';
ALTER ROLE agent_ro SET statement_timeout = '15s';
ALTER ROLE agent_ro SET default_transaction_read_only = on;
ALTER ROLE agent_ro SET pgaudit.log = 'read';

-- Agent write path (the `layer` tool): may CREATE only inside ws_* schemas (granted per schema).
CREATE ROLE agent_ws LOGIN PASSWORD 'agent_ws';
ALTER ROLE agent_ws SET statement_timeout = '60s';
ALTER ROLE agent_ws SET pgaudit.log = 'all';

-- The app role may read workspace tables created by agent_ws (viewer/data endpoint, export),
-- and may clean them up (TTL garbage collection drops the whole schema).
GRANT agent_ws TO geodata_app;
