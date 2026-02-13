import os, base64, tempfile, shutil, zipfile
from pathlib import Path
import yt_dlp
from pydub import AudioSegment
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

def download_audios(query, n, download_dir):
    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "default_search": "ytsearch",
        "outtmpl": str(download_dir / "%(title).200s.%(ext)s"),
        "format": "bestaudio/best",
        "ignoreerrors": True,
        "postprocessors": [{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"192"}],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.extract_info(f"ytsearch{n}:{query}", download=True)
    return sorted(download_dir.glob("*.mp3"))

def trim_and_merge(mp3_files, y_seconds, out_mp3):
    limit_ms = y_seconds * 1000
    combined = AudioSegment.empty()
    ok = 0
    for f in mp3_files:
        try:
            audio = AudioSegment.from_file(f)
            combined += audio[:limit_ms]
            ok += 1
        except:
            pass
    if ok == 0:
        raise RuntimeError("No valid audio could be processed.")
    combined.export(out_mp3, format="mp3", bitrate="192k")

def send_zip(to_email, zip_path):
    sg = SendGridAPIClient(os.environ["SENDGRID_API_KEY"])
    from_email = os.environ["FROM_EMAIL"]

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject="Your Mashup ZIP",
        html_content="Attached is your mashup output ZIP."
    )

    data = zip_path.read_bytes()
    encoded = base64.b64encode(data).decode()

    attachment = Attachment(
        FileContent(encoded),
        FileName(zip_path.name),
        FileType("application/zip"),
        Disposition("attachment"),
    )
    message.attachment = attachment
    sg.send(message)

def create_mashup_and_email(singer, n, y, email):
    work = Path(tempfile.mkdtemp(prefix="mashup_"))
    try:
        ddir = work / "downloads"
        ddir.mkdir(parents=True, exist_ok=True)

        mp3s = download_audios(singer, n, ddir)

        out_mp3 = work / "mashup.mp3"
        trim_and_merge(mp3s, y, out_mp3)

        zip_path = work / "result.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
            z.write(out_mp3, arcname="mashup.mp3")

        send_zip(email, zip_path)
        return "sent"
    finally:
        shutil.rmtree(work, ignore_errors=True)
