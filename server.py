"""
HTTP frontend for the subtitle generator pipeline.

The server wraps the existing CLI in a thin FastAPI service. Each job runs as a
subprocess of `main.py` with the client's API keys injected as environment
variables. The keys are never written to disk, logged, or returned to the
client.

Security model assumes:
- Deployment behind a TLS-terminating reverse proxy (Railway, nginx, etc.).
- A single trusted browser origin set via the `ALLOWED_ORIGIN` env var.
- The container is single-tenant; no other process should be able to read
  /proc/<pid>/environ for the worker subprocesses.

The CLI itself never echoes API key values to stdout or stderr, so subprocess
output is captured and inspected only by patterns we explicitly trust before
being shown to the client.
"""

import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


# Configuration ---------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent
JOBS_ROOT = PROJECT_ROOT / "web_jobs"
JOBS_ROOT.mkdir(parents=True, exist_ok=True)

ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN")
if not ALLOWED_ORIGIN:
    raise SystemExit(
        "ALLOWED_ORIGIN must be set. Use the deployment URL "
        "(e.g. https://demo.soniox.com) in production, or '*' for local dev."
    )

JOB_TTL_HOURS = int(os.getenv("JOB_TTL_HOURS", "24"))
SWEEP_INTERVAL_SECONDS = int(os.getenv("SWEEP_INTERVAL_SECONDS", "3600"))

JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")  # uuid4().hex


# Content Security Policy. `connect-src 'self'` is the load-bearing line for
# defending against API key exfiltration: even if a script somehow runs on the
# page, it cannot send fetch/XHR requests to anywhere except our own origin.
CSP = "; ".join([
    "default-src 'none'",
    # 'wasm-unsafe-eval' is required because Tailwind v4 ships a WebAssembly-
    # compiled CSS engine. It does not authorize JavaScript eval(), only WASM
    # module instantiation.
    "script-src 'self' 'wasm-unsafe-eval' https://unpkg.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "form-action 'self'",
    "base-uri 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
])

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}


# Per-job state. The in-memory dict is the source of truth for status/events;
# `web_jobs/<id>/` holds the uploaded media and generated SRTs.
_jobs: dict[str, dict[str, Any]] = {}
_jobs_lock = threading.Lock()


app = FastAPI(title="Subtitle Generator", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")


@app.middleware("http")
async def origin_check(request: Request, call_next: Callable) -> Any:
    """
    Reject state-changing requests that don't come from the configured origin.
    GETs are exempt because the browser never lets cross-origin GETs read our
    HTML or JSON without our explicit cooperation (CORS), and we never grant it.
    """
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        if ALLOWED_ORIGIN != "*":
            origin = request.headers.get("origin")
            if origin != ALLOWED_ORIGIN:
                return JSONResponse({"error": "Forbidden"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next: Callable) -> Any:
    """Apply hardening headers to every response, including error responses."""
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers[header] = value
    # The HTML page and the JSON API both must never be cached: keys travel in
    # request bodies, and a cached job-status response could leak between users
    # on a shared browser.
    if request.url.path == "/" or request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _require_valid_job_id(job_id: str) -> None:
    if not JOB_ID_RE.match(job_id):
        raise HTTPException(400, "Invalid job ID")


# Each rule is a regex with named groups and a template that uses those names.
# CLI stdout lines that match a rule become a clean event for the client; lines
# that don't match are dropped, so ffmpeg verbosity and Python tracebacks never
# reach the browser.

_EVENT_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"^Found (?P<count>\d+) processable files!$"),
        "Found {count} video file(s)",
    ),
    (
        re.compile(r"^Queued file for processing: .*?(?P<name>[^/\\]+)\.\.\.$"),
        "Queued: {name}",
    ),
    (
        re.compile(r"^Successfully processed .*?(?P<name>[^/\\]+)!$"),
        "Audio extracted: {name}",
    ),
    (
        re.compile(r"^Generated context for .*?(?P<name>[^/\\]+)\.$"),
        "Generated vocabulary hints for {name}",
    ),
    (
        re.compile(r"^Starting transcription for (?P<name>\S+)\.\.\.$"),
        "Transcribing {name}…",
    ),
    (
        re.compile(r"^Completed (?P<name>\S+)$"),
        "Completed {name}",
    ),
    (
        re.compile(r"^FAILED (?P<name>\S+) during stage \[(?P<stage>[^\]]+)\]:"),
        "Failed {name} ({stage})",
    ),
]


def _parse_event(line: str) -> str | None:
    line = line.strip()
    if not line:
        return None
    for pattern, template in _EVENT_RULES:
        match = pattern.match(line)
        if match:
            return template.format(**match.groupdict())
    return None


def _append_event(job_id: str, message: str) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id]["events"].append(message)


def _update_job(job_id: str, **fields: Any) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(fields)


