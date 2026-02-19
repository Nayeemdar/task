# ── Azure Functions Python runtime ────────────────────────────────────────────
# Base image provides the Azure Functions host + Python 3.12 worker.
# Deploy this image to Azure Container Registry and reference it from
# your Function App (Linux, custom container deployment).
FROM mcr.microsoft.com/azure-functions/python:4-python3.12

ENV AzureWebJobsScriptRoot=/home/site/wwwroot \
    AzureFunctionsJobHost__Logging__Console__IsEnabled=true

# Install Python dependencies
COPY requirements.txt /
RUN pip install --no-cache-dir -r /requirements.txt

# Copy application code to the Functions root
COPY . /home/site/wwwroot
