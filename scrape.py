# /// script
# requires-python = ">=3.9"
# dependencies = [
#     "requests",
#     "rich",
#     "typer",
# ]
# ///
import json
import os
import sqlite3
import time
import typer
import requests
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.table import Table

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.json")

console = Console()

HEADERS_BASE = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "it,it-IT;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
    "origin": "https://bo.octavianlab.com",
    "referer": "https://bo.octavianlab.com/",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36 Edg/154.0.0.0",
    "x-lab-language": "it",
}


def init_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cms_pages (
            id INTEGER PRIMARY KEY,
            idLicensee INTEGER,
            idSkin INTEGER,
            title TEXT,
            language TEXT,
            body TEXT,
            active INTEGER,
            lastUpdate TEXT,
            type INTEGER,
            metaTitle TEXT,
            metaDescription TEXT,
            signature TEXT,
            author TEXT,
            url TEXT,
            version TEXT,
            seoActive INTEGER,
            raw_json TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS failed_ids (
            id INTEGER PRIMARY KEY,
            reason TEXT,
            last_attempt TEXT
        )
        """
    )
    conn.commit()
    return conn


def record_failure(conn, page_id, reason):
    conn.execute(
        """
        INSERT OR REPLACE INTO failed_ids (id, reason, last_attempt)
        VALUES (?, ?, datetime('now'))
        """,
        (page_id, reason),
    )
    conn.commit()


def clear_failure(conn, page_id):
    conn.execute("DELETE FROM failed_ids WHERE id = ?", (page_id,))
    conn.commit()


def get_failed_ids(conn):
    rows = conn.execute("SELECT id FROM failed_ids ORDER BY id").fetchall()
    return [row[0] for row in rows]


def save_record(conn, data):
    conn.execute(
        """
        INSERT OR REPLACE INTO cms_pages
        (id, idLicensee, idSkin, title, language, body, active, lastUpdate,
         type, metaTitle, metaDescription, signature, author, url, version, seoActive, raw_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            data.get("id"),
            data.get("idLicensee"),
            data.get("idSkin"),
            data.get("title"),
            data.get("language"),
            data.get("body"),
            int(bool(data.get("active"))),
            data.get("lastUpdate"),
            data.get("type"),
            data.get("metaTitle"),
            data.get("metaDescription"),
            data.get("signature"),
            data.get("author"),
            data.get("url"),
            data.get("version"),
            int(bool(data.get("seoActive"))),
            json.dumps(data),
        ),
    )
    conn.commit()


def save_html(conn, output_dir, data):
    idLicensee = data.get("idLicensee", 0)
    idSkin = data.get("idSkin", 0)
    version = data.get("version", "unknown")
    url = (data.get("url") or f"/page_{data.get('id')}").lstrip("/")

    dir_path = os.path.join(
        output_dir, str(idLicensee), str(idSkin), str(version), os.path.dirname(url)
    )
    os.makedirs(dir_path, exist_ok=True)

    base, ext = os.path.splitext(os.path.basename(url))
    if ext == ".xxx":
        ext = ".scss"
    filename = base + (ext or ".html")
    file_path = os.path.join(dir_path, filename)

    if os.path.exists(file_path):
        row = conn.execute(
            """
            SELECT lastUpdate FROM cms_pages
            WHERE idLicensee = ? AND idSkin = ? AND version = ? AND url = ? AND id != ?
            ORDER BY id DESC LIMIT 1
            """,
            (idLicensee, idSkin, version, data.get("url"), data.get("id")),
        ).fetchone()
        tag = (row[0] if row and row[0] else "unknown").replace(":", "-").replace(" ", "_")
        renamed_path = os.path.join(dir_path, f"{base}_{tag}{ext or '.html'}")
        os.replace(file_path, renamed_path)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(data.get("body") or "")

    return file_path


def fetch_page(session, headers, cfg, conn, page_id):
    """Fetch one page_id and persist it. Returns (outcome, detail)."""
    url = f"{cfg['base_url']}/{page_id}"
    try:
        resp = session.get(url, headers=headers, timeout=15)
    except requests.RequestException as exc:
        return "error", str(exc)

    if resp.status_code == 404:
        return "not_found", "404"

    if resp.status_code != 200:
        return "error", f"status {resp.status_code}"

    data = resp.json().get("data")
    if not data:
        return "not_found", "dati vuoti"

    file_path = save_html(conn, cfg["output_dir"], data)
    save_record(conn, data)
    return "saved", file_path


