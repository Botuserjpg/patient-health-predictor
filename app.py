"""Flask application for managing patient blood-test records."""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from dotenv import load_dotenv
from flask import Flask, Response, jsonify, render_template, request

from ai_service import predict_health_risk

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")
LAB_BOUNDS = {"glucose": (1, 2000), "haemoglobin": (0.1, 40), "cholesterol": (1, 2000)}
EMAIL_LOCAL = r"A-Z0-9!#$%&'*+/=?^_`{|}~-"
EMAIL_RE = re.compile(
    rf"^[{EMAIL_LOCAL}]+(?:\.[{EMAIL_LOCAL}]+)*@[A-Z0-9](?:[A-Z0-9-]{{0,61}}[A-Z0-9])?(?:\.[A-Z0-9](?:[A-Z0-9-]{{0,61}}[A-Z0-9])?)+$",
    re.I,
)
DUPLICATE_MESSAGE = "A patient with this email and date of birth already exists."


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(DATABASE=os.environ.get("DATABASE_PATH", str(BASE_DIR / "patients.db")))
    if test_config:
        app.config.update(test_config)
    init_db(app)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/patients")
    def list_patients():
        query = request.args.get("q", "").strip()
        with connect_db(app) as db:
            rows = db.execute(
                "SELECT * FROM patients WHERE name LIKE ? OR email LIKE ? ORDER BY created_at DESC, id DESC",
                (f"%{query}%", f"%{query}%"),
            ).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.get("/api/patients/<int:patient_id>")
    def get_patient(patient_id: int):
        with connect_db(app) as db:
            row = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        return (jsonify(dict(row)), 200) if row else api_error({"id": "Patient not found."}, 404)

    @app.post("/api/patients")
    def create_patient():
        values, errors = validate_patient(request.get_json(silent=True))
        if errors:
            return api_error(errors, 400)
        with connect_db(app) as db:
            duplicate = db.execute(
                "SELECT id FROM patients WHERE email = ? AND dob = ?",
                (values["email"], values["dob"]),
            ).fetchone()
        if duplicate:
            return api_error({"email": DUPLICATE_MESSAGE}, 409)

        remarks = get_prediction(values, app)
        now = timestamp()
        with connect_db(app) as db:
            duplicate = db.execute(
                "SELECT id FROM patients WHERE email = ? AND dob = ?",
                (values["email"], values["dob"]),
            ).fetchone()
            if duplicate:
                return api_error({"email": DUPLICATE_MESSAGE}, 409)
            try:
                cursor = db.execute(
                    "INSERT INTO patients (name,dob,email,glucose,haemoglobin,cholesterol,remarks,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                    (*patient_values(values), remarks, now, now),
                )
            except sqlite3.IntegrityError:
                return api_error({"email": DUPLICATE_MESSAGE}, 409)
            row = db.execute("SELECT * FROM patients WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return jsonify(dict(row)), 201

    @app.put("/api/patients/<int:patient_id>")
    def update_patient(patient_id: int):
        values, errors = validate_patient(request.get_json(silent=True))
        if errors:
            return api_error(errors, 400)
        with connect_db(app) as db:
            old = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
            if not old:
                return api_error({"id": "Patient not found."}, 404)
            duplicate = db.execute(
                "SELECT id FROM patients WHERE email = ? AND dob = ? AND id != ?",
                (values["email"], values["dob"], patient_id),
            ).fetchone()
            if duplicate:
                return api_error({"email": DUPLICATE_MESSAGE}, 409)
            old = dict(old)
            changed = any(
                str(old[key]) != str(values[key])
                for key in ("dob", "glucose", "haemoglobin", "cholesterol")
            )
        remarks = get_prediction(values, app) if changed else old["remarks"]
        with connect_db(app) as db:
            current = db.execute(
                "SELECT updated_at FROM patients WHERE id = ?", (patient_id,)
            ).fetchone()
            if not current:
                return api_error({"id": "Patient not found."}, 404)
            if current["updated_at"] != old["updated_at"]:
                return api_error(
                    {
                        "record": "This patient changed while you were editing. Reload and try again."
                    },
                    409,
                )
            duplicate = db.execute(
                "SELECT id FROM patients WHERE email = ? AND dob = ? AND id != ?",
                (values["email"], values["dob"], patient_id),
            ).fetchone()
            if duplicate:
                return api_error({"email": DUPLICATE_MESSAGE}, 409)
            try:
                db.execute(
                    "UPDATE patients SET name=?,dob=?,email=?,glucose=?,haemoglobin=?,cholesterol=?,remarks=?,updated_at=? WHERE id=?",
                    (*patient_values(values), remarks, timestamp(), patient_id),
                )
            except sqlite3.IntegrityError:
                return api_error({"email": DUPLICATE_MESSAGE}, 409)
            row = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
        return jsonify(dict(row))

    @app.post("/api/patients/<int:patient_id>/prediction")
    def rerun_prediction(patient_id: int):
        with connect_db(app) as db:
            row = db.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone()
            if not row:
                return api_error({"id": "Patient not found."}, 404)
            patient = dict(row)
        remarks = get_prediction(patient, app)
        with connect_db(app) as db:
            cursor = db.execute(
                "UPDATE patients SET remarks=?, updated_at=? WHERE id=? AND updated_at=?",
                (remarks, timestamp(), patient_id, patient["updated_at"]),
            )
            if cursor.rowcount == 0:
                exists = db.execute(
                    "SELECT id FROM patients WHERE id = ?", (patient_id,)
                ).fetchone()
                if not exists:
                    return api_error({"id": "Patient not found."}, 404)
                return api_error(
                    {"record": "This patient changed while the prediction was running. Rerun it."},
                    409,
                )
            updated = db.execute("SELECT * FROM patients WHERE id=?", (patient_id,)).fetchone()
        return jsonify(dict(updated))

    @app.delete("/api/patients/<int:patient_id>")
    def delete_patient(patient_id: int):
        with connect_db(app) as db:
            cursor = db.execute("DELETE FROM patients WHERE id = ?", (patient_id,))
        if not cursor.rowcount:
            return api_error({"id": "Patient not found."}, 404)
        return jsonify({"message": "Patient deleted."}), 200

    return app


@contextmanager
def connect_db(app: Flask) -> Iterator[sqlite3.Connection]:
    database_path = app.config["DATABASE"]
    if database_path != ":memory:":
        Path(database_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database_path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db(app: Flask) -> None:
    with connect_db(app) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                dob TEXT NOT NULL,
                email TEXT NOT NULL,
                glucose REAL NOT NULL,
                haemoglobin REAL NOT NULL,
                cholesterol REAL NOT NULL,
                remarks TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        try:
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_patients_email_dob ON patients (lower(email), dob)"
            )
        except sqlite3.IntegrityError:
            app.logger.warning(
                "Patient database has duplicate email and date-of-birth pairs; resolve them to enable database-level uniqueness."
            )


def validate_patient(data: object) -> tuple[dict[str, Any] | None, dict[str, str]]:
    if not isinstance(data, dict):
        return None, {"form": "A JSON object is required."}
    errors = {}
    raw_name = data.get("name", "")
    name = raw_name.strip() if isinstance(raw_name, str) else ""
    if not name or len(name) > 120:
        errors["name"] = "Enter a name of 1 to 120 characters."
    raw_dob_value = data.get("dob", "")
    raw_dob = raw_dob_value.strip() if isinstance(raw_dob_value, str) else ""
    dob = ""
    try:
        normalized_dob = (
            re.sub(r"\s*([./-])\s*", r"\1", raw_dob).replace(".", "/").replace("-", "/")
        )
        parsed = datetime.strptime(normalized_dob, "%d/%m/%Y").date()
        age = (
            date.today().year
            - parsed.year
            - ((date.today().month, date.today().day) < (parsed.month, parsed.day))
        )
        if parsed > date.today():
            errors["dob"] = "Date of birth cannot be in the future."
        elif age > 120:
            errors["dob"] = "Age cannot exceed 120 years."
        else:
            dob = parsed.strftime("%d/%m/%Y")
    except ValueError:
        errors["dob"] = "Enter a real date in dd/mm/yyyy, dd.mm.yyyy, or dd-mm-yyyy format."
    raw_email = data.get("email", "")
    email = raw_email.strip().lower() if isinstance(raw_email, str) else ""
    if len(email) > 254 or not EMAIL_RE.fullmatch(email):
        errors["email"] = "Enter a valid email address."
    values = {"name": name, "dob": dob, "email": email}
    for field, (minimum, maximum) in LAB_BOUNDS.items():
        try:
            raw_value = data.get(field)
            if isinstance(raw_value, bool):
                raise ValueError
            value = float(raw_value)
            if not (minimum <= value <= maximum):
                raise ValueError
            values[field] = value
        except (TypeError, ValueError):
            label = field.replace("_", " ").capitalize()
            errors[field] = f"{label} must be numeric and between {minimum:g} and {maximum:g}."
    return (None, errors) if errors else (values, {})


def patient_values(values: dict[str, Any]) -> tuple[str, str, str, float, float, float]:
    return tuple(
        values[key] for key in ("name", "dob", "email", "glucose", "haemoglobin", "cholesterol")
    )


def get_prediction(values: dict[str, Any], app: Flask) -> str:
    try:
        return predict_health_risk(values)
    except Exception:
        app.logger.exception("Health prediction provider failed")
        return "Prediction unavailable. Please consult a doctor for interpretation. Informational only; not medical advice."


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def api_error(errors: dict[str, str], status: int) -> tuple[Response, int]:
    return jsonify({"errors": errors}), status


app = create_app()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