def _run_pipeline(job_id: str, env: dict[str, str], input_dir: Path, output_dir: Path) -> None:
    """
    Spawn the CLI as a subprocess with the client's keys in env. Read stdout
    line by line; convert each recognized line to a client-visible event.

    The raw stdout/stderr is forwarded to the server's own stdout (captured by
    the platform's log aggregator) for operator debugging, but is never
    returned to the client.
    """
    _append_event(job_id, "Started")
    _update_job(job_id, status="running")

    try:
        proc = subprocess.Popen(
            [
                sys.executable, str(PROJECT_ROOT / "main.py"), "run",
                "--media-path", str(input_dir),
                "--output-dir", str(output_dir),
            ],
            # PYTHONUNBUFFERED=1 disables stdout buffering in the child so its
            # print() calls reach our pipe immediately. Without this, the CLI's
            # output is block-buffered and only flushes at exit, so the event
            # log appears all at once at the end of a job instead of streaming.
            env={**os.environ, "PYTHONUNBUFFERED": "1", **env},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_ROOT),
            bufsize=1,
            encoding="utf-8",
            errors="replace",
        )

        # Drain stdout in this thread (we're already on a worker thread).
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            # Server-side trace for operators.
            print(f"[job {job_id}] {raw_line.rstrip()}", file=sys.stderr)
            event = _parse_event(raw_line)
            if event is not None:
                _append_event(job_id, event)

        return_code = proc.wait()
        outputs = sorted(p.name for p in output_dir.glob("*.srt"))
        status = "completed" if return_code == 0 else "failed"
        _update_job(job_id, status=status, outputs=outputs)
        _append_event(job_id, "Completed" if status == "completed" else "Failed")
    except Exception as e:
        # Generic event for the client; full exception lands in server logs.
        print(f"[job {job_id}] internal error: {e}", file=sys.stderr)
        _append_event(job_id, "Failed")
        _update_job(job_id, status="failed")


def _delete_job(job_id: str) -> None:
    """Remove all on-disk and in-memory state for a job. Safe to call twice."""
    with _jobs_lock:
        _jobs.pop(job_id, None)
    job_dir = JOBS_ROOT / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)


def _sweep_old_jobs() -> None:
    """
    Background sweeper that deletes job directories older than JOB_TTL_HOURS.
    Runs forever; dies with the process. Never raises, so a transient FS error
    doesn't kill the sweeper for the rest of the deploy.
    """
    cutoff_seconds = JOB_TTL_HOURS * 3600
    while True:
        try:
            cutoff = time.time() - cutoff_seconds
            for job_dir in JOBS_ROOT.iterdir():
                if not job_dir.is_dir():
                    continue
                try:
                    if job_dir.stat().st_mtime < cutoff:
                        _delete_job(job_dir.name)
                except OSError:
                    pass
        except Exception as e:
            print(f"[sweeper] {e}", file=sys.stderr)
        time.sleep(SWEEP_INTERVAL_SECONDS)


threading.Thread(target=_sweep_old_jobs, daemon=True).start()


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    return (PROJECT_ROOT / "templates" / "index.html").read_text(encoding="utf-8")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/jobs")
async def create_job(
    soniox_api_key: str = Form(...),
    tmdb_read_access_token: str = Form(...),
    anthropic_api_key: str = Form(...),
    files: list[UploadFile] = File(...),
) -> dict[str, str]:
    if not files:
        raise HTTPException(400, "No files provided")

    job_id = uuid.uuid4().hex
    job_dir = JOBS_ROOT / job_id
    input_dir = job_dir / "input"
    output_dir = job_dir / "output"
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    for upload in files:
        if not upload.filename:
            continue
        # Strip any directory components from the client-supplied name.
        target = input_dir / Path(upload.filename).name
        with open(target, "wb") as f:
            shutil.copyfileobj(upload.file, f)

    env = {
        "SONIOX_API_KEY": soniox_api_key,
        "TMDB_READ_ACCESS_TOKEN": tmdb_read_access_token,
        "ANTHROPIC_API_KEY": anthropic_api_key,
    }

    with _jobs_lock:
        _jobs[job_id] = {"status": "queued", "events": [], "outputs": []}

    threading.Thread(
        target=_run_pipeline,
        args=(job_id, env, input_dir, output_dir),
        daemon=True,
    ).start()

    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    _require_valid_job_id(job_id)
    with _jobs_lock:
        if job_id not in _jobs:
            raise HTTPException(404, "Job not found")
        # Shallow copy under the lock so the caller iterates a stable snapshot.
        return {
            "status": _jobs[job_id]["status"],
            "events": list(_jobs[job_id]["events"]),
            "outputs": list(_jobs[job_id]["outputs"]),
        }


@app.post("/api/jobs/{job_id}/delete")
async def delete_job(job_id: str) -> dict[str, str]:
    """
    Wipe a job's state and on-disk files. Used by the page-unload handler
    (via navigator.sendBeacon) and the manual reset button. Idempotent.
    """
    _require_valid_job_id(job_id)
    _delete_job(job_id)
    return {"deleted": job_id}


@app.get("/api/jobs/{job_id}/files/{filename}")
async def download_output(job_id: str, filename: str) -> FileResponse:
    _require_valid_job_id(job_id)
    safe_name = Path(filename).name
    file_path = JOBS_ROOT / job_id / "output" / safe_name
    if not file_path.exists():
        raise HTTPException(404, "File not found")
    return FileResponse(
        file_path,
        media_type="application/x-subrip",
        filename=safe_name,
    )
