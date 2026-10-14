"""
DesiCreator Backend — free video-processing API (ffmpeg).
Runs on Render free tier or Modal. Async job pattern:
  POST /process (upload) -> 202 + job_id -> GET /job/{id} (poll) -> GET /download/{id}
Env vars:
  BACKEND_API_KEY  - shared secret; frontend sends X-API-Key header (set on deploy)
  ALLOWED_ORIGINS  - comma-separated CORS origins (default: https://desicreator.vercel.app)
  MAX_UPLOAD_MB    - upload cap (default 100)
"""
import os
import uuid
import time
import shutil
import threading
import subprocess
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware

BASE = Path("/tmp/desicreator-jobs")
BASE.mkdir(parents=True, exist_ok=True)

API_KEY = os.environ.get("BACKEND_API_KEY", "")
ALLOWED_ORIGINS = [o.strip() for o in os.environ.get(
    "ALLOWED_ORIGINS", "https://desicreator.vercel.app").split(",") if o.strip()]
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "100"))

app = FastAPI(title="DesiCreator Backend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS + ["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs = {}  # job_id -> dict(status, progress, result, error, created)
jobs_lock = threading.Lock()

OPS = {"compress", "trim", "convert", "speed", "mute", "extract-audio"}


def check_key(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")


def build_cmd(op: str, src: Path, dst: Path, p: dict) -> list:
    if op == "compress":
        crf = p.get("crf", "28")
        return ["ffmpeg", "-y", "-i", str(src), "-vcodec", "libx264",
                "-crf", crf, "-preset", "veryfast", "-acodec", "aac", str(dst)]
    if op == "trim":
        start, end = p.get("start", "0"), p.get("end", "")
        cmd = ["ffmpeg", "-y", "-ss", start]
        if end:
            cmd += ["-to", end]
        return cmd + ["-i", str(src), "-c", "copy", str(dst)]
    if op == "convert":
        fmt = p.get("format", "mp4")
        return ["ffmpeg", "-y", "-i", str(src), str(dst.with_suffix("." + fmt))]
    if op == "speed":
        s = float(p.get("speed", "1"))
        return ["ffmpeg", "-y", "-i", str(src),
                "-filter:v", f"setpts={1/s}*PTS", "-filter:a", f"atempo={s}", str(dst)]
    if op == "mute":
        return ["ffmpeg", "-y", "-i", str(src), "-an", "-c:v", "copy", str(dst)]
    if op == "extract-audio":
        return ["ffmpeg", "-y", "-i", str(src), "-vn", "-acodec", "libmp3lame", str(dst)]
    raise ValueError("unknown op")


def run_job(job_id: str, op: str, src: Path, dst: Path, params: dict):
    try:
        with jobs_lock:
            jobs[job_id]["status"] = "processing"
        cmd = build_cmd(op, src, dst, params)
        # fix convert suffix
        if op == "convert":
            dst = dst.with_suffix("." + params.get("format", "mp4"))
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr[-2000:])
        # cleanup upload, keep result
        try:
            src.unlink(missing_ok=True)
        except OSError:
            pass
        with jobs_lock:
            jobs[job_id].update(status="done", result=str(dst))
    except Exception as e:  # noqa: BLE001
        with jobs_lock:
            jobs[job_id].update(status="failed", error=str(e)[:500])


@app.get("/health")
def health():
    return {"ok": True, "ops": sorted(OPS)}


@app.post("/process", status_code=202)
async def process(
    file: UploadFile = File(...),
    op: str = Form(...),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    start: str = Form(default="0"),
    end: str = Form(default=""),
    format: str = Form(default="mp4"),  # noqa: A002
    speed: str = Form(default="1"),
    crf: str = Form(default="28"),
):
    check_key(x_api_key)
    if op not in OPS:
        raise HTTPException(400, f"unknown op '{op}'")
    data = await file.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"max {MAX_UPLOAD_MB}MB")
    if len(data) == 0:
        raise HTTPException(400, "empty file")

    job_id = uuid.uuid4().hex[:12]
    workdir = BASE / job_id
    workdir.mkdir(parents=True, exist_ok=True)
    src = workdir / ("in_" + (file.filename or "video").replace("/", "_")[-60:])
    src.write_bytes(data)
    ext = ".mp3" if op == "extract-audio" else ".mp4"
    dst = workdir / ("out" + ext)

    params = {"start": start, "end": end, "format": format, "speed": speed, "crf": crf}
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "op": op, "created": time.time()}
    t = threading.Thread(target=run_job, args=(job_id, op, src, dst, params), daemon=True)
    t.start()
    return {"job_id": job_id, "status": "queued"}


@app.get("/job/{job_id}")
def job_status(job_id: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    check_key(x_api_key)
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    out = {"job_id": job_id, "status": job["status"]}
    if job["status"] == "failed":
        out["error"] = job.get("error", "")
    return out


@app.get("/download/{job_id}")
def download(job_id: str, x_api_key: str | None = Header(default=None, alias="X-API-Key")):
    check_key(x_api_key)
    with jobs_lock:
        job = jobs.get(job_id)
    if not job or job["status"] != "done":
        raise HTTPException(404 if not job else 409, "not ready")
    path = Path(job["result"])
    if not path.exists():
        raise HTTPException(410, "expired")

    def cleanup():
        time.sleep(5)
        shutil.rmtree(BASE / job_id, ignore_errors=True)
        with jobs_lock:
            jobs.pop(job_id, None)

    threading.Thread(target=cleanup, daemon=True).start()
    return FileResponse(path, filename="desicreator-output" + path.suffix)
