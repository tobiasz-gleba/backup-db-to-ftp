#!/usr/bin/env python3

from __future__ import annotations

import datetime as _dt
import ftplib
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

import typer
from rich import print  # noqa: T003

app = typer.Typer(add_completion=False, help="Backup or restore MongoDB, MySQL, or a folder via FTP/SFTP.")
restore_app = typer.Typer(help="Restore backups from local archive or FTP/SFTP.")
app.add_typer(restore_app, name="restore")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# ────────────────────────────────────────────────────────────────────────────
# Internal Helpers
# ────────────────────────────────────────────────────────────────────────────

def _timestamp() -> str:
    return _dt.datetime.utcnow().strftime("%Y%m%d%H%M%S")


def _make_archive(src: Path, label: str, workdir: Optional[Path] = None) -> Path:
    dest = (workdir or Path.cwd()) / f"{label}_{_timestamp()}.tar.gz"
    logging.info("Creating archive %s", dest)
    with tarfile.open(dest, "w:gz") as tar:
        tar.add(src, arcname=src.name, recursive=True)
    return dest


# ───────────────────────────── FTP / SFTP LAYER ────────────────────────────


def _connect_ftp():
    host, user, passwd = os.getenv("FTP_HOST"), os.getenv("FTP_USER"), os.getenv("FTP_PASSWORD")
    port = int(os.getenv("FTP_PORT", "21"))
    if not all([host, user, passwd]):
        raise RuntimeError("Missing FTP_HOST/USER/PASSWORD env vars")
    ftp = ftplib.FTP()
    ftp.connect(host, port)
    ftp.login(user, passwd)
    ftp.set_pasv(os.getenv("FTP_PASSIVE", "true").lower() in {"1", "true", "yes"})
    _ftp_cd_mkdirs(ftp, os.getenv("FTP_DEST_DIR", "/backups"))
    return ftp


def _ftp_cd_mkdirs(ftp: ftplib.FTP, dest_dir: str):
    for part in dest_dir.strip("/").split("/"):
        if part and part not in ftp.nlst():
            try:
                ftp.mkd(part)
            except ftplib.error_perm:
                pass
        ftp.cwd(part)


def _upload_archive(path: Path):
    protocol = os.getenv("FTP_PROTOCOL", "ftp").lower()
    if protocol == "ftp":
        ftp = _connect_ftp()
        logging.info("Uploading %s", path.name)
        with path.open("rb") as fh:
            ftp.storbinary(f"STOR {path.name}", fh)
        _enforce_retention_ftp(ftp)
        ftp.quit()
    elif protocol == "sftp":
        _upload_archive_sftp(path)
    else:
        raise RuntimeError(f"Unsupported FTP_PROTOCOL {protocol}")


# SFTP helpers --------------------------------------------------------------
try:
    import paramiko  # type: ignore
except ImportError:
    paramiko = None


def _connect_sftp():
    if paramiko is None:
        raise RuntimeError("paramiko not installed – required for SFTP")
    host, user, passwd = os.getenv("FTP_HOST"), os.getenv("FTP_USER"), os.getenv("FTP_PASSWORD")
    port = int(os.getenv("FTP_PORT", "22"))
    t = paramiko.Transport((host, port))
    t.connect(username=user, password=passwd)
    sftp = paramiko.SFTPClient.from_transport(t)
    _sftp_mkdirs(sftp, os.getenv("FTP_DEST_DIR", "/backups"))
    return sftp


def _sftp_mkdirs(sftp, dest_dir: str):
    path = ""
    for part in dest_dir.strip("/").split("/"):
        path = f"{path}/{part}" if path else f"/{part}"
        try:
            sftp.stat(path)
        except FileNotFoundError:
            sftp.mkdir(path)


def _upload_archive_sftp(path: Path):
    sftp = _connect_sftp()
    remote_path = f"{os.getenv('FTP_DEST_DIR', '/backups').rstrip('/')}/{path.name}"
    logging.info("Uploading %s", remote_path)
    sftp.put(path.as_posix(), remote_path)
    _enforce_retention_sftp(sftp)
    sftp.close()


# ───────────────────────────── RETENTION ───────────────────────────────────

def _extract_ts(name: str) -> Optional[_dt.datetime]:
    try:
        return _dt.datetime.strptime(name.rsplit("_", 1)[-1].split(".")[0], "%Y%m%d%H%M%S")
    except Exception:
        return None


def _enforce_retention_ftp(ftp: ftplib.FTP):
    days = int(os.getenv("RETENTION_DAYS", "7"))
    if days <= 0:
        return
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=days)
    for fname in ftp.nlst():
        ts = _extract_ts(fname)
        if ts and ts < cutoff:
            logging.info("Deleting old backup %s", fname)
            try:
                ftp.delete(fname)
            except ftplib.error_perm:
                pass


