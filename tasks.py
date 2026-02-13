import os
import shutil
import tempfile
from pathlib import Path
import zipfile

import yt_dlp
from pydub import AudioSegment
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Attachment, FileContent, FileName, FileType, Disposition

import base64


def _download_n_audios(singer: str, n: int, download_dir: Path) -> list[Path]:
    """
    Downloads N YouTube videos (via search) and extracts audio as mp3 into download_dir.
    Returns list of mp3 file paths.
    """
    query = f"ytsearch{n}:{singer} official song"
    outtmpl = str(download_dir / "%(title).80s.%(ext)s")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "noplaylist": True,
        "extract_flat": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }

    mp3_files_before = set(download_dir.glob("*.mp3"))

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([query])

    mp3_files_after = set(download_dir.glob("*.mp3"))
    new_files = sorted(list(mp3_files_after - mp3_files_before))
    return new_files


def _trim_first_y_seconds(audio_path: Path, y_seconds: int, out_dir: Path) -> Path:
    """
    Trim first y_seconds from audio_path, save to out_dir, return trimmed file path.
    """
    audio = AudioSegment.from_file(audio_path)
    clip = audio[: y_seconds * 1000]  # ms
    out_path = out_dir / f"trim_{audio_path.stem}.mp3"
    clip.export(out_path, format="mp3")
    return out_path


def _merge_audios(audio_paths: list[Path], output_path: Path) -> None:
    merged = AudioSegment.empty()
    for p in audio_paths:
        merged += AudioSegment.from_file(p)
    merged.export(output_path, format="mp3")


def _zip_file(file_path: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(file_path, arcname=file_path.name)


def _send_email_with_zip(to_email: str, zip_path: Path) -> None:
    api_key = os.environ.get("SENDGRID_API_KEY", "")
    from_email = os.environ.get("FROM_EMAIL", "")

    if not api_key or not from_email:
        raise RuntimeError("Missing SENDGRID_API_KEY or FROM_EMAIL in environment variables")

    subject = "Your Mashup ZIP File"
    body = "Hi,\n\nYour mashup has been generated successfully. Please find the ZIP attached.\n\nThanks!"

    message = Mail(
        from_email=from_email,
        to_emails=to_email,
        subject=subject,
        plain_text_content=body,
    )

    # Attach ZIP
    encoded = base64.b64encode(zip_path.read_bytes()).decode()
    attachment = Attachment(
        FileContent(encoded),
        FileName(zip_path.name),
        FileType("application/zip"),
        Disposition("attachment"),
    )
    message.attachment = attachment

    sg = SendGridAPIClient(api_key)
    sg.send(message)


def create_mashup_and_email(singer: str, n: int, y: int, email: str) -> None:
    """
    Full pipeline:
    1) Download N audios for singer
    2) Trim first Y seconds
    3) Merge into one mp3
    4) Zip it
    5) Email zip
    """
    temp_root = Path(tempfile.mkdtemp(prefix="mashup_"))
    try:
        downloads = temp_root / "downloads"
        trims = temp_root / "trims"
        downloads.mkdir(parents=True, exist_ok=True)
        trims.mkdir(parents=True, exist_ok=True)

        # 1) download
        audio_files = _download_n_audios(singer, n, downloads)
        if not audio_files:
            raise RuntimeError("No audio files downloaded. Try a different singer name.")

        # 2) trim
        trimmed_files = []
        for f in audio_files:
            trimmed_files.append(_trim_first_y_seconds(f, y, trims))

        # 3) merge
        output_mp3 = temp_root / "mashup-output.mp3"
        _merge_audios(trimmed_files, output_mp3)

        # 4) zip
        output_zip = temp_root / "mashup-output.zip"
        _zip_file(output_mp3, output_zip)

        # 5) email
        _send_email_with_zip(email, output_zip)

    except Exception as e:
        # Optional: print error for Render logs
        print("Mashup Error:", str(e))
        raise
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)
