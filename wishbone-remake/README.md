# Wishbone (simple edition)

A this-or-that voting app: accounts, photo options, light pink and warm brown.
Just three files that matter: `app.py`, `index.html`, `requirements.txt`.

## Run it
Open this folder in VS Code, open the terminal, then:

    python3 -m venv .venv
    source .venv/bin/activate          (Windows: .venv\Scripts\activate)
    pip install -r requirements.txt
    python app.py

Open http://localhost:5001

To try voting, make two accounts (use a private window for the second one).
You can't vote on your own polls.

Your data is created automatically next to the code:
`wishbone.db` (accounts, polls, votes) and `uploads/` (pictures). Delete both to reset.

## Put it online (no Docker)
**Easiest, keeps your data: PythonAnywhere (free tier).**
1. Create an account at pythonanywhere.com, open a Bash console, and run
   `git clone https://github.com/YOU/YOUR-REPO.git`
2. Web tab > Add a new web app > Flask > Python 3.10+, and point it at `app.py`
   in your cloned folder (the WSGI file should import `app` from `app`).
3. In the console: `pip install --user flask`, then reload the web app.

**Alternative: Render.com** (connect your GitHub repo, type: Web Service).
Build command `pip install -r requirements.txt`, start command `gunicorn app:app`.
Note: Render's free disk is wiped on every redeploy/restart, so accounts and
pictures disappear. Fine for a demo, not for real use.

Set a `SECRET_KEY` environment variable on any host (any long random text).

## Pictures
JPG, PNG, WebP and GIF are accepted (checked by file contents, not filename).
Pictures are shown cropped to portrait with CSS; large photos are stored as-is.