def _enforce_retention_sftp(sftp):
    days = int(os.getenv("RETENTION_DAYS", "7"))
    if days <= 0:
        return
    dest_dir = os.getenv("FTP_DEST_DIR", "/backups")
    cutoff = _dt.datetime.utcnow() - _dt.timedelta(days=days)
    for entry in sftp.listdir_attr(dest_dir):
        ts = _extract_ts(entry.filename)
        if ts and ts < cutoff:
            logging.info("Deleting old backup %s", entry.filename)
            sftp.remove(f"{dest_dir.rstrip('/')}/{entry.filename}")


# ───────────────────────────── DOWNLOAD (RESTORE) ──────────────────────────

def _download_backup(prefix: str, remote_name: Optional[str] = None) -> Path:
    """Download backup archive and return local path inside a temp dir."""
    protocol = os.getenv("FTP_PROTOCOL", "ftp").lower()
    tempdir = Path(tempfile.mkdtemp())
    if protocol == "ftp":
        ftp = _connect_ftp()
        files = ftp.nlst()
        target = remote_name or _latest_matching(files, prefix)
        local = tempdir / target
        logging.info("Downloading %s", target)
        with local.open("wb") as fh:
            ftp.retrbinary(f"RETR {target}", fh.write)
        ftp.quit()
        return local
    elif protocol == "sftp":
        sftp = _connect_sftp()
        files = [f.filename for f in sftp.listdir_attr(os.getenv("FTP_DEST_DIR", "/backups"))]
        target = remote_name or _latest_matching(files, prefix)
        local = tempdir / target
        remote_path = f"{os.getenv('FTP_DEST_DIR', '/backups').rstrip('/')}/{target}"
        logging.info("Downloading %s", remote_path)
        sftp.get(remote_path, local.as_posix())
        sftp.close()
        return local
    else:
        raise RuntimeError(f"Unsupported FTP_PROTOCOL {protocol}")


def _latest_matching(files: list[str], prefix: str) -> str:
    candidates = sorted([f for f in files if f.startswith(prefix)], reverse=True)
    if not candidates:
        raise RuntimeError(f"No backups found with prefix {prefix}")
    return candidates[0]


def _extract_archive(archive: Path) -> Path:
    tempdir = Path(tempfile.mkdtemp())
    with tarfile.open(archive) as tar:
        tar.extractall(tempdir)
    return tempdir


# ───────────────────────────── UTILITIES ───────────────────────────────────