def build_progress():
    return Progress(
        TextColumn("[bold cyan]scraping"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        TextColumn("{task.fields[status]}"),
        console=console,
    )


def print_report(total, saved, not_found, errors, elapsed, interrupted, title="Report esecuzione"):
    table = Table(
        title=title + (" (interrotto)" if interrupted else ""),
        show_header=False,
    )
    table.add_row("Totale ID processati", str(total))
    table.add_row("[green]File scaricati[/green]", f"[green]{saved}[/green]")
    table.add_row("[yellow]Non trovati/skip[/yellow]", f"[yellow]{not_found}[/yellow]")
    table.add_row("[red]Errori[/red]", f"[red]{errors}[/red]")
    table.add_row("Tempo esecuzione", f"{elapsed:.2f}s")
    if total:
        table.add_row("Media per richiesta", f"{elapsed / total:.3f}s")

    console.print()
    console.print(table)


def run_sync(cfg, headers, conn, session):
    total = cfg["end"] - cfg["start"]
    saved = 0
    not_found = 0
    errors = 0
    interrupted = False
    start_time = time.monotonic()

    progress = build_progress()

    with progress:
        task = progress.add_task("scraping", total=total, status="in attesa...")

        try:
            for page_id in range(cfg["start"], cfg["end"]):
                outcome, detail = fetch_page(session, headers, cfg, conn, page_id)

                if outcome == "error":
                    errors += 1
                    record_failure(conn, page_id, detail)
                    progress.update(task, status=f"[red]errore id {page_id}: {detail}[/red]")
                elif outcome == "not_found":
                    not_found += 1
                    progress.update(task, status=f"[yellow]non trovato {page_id}[/yellow]")
                else:
                    saved += 1
                    progress.update(task, status=f"[green]salvato: {detail}[/green]")

                progress.advance(task)
                time.sleep(cfg["delay"])
        except KeyboardInterrupt:
            interrupted = True
            progress.update(task, status="[bold red]interrotto (Ctrl+C)[/bold red]")

    elapsed = time.monotonic() - start_time
    print_report(total, saved, not_found, errors, elapsed, interrupted)


def run_retry(cfg, headers, conn, session):
    failed_ids = get_failed_ids(conn)
    total = len(failed_ids)
    saved = 0
    not_found = 0
    errors = 0
    interrupted = False
    start_time = time.monotonic()

    progress = build_progress()

    with progress:
        task = progress.add_task("retry", total=total, status="in attesa...")

        try:
            for page_id in failed_ids:
                outcome, detail = fetch_page(session, headers, cfg, conn, page_id)

                if outcome == "error":
                    errors += 1
                    record_failure(conn, page_id, detail)
                    progress.update(task, status=f"[red]ancora in errore {page_id}: {detail}[/red]")
                else:
                    clear_failure(conn, page_id)
                    if outcome == "not_found":
                        not_found += 1
                        progress.update(task, status=f"[yellow]non trovato {page_id}, rimosso[/yellow]")
                    else:
                        saved += 1
                        progress.update(task, status=f"[green]salvato: {detail}[/green]")

                progress.advance(task)
                time.sleep(cfg["delay"])
        except KeyboardInterrupt:
            interrupted = True
            progress.update(task, status="[bold red]interrotto (Ctrl+C)[/bold red]")

    elapsed = time.monotonic() - start_time
    print_report(total, saved, not_found, errors, elapsed, interrupted, title="Report retry")


def build_context():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    headers = dict(HEADERS_BASE)
    headers["authorization"] = f"token {cfg['token']}"

    conn = init_db(cfg["db_path"])
    session = requests.Session()

    return cfg, headers, conn, session


app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.command(help="Scraper CMS pages Octavian: scarica, salva su file e SQLite.")
def cli(
    sync_mode: bool = typer.Option(False, "-s", "--sync", help="cicla tutti gli id da start a end (default)"),
    retry_mode: bool = typer.Option(False, "-r", "--retry", help="riprova solo gli id in failed_ids; i successi vengono rimossi dalla tabella"),
):
    if sync_mode and retry_mode:
        raise typer.BadParameter("usa solo uno tra --sync e --retry")

    cfg, headers, conn, session = build_context()
    if retry_mode:
        run_retry(cfg, headers, conn, session)
    else:
        run_sync(cfg, headers, conn, session)
    conn.close()


if __name__ == "__main__":
    app()
