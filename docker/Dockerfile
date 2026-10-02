FROM apache/airflow:2.10.5-python3.11

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev libgomp1 \
    && rm -rf /var/lib/apt/lists/*
# Isolated interpreters: the data stack and dbt each conflict with Airflow 2's pins.
RUN python -m venv /opt/pipeline-venv && python -m venv /opt/dbt-venv \
    && chown -R airflow: /opt/pipeline-venv /opt/dbt-venv

USER airflow
COPY --chown=airflow:root requirements-dbt.txt requirements.txt /tmp/
RUN /opt/dbt-venv/bin/pip install --no-cache-dir -r /tmp/requirements-dbt.txt \
    && /opt/pipeline-venv/bin/pip install --no-cache-dir -r /tmp/requirements.txt \
    && pip install --no-cache-dir \
         --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-2.10.5/constraints-3.11.txt" \
         apache-airflow-providers-openlineage

COPY --chown=airflow:root . /opt/pipeline
# Full install for task execution; dependency-free install so DAGs can import alert callbacks.
RUN /opt/pipeline-venv/bin/pip install --no-cache-dir --no-deps -e /opt/pipeline \
    && pip install --no-cache-dir --no-deps -e /opt/pipeline

ENV PIPELINE_HOME=/opt/pipeline \
    PIPELINE_PYTHON=/opt/pipeline-venv/bin/python \
    DBT_PROJECT_DIR=/opt/pipeline/dbt \
    DBT_BIN=/opt/dbt-venv/bin/dbt \
    DATA_DIR=/opt/pipeline/data/raw \
    OUTPUT_DIR=/opt/pipeline/output
