FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    default-mysql-client \
    openssh-client \
    tar gzip curl wget \
    && rm -rf /var/lib/apt/lists/*

# We'll skip MongoDB tools for ARM - the application should check if mongodump exists
# before trying to use it

# Install Python dependencies
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# Copy application code
COPY backup.py /app/
WORKDIR /app

# Create non-root user for security if it doesn't exist
RUN getent group backup || groupadd -r backup && \
    getent passwd backup || useradd -r -g backup backup

# Set proper permissions
RUN chown -R backup:backup /app
USER backup

# Run the application
ENTRYPOINT ["python", "/app/backup.py"]
CMD ["--help"] 