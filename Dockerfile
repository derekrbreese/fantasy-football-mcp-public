# Use Python 3.11 slim image for compatibility with dependencies
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Upgrade pip and install Python dependencies
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash app \
    && chown -R app:app /app
USER app

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# Container ingress is an explicit deployment boundary; local runs default to loopback.
ENV HOST=0.0.0.0

# Expose port (Cloud Run will set PORT env var)
EXPOSE 8080

# Run FastMCP server directly (no_auth_server shim removed)
CMD python fastmcp_server.py
