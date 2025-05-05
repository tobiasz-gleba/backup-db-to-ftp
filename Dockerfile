# ----------------------------------------------------------------------------
# Dockerfile for Universal Backup ⇆ Restore CLI
# ----------------------------------------------------------------------------
# This image bundles every system package and Python dependency required
# to run backup.py (MongoDB / MySQL / folder backup + FTP/SFTP upload
# and restore). Build it from the project root where backup.py lives:
#
#   docker build -t universal-backup .
#
# Run for a one‑off backup (env vars supplied on the command line):
#
#   docker run --rm \
#     -e FTP_HOST=ftp.example.com -e FTP_USER=ftpuser -e FTP_PASSWORD=ftppass \
#     -e MONGO_HOST=mongodb -e MONGO_USER=backup -e MONGO_PASSWORD=secret \
#     universal-backup mongodb --db myapp
#
# ----------------------------------------------------------------------------

    FROM python:3.11-slim AS base
    
    # ---- System dependencies ---------------------------------------------------
    # • mongodb-database-tools → provides mongodump / mongorestore
    # • default-mysql-client   → provides mysqldump / mysql
    # • openssh-client         → SFTP fallback when using Paramiko
    # • tar, gzip              → used by the script for folder backups
    # ---------------------------------------------------------------------------
    RUN set -eux; \
        apt-get update; \
        apt-get install -y --no-install-recommends \
            mongodb-database-tools \
            default-mysql-client \
            openssh-client \
            tar gzip; \
        rm -rf /var/lib/apt/lists/*
    
    # ---- Python dependencies ---------------------------------------------------
    COPY requirements.txt /tmp/requirements.txt
    RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt
    
    # ---- Application code ------------------------------------------------------
    WORKDIR /app
    COPY backup.py /app/backup.py
    
    # Use a non‑root UID for safety
    RUN useradd -ms /bin/bash backup && chown -R backup /app
    USER backup
    
    ENTRYPOINT ["python", "/app/backup.py"]
    CMD ["--help"]
    