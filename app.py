import os
import uuid
import tempfile
from pathlib import Path

from flask import Flask, render_template, request, send_from_directory, url_for
from pydub import AudioSegment

app = Flask(__name__)

# Where outputs will be stored in the container
OUT_DIR = Path("outputs")
OUT_DIR.mkdir(exist_ok=True)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "GET":
        return render_template("index.html", error=None, download_url=None)

    try:
        y = int(request.form.get("y", "20"))
        y = max(1, min(y, 120))  # safety clamp

        outname = (request.form.get("outname") or "102303184-output.mp3").strip()
        if not outname.lower().endswith(".mp3"):
            outname += ".mp3"

        files = request.files.getlist("files")
        if not files or files[0].filename == "":
            return render_template("index.html", error="Please upload at least 1 audio file.", download_url=None)

        # Temporary workspace
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            clips = []

            for f in files:
                # Save upload
                safe_name = f"{uuid.uuid4().hex}_{Path(f.filename).name}"
                in_path = tmpdir / safe_name
                f.save(in_path)

                # Load and cut first y seconds
                audio = AudioSegment.from_file(in_path)
                clip = audio[: y * 1000]
                clips.append(clip)

            # Merge
            merged = clips[0]
            for c in clips[1:]:
                merged += c

            # Unique output path (avoid collisions)
            final_name = f"{uuid.uuid4().hex}_{outname}"
            out_path = OUT_DIR / final_name
            merged.export(out_path, format="mp3", bitrate="192k")

        download_url = url_for("download_file", filename=final_name)
        return render_template("index.html", error=None, download_url=download_url)

    except Exception as e:
        return render_template("index.html", error=str(e), download_url=None)

@app.route("/download/<path:filename>")
def download_file(filename):
    return send_from_directory(OUT_DIR, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
