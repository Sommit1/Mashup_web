import os
import threading

from flask import Flask, render_template, request
from email_validator import validate_email, EmailNotValidError

from tasks import create_mashup_and_email

app = Flask(__name__)

@app.route("/", methods=["GET", "POST"])
def index():
    msg = ""
    error = ""

    if request.method == "POST":
        singer = request.form.get("singer", "").strip()
        n = request.form.get("n", "").strip()
        y = request.form.get("y", "").strip()
        email = request.form.get("email", "").strip()

        # ---- validations ----
        if not singer:
            error = "Singer name is required."
            return render_template("index.html", msg=msg, error=error)

        try:
            n_int = int(n)
            y_int = int(y)
            if n_int < 1:
                raise ValueError
            if y_int < 1:
                raise ValueError
        except Exception:
            error = "Please enter valid numbers. (#videos >= 1, duration >= 1 sec)"
            return render_template("index.html", msg=msg, error=error)

        try:
            validate_email(email)
        except EmailNotValidError:
            error = "Invalid Email ID. Please enter a correct email."
            return render_template("index.html", msg=msg, error=error)

        # ---- run task in background thread (FREE, no worker needed) ----
        t = threading.Thread(
            target=create_mashup_and_email,
            args=(singer, n_int, y_int, email),
            daemon=True
        )
        t.start()

        msg = "✅ Request received! Your mashup is being prepared and will be emailed as a ZIP file."
        return render_template("index.html", msg=msg, error="")

    return render_template("index.html", msg=msg, error=error)


if __name__ == "__main__":
    # For local run only
    app.run(host="0.0.0.0", port=5000, debug=True)
