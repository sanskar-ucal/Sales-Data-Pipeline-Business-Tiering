-- Runs once on first container start (POSTGRES_USER is the superuser).
CREATE USER airflow WITH PASSWORD 'airflow';
CREATE DATABASE airflow OWNER airflow;

CREATE USER pipeline WITH PASSWORD 'pipeline';
CREATE DATABASE sales_dw OWNER pipeline;

-- Read-only role for Tableau live connections.
CREATE USER tableau_reader WITH PASSWORD 'tableau_reader';
\connect sales_dw
GRANT CONNECT ON DATABASE sales_dw TO tableau_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE pipeline GRANT USAGE ON SCHEMAS TO tableau_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE pipeline GRANT SELECT ON TABLES TO tableau_reader;
