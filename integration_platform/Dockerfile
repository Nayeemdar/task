# ── Azure Functions Python runtime ────────────────────────────────────────────
# Base image provides the Azure Functions host + Python 3.12 worker.
# Deploy this image to Azure Container Registry and reference it from
# your Function App (Linux, custom container deployment).
FROM mcr.microsoft.com/azure-functions/python:4-python3.12

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

# ── ODBC Driver 18 for SQL Server ─────────────────────────────────────────────
# Required by pyodbc / aioodbc for the mssql+aioodbc SQLAlchemy dialect.
RUN apt-get update && apt-get install -y --no-install-recommends curl gnupg2 apt-transport-https \
    && curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list \
         -o /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# Copy application code to the Functions root
COPY . /home/site/wwwroot
