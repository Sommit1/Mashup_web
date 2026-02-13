import os
import re
import time
import uuid
import base64
import zipfile
import tempfile
import shutil
from pathlib import Path

from flask import Flask, render_template, request, send_file
from email_validator import validate_email, EmailNotValidError

import yt_dlp
from pydub import AudioSegment

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import (
    Mail, Attachment, FileContent, FileName, FileType, Disposition
)

app = Flask(__name__)

# ----------------------------
# Download cache (Render uses ephemeral disk; links expire)
# ----------------------------
DOWNLOAD_CACHE_DIR = Path("/tmp/mashup_cache")
DOWNLOAD_CACHE_DIR.mkdir(parents=True, exist_ok=True)

DOWNLOAD_TTL_SECONDS = 20 * 60  # 20 minutes
DOWNLOADS = {}  # token -> {"path": Path, "created": ts, "filename": str}


def cleanup_downloads():
    now = time.time()
    expired = [t for t, meta in DOWNLOADS.items() if now - meta["created"] > DOWNLOAD_TTL_SECONDS]
    for t in expired:
        try:
            p = DOWNLOADS[t]["path"]
            if p.exists():
                p.unlink()
        except:
            pass
        DOWNLOADS.pop(t, None)


def safe_int(x, default=0):
    try:
        return int(x)
    except:
        return default


def sanitize_filename(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9._-]+", "_", name).strip("_")
    return name or "mashup"


def _yt_dlp_opts(out_dir: Path):
    """
    yt-dlp options tuned for server stability.
    Note: YouTube may still block cloud IPs sometimes.
    """
    outtmpl = str(out_dir / "%(id)s.%(ext)s")
    return {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "socket_timeout": 20,
        # Helps in some cases (not guaranteed)
        "extractor_args": {"youtube": {"player_client": ["android"]}},
        "http_headers": {"User-Agent": "Mozilla/5.0"},
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }


def download_n_audios_by_search(singer: str, n: int, out_dir: Path):
    """
    Uses ytsearchN to download top N results.
    """
    search = f"ytsearch{n}:{singer} official audio"
    with yt_dlp.YoutubeDL(_yt_dlp_opts(out_dir)) as ydl:
        ydl.download([search])

    return sorted(out_dir.glob("*.mp3"))


def build_mashup(mp3_files, seconds_each: int, out_mp3: Path):
    merged = AudioSegment.silent(duration=0)
    clip_ms = max(1, seconds_each) * 1000

    for f in mp3_files:
        audio = AudioSegment.from_file(f)
        merged += audio[:clip_ms]

    merged.export(out_mp3, format="mp3")


def make_zip(file_path: Path, zip_path: Path):
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, arcname=file_path.name)


def send_zip_via_sendgrid(to_email: str, zip_path: Path):
    """
    Requires env vars:
    - SENDGRID_API_KEY
    - FROM_EMAIL   (must be verified in SendGrid: Single Sender or Domain Auth)
    """
    api_key = os.getenv("SENDGRID_API_KEY", "").strip()
    from_email = os.getenv("FROM_EMAIL", "").strip()
    if not api_key or not from_email:
        raise RuntimeError("SendGrid not configured (SENDGRID_API_KEY / FROM_EMAIL missing).")

    with open(zip_path, "rb") as f:
        data = f.read()

    encoded = base64.b64encode(data).decode()

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject="Mashup ZIP file",
        html_content="<p>Your mashup ZIP is attached.</p>"
    )

    attachment = Attachment(
        FileContent(encoded),
        FileName(zip_path.name),
        FileType("application/zip"),
        Disposition("attachment"),
    )
    message.attachment = attachment

    sg = SendGridAPIClient(api_key)
    sg.send(message)


def cache_zip_for_download(zip_path: Path, display_name: str):
    """
    Copy zip into /tmp cache and return token.
    """
    cleanup_downloads()
    token = uuid.uuid4().hex
    target = DOWNLOAD_CACHE_DIR / f"{token}.zip"
    target.write_bytes(zip_path.read_bytes())
    DOWNLOADS[token] = {"path": target, "created": time.time(), "filename": display_name}
    return token


@app.route("/", methods=["GET", "POST"])
def index():
    cleanup_downloads()

    if request.method == "GET":
        return render_template("index.html")

    singer = (request.form.get("singer") or "").strip()
    n = safe_int(request.form.get("n"), 0)
    y = safe_int(request.form.get("y"), 0)
    email = (request.form.get("email") or "").strip()

    # ---- Assignment validations ----
    if not singer:
        return render_template("index.html", error="Singer name is required.")
    if n <= 0 or n > 20:
        return render_template("index.html", error="Number of videos (n) must be between 1 and 20.")
    if y <= 0 or y > 60:
        return render_template("index.html", error="Duration (y) must be between 1 and 60 seconds.")
    if not email:
        return render_template("index.html", error="Email is required (as per assignment).")
    try:
        validate_email(email)
    except EmailNotValidError:
        return render_template("index.html", error="Invalid email format. Please enter a correct email.")

    tmp_root = Path(tempfile.mkdtemp(prefix="mashup_"))

    try:
        dl_dir = tmp_root / "downloads"
        dl_dir.mkdir(parents=True, exist_ok=True)

        # 1) Download audio
        try:
            mp3s = download_n_audios_by_search(singer, n, dl_dir)
        except Exception as e:
            return render_template(
                "index.html",
                error=(
                    "YouTube blocked downloads on the server (bot/sign-in check). "
                    "This is common on cloud hosting. Try smaller n (5–10), or demo locally. "
                    f"Details: {str(e)[:240]}"
                )
            )

        if not mp3s:
            return render_template("index.html", error="No audios downloaded. Try another singer keyword.")

        # 2) Build mashup mp3
        base = sanitize_filename(singer)
        out_mp3 = tmp_root / f"{base}_mashup.mp3"
        build_mashup(mp3s, y, out_mp3)

        # 3) Make ZIP
        out_zip = tmp_root / f"{base}_mashup.zip"
        make_zip(out_mp3, out_zip)

        # 4) Backup download link (always)
        token = cache_zip_for_download(out_zip, out_zip.name)
        download_url = f"/download/{token}"

        # 5) Email ZIP
        try:
            send_zip_via_sendgrid(email, out_zip)
            return render_template(
                "index.html",
                success=f"✅ ZIP sent to: {email}",
                download_url=download_url
            )
        except Exception as e:
            # Email failed -> still give download link
            return render_template(
                "index.html",
                error=f"Email failed, but ZIP is ready. Download below. Reason: {str(e)[:240]}",
                download_url=download_url
            )

    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


@app.route("/download/<token>")
def download(token):
    cleanup_downloads()
    meta = DOWNLOADS.get(token)
    if not meta:
        return "Download link expired or invalid. Please generate again.", 404

    p = meta["path"]
    if not p.exists():
        return "File not found (server restarted). Please generate again.", 404

    return send_file(
        p,
        as_attachment=True,
        download_name=meta["filename"],
        mimetype="application/zip"
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, debug=True)