def _run(cmd: list[str]):
    logging.info("EXEC: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
    except FileNotFoundError:
        raise RuntimeError(f"Binary {cmd[0]} not found – install and ensure it is on the PATH")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed (exit {e.returncode})")


# ───────────────────────────── BACKUP COMMANDS ─────────────────────────────

@app.command()
def mongodb(
    db: Optional[str] = typer.Option(None, "--db", help="Database name (omit → all)"),
    host: str = typer.Option(os.getenv("MONGO_HOST", "localhost")),
    port: str = typer.Option(os.getenv("MONGO_PORT", "27017")),
    user: Optional[str] = typer.Option(os.getenv("MONGO_USER")),
    password: Optional[str] = typer.Option(os.getenv("MONGO_PASSWORD")),
    auth_db: str = typer.Option(os.getenv("MONGO_AUTH_DB", "admin")),
):
    """Backup MongoDB and push to FTP/SFTP."""
    with tempfile.TemporaryDirectory() as td:
        dump_dir = Path(td) / "dump"
        cmd = ["mongodump", "--host", host, "--port", port, "--out", str(dump_dir)]
        if db:
            cmd += ["--db", db]
        if user and password:
            cmd += ["--username", user, "--password", password, "--authenticationDatabase", auth_db]
        _run(cmd)
        archive = _make_archive(dump_dir, "mongodb_dump")
        _upload_archive(archive)


@app.command()
def mysql(
    db: str = typer.Option(..., "--db", help="Database name"),
    host: str = typer.Option(os.getenv("MYSQL_HOST", "localhost")),
    port: str = typer.Option(os.getenv("MYSQL_PORT", "3306")),
    user: str = typer.Option(os.getenv("MYSQL_USER", "root")),
    password: Optional[str] = typer.Option(os.getenv("MYSQL_PASSWORD")),
    single_transaction: bool = typer.Option(True, help="Use --single-transaction for consistency"),
):
    """Backup MySQL database and push to FTP/SFTP."""
    with tempfile.TemporaryDirectory() as td:
        dump_file = Path(td) / f"{db}.sql"
        cmd = ["mysqldump", "--host", host, "--port", port, "--user", user]
        if single_transaction:
            cmd.append("--single-transaction")
        cmd.append(db)
        env = os.environ.copy()
        if password:
            env["MYSQL_PWD"] = password
        logging.info("Running mysqldump …")
        with dump_file.open("wb") as fh:
            subprocess.run(cmd, env=env, stdout=fh, check=True)
        archive = _make_archive(dump_file, "mysql_dump")
        _upload_archive(archive)


@app.command()
def folder(path: Path = typer.Argument(..., exists=True, readable=True, help="Folder to back up")):
    """Archive any folder and push to FTP/SFTP."""
    path = path.resolve()
    with tempfile.TemporaryDirectory() as td:
        temp_copy = Path(td) / path.name
        shutil.copytree(path, temp_copy)
        archive = _make_archive(temp_copy, "folder_backup")
        _upload_archive(archive)


# ───────────────────────────── RESTORE COMMANDS ────────────────────────────

@restore_app.command("mongodb")
def restore_mongodb(
    db: Optional[str] = typer.Option(None, "--db", help="Target DB (omit = all contained)"),
    archive: Optional[Path] = typer.Option(None, "--archive", exists=True, help="Local .tar.gz to restore"),
    remote_file: Optional[str] = typer.Option(None, "--remote-file", help="Remote archive name to download"),
    host: str = typer.Option(os.getenv("MONGO_HOST", "localhost")),
    port: str = typer.Option(os.getenv("MONGO_PORT", "27017")),
    user: Optional[str] = typer.Option(os.getenv("MONGO_USER")),
    password: Optional[str] = typer.Option(os.getenv("MONGO_PASSWORD")),
    auth_db: str = typer.Option(os.getenv("MONGO_AUTH_DB", "admin")),
    drop: bool = typer.Option(True, help="Drop collections before restoring"),
):
    """Restore MongoDB from an archive (local or fetched)."""
    archive = archive or _download_backup("mongodb_dump_", remote_file)
    workdir = _extract_archive(archive)
    dump_dir = next(workdir.glob("dump"))
    cmd = ["mongorestore", "--host", host, "--port", port, str(dump_dir)]
    if drop:
        cmd.insert(1, "--drop")
    if db:
        cmd += ["--nsInclude", f"{db}.*"]
    if user and password:
        cmd += ["--username", user, "--password", password, "--authenticationDatabase", auth_db]
    _run(cmd)
    print("[bold green]MongoDB restore completed✔️[/]")


@restore_app.command("mysql")
def restore_mysql(
    db: str = typer.Option(..., "--db", help="Target DB name"),
    archive: Optional[Path] = typer.Option(None, "--archive", exists=True),
    remote_file: Optional[str] = typer.Option(None, "--remote-file"),
    host: str = typer.Option(os.getenv("MYSQL_HOST", "localhost")),
    port: str = typer.Option(os.getenv("MYSQL_PORT", "3306")),
    user: str = typer.Option(os.getenv("MYSQL_USER", "root")),
    password: Optional[str] = typer.Option(os.getenv("MYSQL_PASSWORD")),
):
    """Restore MySQL from SQL archive."""
    archive = archive or _download_backup("mysql_dump_", remote_file)
    workdir = _extract_archive(archive)
    sql_file = next(workdir.glob("*.sql"))
    cmd = ["mysql", "--host", host, "--port", port, "--user", user, db]
    env = os.environ.copy()
    if password:
        env["MYSQL_PWD"] = password
    with sql_file.open("rb") as fh:
        logging.info("Importing SQL …")
        proc = subprocess.Popen(cmd, env=env, stdin=fh)
        proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("mysql import failed")
    print("[bold green]MySQL restore completed✔️[/]")


@restore_app.command("folder")
def restore_folder(
    dest: Path = typer.Argument(..., help="Destination path (existing or new)"),
    archive: Optional[Path] = typer.Option(None, "--archive", exists=True),
    remote_file: Optional[str] = typer.Option(None, "--remote-file"),
    overwrite: bool = typer.Option(False, help="Overwrite existing files"),
):
    """Restore a folder backup by extracting the archive to DEST."""
    archive = archive or _download_backup("folder_backup_", remote_file)
    workdir = _extract_archive(archive)
    src_dir = next(workdir.iterdir())  # first (and only) extracted dir
    dest = dest.expanduser().resolve()
    dest.mkdir(parents=True, exist_ok=True)
    logging.info("Copying files to %s", dest)
    shutil.copytree(src_dir, dest, dirs_exist_ok=overwrite)
    print("[bold green]Folder restore completed✔️[/]")


# ────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app()
