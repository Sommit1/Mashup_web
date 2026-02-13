import os
from flask import Flask, render_template, request
from email_validator import validate_email, EmailNotValidError
from redis import Redis
from rq import Queue

from tasks import create_mashup_and_email

app = Flask(__name__)

redis_url = os.environ.get("REDIS_URL")
if not redis_url:
    raise RuntimeError("REDIS_URL not set in environment variables")

q = Queue(connection=Redis.from_url(redis_url))


@app.route("/", methods=["GET","POST"])
def index():
    if request.method == "POST":
        singer = request.form.get("singer","").strip()
        n = request.form.get("n","").strip()
        y = request.form.get("y","").strip()
        email = request.form.get("email","").strip()

        try:
            validate_email(email)
        except EmailNotValidError:
            return render_template("index.html", msg="Invalid email.")

        if not singer:
            return render_template("index.html", msg="Singer name cannot be empty.")

        try:
            n = int(n); y = int(y)
        except:
            return render_template("index.html", msg="N and Y must be integers.")

        if n <= 10:
            return render_template("index.html", msg="N must be > 10.")
        if y <= 20:
            return render_template("index.html", msg="Y must be > 20 seconds.")

        job = q.enqueue(create_mashup_and_email, singer, n, y, email)
        return render_template("index.html", msg=f"Submitted ✅ Job ID: {job.id}. Check email soon.")

    return render_template("index.html", msg=None)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
