# Airflow 3.x with custom packages for Profile Scorer
FROM apache/airflow:3.0.0-python3.12

USER root

# Install git for pip git dependencies
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

USER airflow

# Copy workspace packages
COPY --chown=airflow:root packages/ /opt/airflow/packages/
COPY --chown=airflow:root pyproject.toml uv.lock /opt/airflow/

# Install uv and packages
RUN pip install uv && \
    cd /opt/airflow && \
    uv sync --no-dev

# Copy DAGs and tasks
COPY --chown=airflow:root dags/ /opt/airflow/dags/
COPY --chown=airflow:root tasks/ /opt/airflow/tasks/

# Copy audience config files from dags/audiences
COPY --chown=airflow:root dags/audiences/ /opt/airflow/audiences/

# Copy RDS SSL certificate
COPY --chown=airflow:root certs/ /opt/airflow/certs/

WORKDIR /opt/airflow
