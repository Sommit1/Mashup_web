import os
import re
import uuid
import shutil
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

from flask import Flask, render_template, request, url_for, send_from_directory

import yt_dlp
from pydub import AudioSegment

# Optional email (SendGrid)
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
except Exception:
    SendGridAPIClient = None
    Mail = None


app = Flask(__name__)

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# Delete old files to save disk
FILE_TTL_MINUTES = 30


def safe_filename(text: str) -> str:
    text = text.strip()
    text = re.sub(r"[^\w\-]+", "_", text)
    return text[:60] if text else "mashup"


def cleanup_old_files():
    now = datetime.utcnow()
    for f in STATIC_DIR.glob("*.mp3"):
        try:
            mtime = datetime.utcfromtimestamp(f.stat().st_mtime)
            if now - mtime > timedelta(minutes=FILE_TTL_MINUTES):
                f.unlink(missing_ok=True)
        except Exception:
            pass


def search_youtube_urls(query: str, n: int) -> list[str]:
    """
    Uses yt-dlp "ytsearchN:" to fetch top N video URLs.
    """
    search_term = f"ytsearch{n}:{query} songs audio"
    ydl_opts = {
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "noplaylist": True,
        # Helps reduce JS runtime issues in many cases
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    urls = []
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(search_term, download=False)
        entries = info.get("entries", []) if info else []
        for e in entries:
            if not e:
                continue
            # Some entries already contain url, some contain id
            if "url" in e and str(e["url"]).startswith("http"):
                urls.append(e["url"])
            elif "id" in e:
                urls.append(f"https://www.youtube.com/watch?v={e['id']}")
    return urls


def download_audio_as_mp3(url: str, out_dir: Path) -> Path:
    """
    Downloads best audio and converts to MP3 via ffmpeg.
    Returns the mp3 path.
    """
    outtmpl = str(out_dir / "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"},
        ],
        # Helps reduce JS runtime issues in many cases
        "extractor_args": {"youtube": {"player_client": ["android"]}},
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        vid = info.get("id")
        # After postprocess, it becomes .mp3
        mp3_path = out_dir / f"{vid}.mp3"
        return mp3_path


def create_mashup(singer: str, n: int, y_seconds: int, output_name: str) -> Path:
    """
    Creates mashup MP3 under /static and returns final path.
    """
    cleanup_old_files()

    if n < 1 or n > 30:
        raise ValueError("n must be between 1 and 30")
    if y_seconds < 5 or y_seconds > 90:
        raise ValueError("y must be between 5 and 90 seconds")

    # unique output
    slug = safe_filename(output_name or singer)
    unique = uuid.uuid4().hex[:8]
    final_filename = f"{slug}_{unique}.mp3"
    final_path = STATIC_DIR / final_filename

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        urls = search_youtube_urls(singer, n)
        if not urls:
            raise RuntimeError("No YouTube results found. Try another singer name.")

        clips = []
        for i, url in enumerate(urls, start=1):
            mp3 = download_audio_as_mp3(url, tmp_dir)
            if not mp3.exists():
                continue

            audio = AudioSegment.from_file(mp3)
            clip = audio[: y_seconds * 1000]  # first y seconds
            clips.append(clip)

        if not clips:
            raise RuntimeError("Failed to download audio clips. Try smaller n or different singer.")

        mashup = clips[0]
        for c in clips[1:]:
            mashup += c

        mashup.export(final_path, format="mp3")
        return final_path


def try_send_email(to_email: str, download_url: str) -> tuple[bool, str]:
    """
    Attempts SendGrid email if configured. Returns (success, message).
    If not configured or fails, returns (False, reason).
    """
    api_key = os.environ.get("SENDGRID_API_KEY", "").strip()
    from_email = os.environ.get("FROM_EMAIL", "").strip()

    if not api_key or not from_email or SendGridAPIClient is None or Mail is None:
        return False, "Email not configured (SENDGRID_API_KEY / FROM_EMAIL missing)."

    try:
        sg = SendGridAPIClient(api_key)
        subject = "Your Mashup is Ready ✅"
        body = f"Your mashup has been generated.\n\nDownload here: {download_url}\n\n(If link expires, generate again.)"

        message = Mail(
            from_email=from_email,
            to_emails=to_email,
            subject=subject,
            plain_text_content=body,
        )
        sg.send(message)
        return True, "Email sent successfully ✅"
    except Exception as e:
        return False, f"Email sending failed: {e}"


@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template("index.html", message=None, download_url=None)

    singer = (request.form.get("singer") or "").strip()
    n = (request.form.get("n") or "").strip()
    y = (request.form.get("y") or "").strip()
    email = (request.form.get("email") or "").strip()

    try:
        if not singer:
            raise ValueError("Singer name is required.")
        n_int = int(n)
        y_int = int(y)

        # output file base name
        out_base = f"{singer}_mashup"

        final_path = create_mashup(singer, n_int, y_int, out_base)
        download_url = url_for("download_file", filename=final_path.name, _external=True)

        # Email is OPTIONAL; if fails, we still show download link
        msg_parts = [f"Done ✅ Mashup created."]

        if email:
            ok, emsg = try_send_email(email, download_url)
            msg_parts.append(emsg if ok else emsg + " (use download link below)")

        return render_template("index.html", message=" | ".join(msg_parts), download_url=download_url)

    except Exception as e:
        return render_template("index.html", message=f"Error: {e}", download_url=None), 400


@app.route("/download/<filename>")
def download_file(filename):
    return send_from_directory(STATIC_DIR, filename, as_attachment=True)


if __name__ == "__main__":
    # Local run
    app.run(host="0.0.0.0", port=5000, debug=True)
