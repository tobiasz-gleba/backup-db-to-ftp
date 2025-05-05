# Universal Backup ⇆ Restore with FTP/SFTP

A **Typer-based** command-line tool that can **backup _and_ restore**:

- **MongoDB** databases (via `mongodump` / `mongorestore`)
- **MySQL** databases (via `mysqldump` / `mysql`)
- **Filesystem folders** (via `tar`)

Backups are compressed into timestamped `.tar.gz` archives and pushed to an FTP/SFTP server with an optional retention policy. The same archives can be pulled back and restored with a single command.

```bash
# Create backups
python backup.py mongodb --db myapp
python backup.py mysql   --db wordpress --user root
python backup.py folder  /var/www/html

# Restore (from the latest archive if none specified)
python backup.py restore mongodb --db myapp
python backup.py restore mysql   --db wordpress
python backup.py restore folder  /var/www/html
```

Configuration uses **environment variables** (see table below) and/or command-line flags. For interactive help, run `python backup.py --help` or any sub-command with `--help`.

---

## Dependencies

- Python ≥ 3.8: `pip install typer[all] rich paramiko` (`paramiko` only for SFTP)
- System binaries: `mongodump`, `mongorestore`, `mysqldump`, `mysql`, `tar`

---

## Environment Variable Reference

```plaintext
FTP_HOST         FTP/SFTP server               (required)
FTP_PORT         Port (21 FTP, 22 SFTP)
FTP_USER         Username                      (required)
FTP_PASSWORD     Password                      (required)
FTP_DEST_DIR     Remote dir      (default /backups)
FTP_PROTOCOL     ftp | sftp      (default ftp)
FTP_PASSIVE      true|false      (FTP passive, default true)
RETENTION_DAYS   Days to keep backups (default 7, 0 = keep all)

# MongoDB
MONGO_HOST       localhost   MONGO_PORT    27017
MONGO_USER       -           MONGO_PASSWORD -     MONGO_AUTH_DB  admin

# MySQL
MYSQL_HOST       localhost   MYSQL_PORT    3306
MYSQL_USER       root        MYSQL_PASSWORD -
```

---

## Cron Example

```bash
0 2 * * * /usr/bin/python3 /opt/scripts/backup.py mongodb --db myapp >> /var/log/backup.log 2>&1
```