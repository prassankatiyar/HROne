# Candidate kit

Read in this order:

1. `PROBLEM_STATEMENT.docx`: what you are asked to do, how it is evaluated.
2. `openapi.yaml`: the API contract and the business rules (R1 - R10).
3. `DATA_MODEL.md`: what is stored in MongoDB, with nine sample documents.
4. `app/main.py`: the starter. Put **all** your code in this one file. It must start with `uvicorn app.main:app --port 8000`.
5. `REVIEW.md`, `DECISIONS.md`: templates to fill in.

`sample_data/` holds the nine sample documents as MongoDB Extended JSON; `sample_seed.py` loads them. The grader's dataset is
much larger and contains cases they do not show.

## Local setup
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set MONGO_URI / MONGO_DB (local docker: docker run -p 27017:27017 mongo:7)
python sample_seed.py
uvicorn app.main:app --port 8000 --reload
```
MongoDB **6.0 or newer** is required (the grader uses 7.0). MongoDB Atlas free tier (M0) works.

## Submit
A public Git repository with `app/main.py`, `requirements.txt`, `REVIEW.md`, `DECISIONS.md` and a short `README.md`.
No `.env`, no secrets, no Dockerfile.


## Completed project

This is an employee attendance API built with FastAPI and MongoDB. It supports employee creation, punch-in and punch-out, attendance corrections, monthly reports, department summaries, late rankings and daily trends.

The starter text above is kept as supplied. Use the setup below for this version. The original code is also kept in `app/main.py`; the corrected definitions and final FastAPI app are added after it. Uvicorn loads that final app. This leaves some duplicate definitions in the file, which is a tradeoff of keeping the boilerplate intact.

### Setup on Windows

Run these commands from the project folder. If `.venv` already exists, skip the first command.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Use MongoDB 6.0 or newer. MongoDB Atlas works, so a local MongoDB installation or Docker is not needed when using Atlas.

Create a local `.env` file with `MONGO_URI` set to your connection string and `MONGO_DB` set to your database name. The included `.env.example` contains placeholders. If your `.env` is already configured, keep it. Real environment variables take priority over `.env`.

For Atlas, allow your current public IP in the project's IP access list and use a database user with access to the database. Then start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

The assignment's `uvicorn app.main:app --port 8000` command also works after activating the virtual environment. Indexes are created automatically at startup.

- Swagger: http://127.0.0.1:8000/docs
- Health check: http://127.0.0.1:8000/health

A successful health check returns `{"status":"ok"}`. A database connection problem returns 503. There is no home page, so opening `/` returns 404.

### A few implementation notes

Instants are stored as UTC BSON dates and returned as integer epoch milliseconds. Shift times and attendance dates use IST. Milliseconds are truncated before calculations, and decimal values use half-up rounding.

Analytics use the stored derived values. Department headcount includes employees with no attendance logs. The trend calendar is generated inside MongoDB, including missing dates and weekends, and its moving average uses `$setWindowFields`.

For a punch-out earlier than every existing punch-in, this implementation returns 422. If the employee has no punch-in at all, it returns 404.

Sample data is optional. The supplied `sample_seed.py` deletes existing employee and attendance documents before loading the samples, so only run it against a disposable database.

Do not commit `.env`, database credentials, `.venv`, or a Dockerfile. The example configuration must contain placeholders only.
