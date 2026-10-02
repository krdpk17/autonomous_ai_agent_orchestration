# Use official slim Python runtime to keep disk footprint minimal (~150MB)
FROM python:3.11-slim

# Prevent Python from buffering stdout/stderr and writing .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies (git for workspace tracking, curl for health checks)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Layer cache: install python dependencies first
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY config/ ./config/
COPY orchestrator/ ./orchestrator/
COPY profiles/ ./profiles/

# Default entrypoint runs the main orchestrator loop
CMD ["python", "orchestrator/main.py"]