"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    h, m = map(int, shift_start.split(":"))
    start = punch_in.replace(hour=h, minute=m, second=0, microsecond=0)
    minutes = int((punch_in - start).total_seconds() / 60)
    return minutes if minutes > 10 else 0


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    return round((punch_out - punch_in).total_seconds() / 3600, 2)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str) -> int:
    h, m = map(int, shift_end.split(":"))
    end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    return max(0, int((punch_out - end).total_seconds() / 60))


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(409, "emp_code already exists")
    doc = body.model_dump()
    doc["created_at"] = datetime.now()
    db.employees.insert_one(doc)
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = page * page_size
    total = db.employees.count_documents({})
    items = list(db.employees.find(q, {"_id": 0}).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    ts = datetime.fromtimestamp(body.punched_at / 1000) if body.punched_at else datetime.now()
    d = ts.date().isoformat()
    if db.attendance_logs.find_one({"emp_code": body.emp_code, "date": d}):
        raise HTTPException(409, "already punched in for this date")
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, emp["shift_start"]),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    res = db.attendance_logs.insert_one(doc)
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
    docs = list(db.attendance_logs.find(q))
    docs.sort(key=lambda d: d["date"], reverse=True)
    total = len(docs)
    page_docs = docs[(page - 1) * page_size : page * page_size]
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #


import calendar
import json
import re
from contextlib import asynccontextmanager
from datetime import date as CalendarDate
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from bson import Decimal128, json_util
from bson.codec_options import CodecOptions
from fastapi import Depends, Query
from fastapi.responses import JSONResponse
from pydantic import AfterValidator, ConfigDict, Field, field_validator, model_validator
from pymongo import ReturnDocument, timeout
from pymongo.errors import DuplicateKeyError, PyMongoError


UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
PRESENCE = ["PRESENT", "WFH", "ON_DUTY"]
STATUS_FIELDS = (
    "status", "punch_in", "punch_out", "work_hours",
    "late_minutes", "overtime_minutes", "half_day",
)
LOG_DEFAULTS = {
    "punch_in": None,
    "punch_out": None,
    "work_hours": None,
    "late_minutes": 0,
    "overtime_minutes": 0,
    "half_day": False,
}
EMPLOYEE_FIELDS = (
    "emp_code", "name", "email", "department", "shift_start",
    "shift_end", "joined_on", "created_at",
)
db = db.with_options(codec_options=CodecOptions(tz_aware=True, tzinfo=UTC))


def valid_day(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError("Use a date in YYYY-MM-DD format")
    CalendarDate.fromisoformat(value)
    return value


def valid_month(value):
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", value):
        raise ValueError("Use a month in YYYY-MM format")
    CalendarDate.fromisoformat(value + "-01")
    return value


Day = Annotated[str, AfterValidator(valid_day)]
Month = Annotated[str, AfterValidator(valid_month)]
EpochMillis = Annotated[int, Field(strict=True, ge=100000000000, le=4102444800000)]
ShiftTime = Annotated[str, Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")]
Status = Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]
PresenceStatus = Literal["PRESENT", "WFH", "ON_DUTY"]
ExplainName = Literal[
    "attendance_list", "employee_monthly", "department_summary",
    "late_leaderboard", "department_trend",
]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def reject_internal_id(cls, values):
        if isinstance(values, dict) and "_id" in values:
            raise ValueError("Use emp_code, not MongoDB _id")
        return values


class EmployeeIn(RequestBody):
    emp_code: str = Field(pattern=r"^EMP[0-9]{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: ShiftTime = "09:30"
    shift_end: ShiftTime = "18:30"
    joined_on: Day

    @model_validator(mode="after")
    def different_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must be different")
        return self


class PunchOutIn(RequestBody):
    emp_code: str
    punched_at: EpochMillis | None = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def timestamp_cannot_be_null(cls, value):
        if value is None:
            raise ValueError("Omit punched_at to use the current time")
        return value


class PunchInIn(PunchOutIn):
    status: PresenceStatus = "PRESENT"


class RegularizeIn(RequestBody):
    status: Status | None = None
    punch_in: EpochMillis | None = None
    punch_out: EpochMillis | None = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("status", "punch_in", "punch_out", mode="before")
    @classmethod
    def supplied_values_cannot_be_null(cls, value):
        if value is None:
            raise ValueError("Omit a field to leave it unchanged")
        return value


def create_indexes():
    with timeout(15):
        db.employees.create_index([("emp_code", 1)], unique=True, name="employee_code")
        db.employees.create_index(
            [("department", 1), ("emp_code", 1)], name="department_people"
        )
        db.employees.create_index(
            [("joined_on", 1), ("department", 1)], name="joined_department"
        )
        db.employees.create_index(
            [("department", 1), ("joined_on", 1), ("emp_code", 1)],
            name="department_joined",
        )
        db.attendance_logs.create_index(
            [("emp_code", 1), ("date", -1)], unique=True, name="employee_day"
        )
        db.attendance_logs.create_index(
            [("date", -1), ("emp_code", 1)], name="day_employee"
        )
        db.attendance_logs.create_index(
            [("status", 1), ("date", -1), ("emp_code", 1)], name="status_day"
        )
        db.attendance_logs.create_index(
            [("emp_code", 1), ("status", 1), ("date", -1)], name="employee_status_day"
        )
        db.attendance_logs.create_index(
            [("emp_code", 1), ("punch_in", -1)], name="employee_punch"
        )


@asynccontextmanager
async def lifespan(application):
    application.state.indexes_ready = False
    try:
        create_indexes()
        application.state.indexes_ready = True
    except PyMongoError:
        pass
    yield
    client.close()


def require_database():
    if not getattr(app.state, "indexes_ready", False):
        try:
            create_indexes()
            app.state.indexes_ready = True
        except PyMongoError as error:
            raise HTTPException(503, "MongoDB is not ready") from error


app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan,
    dependencies=[Depends(require_database)],
)


@app.exception_handler(PyMongoError)
async def database_error_handler(request, error):
    return JSONResponse(status_code=503, content={"detail": "MongoDB request failed"})


def whole_second(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0)


def clock_time(milliseconds=None):
    if milliseconds is None:
        return whole_second(datetime.now(UTC))
    return EPOCH + timedelta(seconds=milliseconds // 1000)


def json_ready(value):
    if isinstance(value, datetime):
        return int((whole_second(value) - EPOCH).total_seconds()) * 1000
    if isinstance(value, Decimal128):
        return float(value.to_decimal())
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items() if key != "_id"}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    return value


def employee_response(employee):
    return json_ready({key: employee[key] for key in EMPLOYEE_FIELDS})


def attendance_response(record):
    result = {key: record.get(key, default) for key, default in LOG_DEFAULTS.items()}
    result.update({key: record[key] for key in ("emp_code", "date", "status")})
    result["history"] = record.get("history", [])
    return json_ready(result)


def get_employee(emp_code):
    employee = db.employees.find_one({"emp_code": emp_code})
    if employee is None:
        raise HTTPException(404, "Employee not found")
    return employee


def attendance_day(instant, employee):
    sleepy_clock = whole_second(instant).astimezone(IST)
    day = sleepy_clock.date()
    if employee["shift_end"] <= employee["shift_start"]:
        if sleepy_clock.strftime("%H:%M:%S") < employee["shift_end"] + ":00":
            day -= timedelta(days=1)
    return day.isoformat()


def shift_bounds(employee, day):
    start = datetime.fromisoformat(day + "T" + employee["shift_start"]).replace(tzinfo=IST)
    end = datetime.fromisoformat(day + "T" + employee["shift_end"]).replace(tzinfo=IST)
    if employee["shift_end"] <= employee["shift_start"]:
        end += timedelta(days=1)
    return start, end


def compute_late_minutes(punch_in, shift_start, date_str=None):
    punch_in = whole_second(punch_in)
    day = date_str or punch_in.astimezone(IST).date().isoformat()
    start = datetime.fromisoformat(day + "T" + shift_start).replace(tzinfo=IST)
    seconds = int((punch_in - start).total_seconds())
    return seconds // 60 if seconds > 600 else 0


def compute_work_hours(punch_in, punch_out):
    seconds = int((whole_second(punch_out) - whole_second(punch_in)).total_seconds())
    return float((Decimal(seconds) / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def compute_overtime(punch_out, shift_end, date_str, shift_start):
    employee = {"shift_start": shift_start, "shift_end": shift_end}
    _, end = shift_bounds(employee, date_str)
    minutes = int((whole_second(punch_out) - end).total_seconds()) // 60
    return minutes if minutes >= 30 else 0


def derived_values(employee, day, status, punch_in, punch_out):
    result = dict(LOG_DEFAULTS)
    if status not in PRESENCE:
        return result
    if punch_in is None:
        raise HTTPException(422, "A presence status requires punch_in")
    punch_in = whole_second(punch_in)
    if attendance_day(punch_in, employee) != day:
        raise HTTPException(422, "punch_in must belong to this attendance date")
    result["punch_in"] = punch_in
    result["late_minutes"] = compute_late_minutes(punch_in, employee["shift_start"], day)
    if punch_out is not None:
        punch_out = whole_second(punch_out)
        seconds = int((punch_out - punch_in).total_seconds())
        if seconds <= 0 or seconds > 86400:
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
        hours = compute_work_hours(punch_in, punch_out)
        result.update(
            punch_out=punch_out,
            work_hours=hours,
            half_day=hours < 4.50,
            overtime_minutes=compute_overtime(
                punch_out, employee["shift_end"], day, employee["shift_start"]
            ),
        )
    return result


def unchanged_record(record):
    condition = {"emp_code": record["emp_code"], "date": record["date"]}
    for key in (*STATUS_FIELDS, "history"):
        condition[key] = {"$eq": record[key]} if key in record else {"$exists": False}
    return condition


@app.get("/health")
def health():
    try:
        with timeout(3):
            client.admin.command("ping")
    except PyMongoError as error:
        raise HTTPException(503, "MongoDB is not ready") from error
    return {"status": "ok"}


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    employee = body.model_dump()
    employee["created_at"] = clock_time()
    try:
        db.employees.insert_one(employee)
    except DuplicateKeyError as error:
        raise HTTPException(409, "emp_code already exists") from error
    return employee_response(employee)


@app.get("/employees")
def list_employees(
    department: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    query = {} if department is None else {"department": department}
    index = "employee_code" if department is None else "department_people"
    total = db.employees.count_documents(query, hint=index)
    employees = db.employees.find(query).hint(index).sort("emp_code", 1)
    employees = employees.skip((page - 1) * page_size).limit(page_size)
    return {
        "items": [employee_response(employee) for employee in employees],
        "total": total, "page": page, "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    employee = get_employee(body.emp_code)
    instant = clock_time(body.punched_at)
    day = attendance_day(instant, employee)
    record = {
        "emp_code": body.emp_code, "date": day, "status": body.status, "history": [],
        **derived_values(employee, day, body.status, instant, None),
    }
    try:
        db.attendance_logs.insert_one(record)
    except DuplicateKeyError as error:
        raise HTTPException(409, "Already recorded for this attendance date") from error
    return attendance_response(record)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    employee = get_employee(body.emp_code)
    instant = clock_time(body.punched_at)
    record = db.attendance_logs.find_one(
        {"emp_code": body.emp_code, "punch_in": {"$type": "date", "$lte": instant}},
        sort=[("punch_in", -1)], hint="employee_punch",
    )
    if record is None:
        later_record = db.attendance_logs.find_one(
            {"emp_code": body.emp_code, "punch_in": {"$type": "date"}},
            hint="employee_punch",
        )
        if later_record is not None:
            raise HTTPException(422, "punched_at is earlier than punch_in")
        raise HTTPException(404, "No punch-in found")
    if record.get("punch_out") is not None:
        raise HTTPException(409, "This record is already punched out")
    values = derived_values(employee, record["date"], record["status"], record["punch_in"], instant)
    values = {key: values[key] for key in ("punch_out", "work_hours", "overtime_minutes", "half_day")}
    updated = db.attendance_logs.find_one_and_update(
        unchanged_record(record), {"$set": values}, return_document=ReturnDocument.AFTER
    )
    if updated is None:
        raise HTTPException(409, "The attendance record changed; reload it and try again")
    return attendance_response(updated)


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: Day, body: RegularizeIn):
    employee = get_employee(emp_code)
    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if record is None:
        raise HTTPException(404, "Attendance record not found")
    supplied = body.model_fields_set
    if not supplied.intersection({"status", "punch_in", "punch_out"}):
        raise HTTPException(422, "Supply at least one field to correct")
    status = body.status if "status" in supplied else record["status"]
    if status not in PRESENCE and supplied.intersection({"punch_in", "punch_out"}):
        raise HTTPException(422, "ABSENT and LEAVE cannot include punch times")
    incoming = clock_time(body.punch_in) if "punch_in" in supplied else record.get("punch_in")
    outgoing = clock_time(body.punch_out) if "punch_out" in supplied else record.get("punch_out")
    values = {"status": status, **derived_values(employee, date, status, incoming, outgoing)}
    changes = {}
    for key, value in values.items():
        previous = record.get(key, LOG_DEFAULTS.get(key))
        if previous != value:
            changes[key] = {"from": previous, "to": value}
    if not changes:
        raise HTTPException(422, "The correction does not change anything")
    entry = {
        "at": clock_time(), "by": body.regularized_by,
        "reason": body.reason, "changes": changes,
    }
    updated = db.attendance_logs.find_one_and_update(
        unchanged_record(record),
        {"$set": values, "$push": {"history": entry}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        raise HTTPException(409, "Another request changed this record; reload it and try again")
    return attendance_response(updated)


def date_range(date_from, date_to):
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(422, "date_from must not be after date_to")
    bounds = {}
    if date_from is not None:
        bounds["$gte"] = date_from
    if date_to is not None:
        bounds["$lte"] = date_to
    return bounds


def attendance_options(
    emp_code: str | None = None,
    date_from: Day | None = None,
    date_to: Day | None = None,
    status: Status | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return {
        "emp_code": emp_code, "date_from": date_from, "date_to": date_to,
        "status": status, "page": page, "page_size": page_size,
    }


def attendance_query(emp_code=None, date_from=None, date_to=None, status=None, page=1, page_size=20):
    query = {}
    if emp_code is not None:
        query["emp_code"] = emp_code
    if status is not None:
        query["status"] = status
    bounds = date_range(date_from, date_to)
    if bounds:
        query["date"] = bounds
    if emp_code is not None:
        index = "employee_status_day" if status is not None else "employee_day"
    else:
        index = "status_day" if status is not None else "day_employee"
    return {
        "find": "attendance_logs", "filter": query, "projection": {"_id": 0},
        "sort": {"date": -1, "emp_code": 1}, "skip": (page - 1) * page_size,
        "limit": page_size, "hint": index,
    }


def run_query(command):
    if "find" in command:
        cursor = db[command["find"]].find(command["filter"], command["projection"])
        return list(
            cursor.hint(command["hint"]).sort(list(command["sort"].items()))
            .skip(command["skip"]).limit(command["limit"])
        )
    return list(db[command["aggregate"]].aggregate(
        command["pipeline"], hint=command["hint"],
        allowDiskUse=command["allowDiskUse"], maxTimeMS=command["maxTimeMS"],
    ))


@app.get("/attendance")
def list_attendance(options: dict = Depends(attendance_options)):
    command = attendance_query(**options)
    total = db.attendance_logs.count_documents(command["filter"], hint=command["hint"])
    return {
        "items": [attendance_response(record) for record in run_query(command)],
        "total": total, "page": options["page"], "page_size": options["page_size"],
    }


def month_bounds(month):
    year, number = map(int, month.split("-"))
    days = calendar.monthrange(year, number)[1]
    return month + "-01", f"{month}-{days:02d}", days


def mongo_date(value):
    return {"$dateFromString": {"dateString": value, "format": "%Y-%m-%d", "timezone": "+05:30"}}


def weekday_expression(value):
    return {"$in": [{"$dayOfWeek": {"date": value, "timezone": "+05:30"}}, [2, 3, 4, 5, 6]]}


def present_expression(weekdays_only=False):
    condition = {"$in": ["$status", PRESENCE]}
    if weekdays_only:
        condition = {"$and": [condition, weekday_expression(mongo_date("$date"))]}
    return {"$cond": [condition, {"$cond": [{"$ifNull": ["$half_day", False]}, 0.5, 1]}, 0]}


def late_expression():
    return {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}


def half_up_expression(value, places=2):
    factor = 10 ** places
    return {"$let": {
        "vars": {"amount": value},
        "in": {"$cond": [
            {"$eq": ["$$amount", None]}, None,
            {"$divide": [
                {"$floor": {"$add": [
                    {"$multiply": [{"$toDecimal": "$$amount"}, factor]}, Decimal128("0.5")
                ]}}, factor,
            ]},
        ]},
    }}


def working_days_expression(first, number_of_days):
    calendar_party = {"$dateAdd": {
        "startDate": mongo_date(first), "unit": "day", "amount": "$$offset",
    }}
    return {"$size": {"$filter": {
        "input": {"$range": [0, number_of_days]}, "as": "offset",
        "cond": {"$and": [
            weekday_expression(calendar_party),
            {"$gte": [calendar_party, mongo_date("$joined_on")]},
        ]},
    }}}


def monthly_totals_stage():
    worked_record = {"$and": [
        {"$in": ["$status", PRESENCE]},
        {"$ne": [{"$ifNull": ["$work_hours", None]}, None]},
    ]}
    return {"$group": {
        "_id": None,
        "present_days": {"$sum": present_expression(True)},
        "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
        "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [late_expression(), 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
        "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}},
        "hours_sum": {"$sum": {"$cond": [worked_record, {"$toDecimal": "$work_hours"}, 0]}},
        "hours_count": {"$sum": {"$cond": [worked_record, 1, 0]}},
    }}


def monthly_lookup(first, last):
    return {"$lookup": {
        "from": "attendance_logs", "localField": "emp_code", "foreignField": "emp_code",
        "pipeline": [
            {"$match": {"date": {"$gte": first, "$lte": last}}},
            monthly_totals_stage(),
        ],
        "as": "monthly",
    }}


def aggregate_command(collection, pipeline, index):
    return {
        "aggregate": collection, "pipeline": pipeline, "cursor": {},
        "hint": index, "allowDiskUse": True, "maxTimeMS": 15000,
    }


def employee_monthly_query(emp_code, month):
    first, last, days = month_bounds(month)
    fields = (
        "present_days", "leave_days", "late_count",
        "total_late_minutes", "total_overtime_minutes",
    )
    pipeline = [
        {"$match": {"emp_code": emp_code}},
        monthly_lookup(first, last),
        {"$set": {"monthly": {"$arrayElemAt": ["$monthly", 0]}}},
        {"$project": {
            "_id": 0, "emp_code": 1, "month": {"$literal": month},
            "working_days": working_days_expression(first, days),
            **{field: {"$ifNull": ["$monthly." + field, 0]} for field in fields},
        }},
        {"$set": {"attendance_pct": {"$cond": [
            {"$gt": ["$working_days", 0]},
            half_up_expression({"$divide": [
                {"$multiply": [{"$toDecimal": "$present_days"}, 100]}, "$working_days",
            ]}), None,
        ]}}},
    ]
    return aggregate_command("employees", pipeline, "employee_code")


def department_summary_query(month, department=None):
    first, last, _ = month_bounds(month)
    query = {"joined_on": {"$lte": last}}
    if department is not None:
        query["department"] = department
    totals = (
        "present_days", "late_count", "total_late_minutes", "leave_days",
        "on_duty_count", "hours_sum", "hours_count",
    )
    pipeline = [
        {"$match": query},
        monthly_lookup(first, last),
        {"$set": {"monthly": {"$arrayElemAt": ["$monthly", 0]}}},
        {"$group": {
            "_id": "$department", "headcount": {"$sum": 1},
            **{field: {"$sum": {"$ifNull": ["$monthly." + field, 0]}} for field in totals},
        }},
        {"$project": {
            "_id": 0, "department": "$_id", "headcount": 1, "present_days": 1,
            "late_count": 1, "total_late_minutes": 1, "leave_count": "$leave_days",
            "on_duty_count": 1,
            "avg_work_hours": {"$cond": [
                {"$gt": ["$hours_count", 0]},
                half_up_expression({"$divide": ["$hours_sum", "$hours_count"]}), None,
            ]},
        }},
        {"$sort": {"department": 1}},
    ]
    index = "joined_department" if department is None else "department_joined"
    return aggregate_command("employees", pipeline, index)


def late_leaderboard_query(month, department=None, limit=10):
    first, last, _ = month_bounds(month)
    employee_filter = [] if department is None else [{"$match": {"department": department}}]
    pipeline = [
        {"$match": {"date": {"$gte": first, "$lte": last}}},
        {"$group": {
            "_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
            "late_count": {"$sum": {"$cond": [late_expression(), 1, 0]}},
        }},
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {"$lookup": {
            "from": "employees", "localField": "_id", "foreignField": "emp_code",
            "pipeline": employee_filter + [{"$project": {"_id": 0, "name": 1, "department": 1}}],
            "as": "employee",
        }},
        {"$unwind": "$employee"},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}},
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$project": {
            "_id": 0, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department",
            "rank": 1, "total_late_minutes": 1, "late_count": 1,
        }},
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}},
    ]
    return aggregate_command("attendance_logs", pipeline, "day_employee")


def department_trend_query(department, first, last):
    count = (CalendarDate.fromisoformat(last) - CalendarDate.fromisoformat(first)).days + 1
    if count < 1 or count > 92:
        raise HTTPException(422, "The inclusive range must contain between 1 and 92 days")
    pipeline = [
        {"$match": {"department": department}},
        {"$limit": 1},
        {"$project": {
            "_id": 0,
            "day": {"$map": {
                "input": {"$range": [0, count]}, "as": "offset",
                "in": {"$dateAdd": {"startDate": mongo_date(first), "unit": "day", "amount": "$$offset"}},
            }},
        }},
        {"$unwind": "$day"},
        {"$set": {
            "date": {"$dateToString": {"date": "$day", "format": "%Y-%m-%d", "timezone": "+05:30"}},
            "is_working_day": weekday_expression("$day"),
        }},
        {"$lookup": {
            "from": "employees", "let": {"day": "$date"},
            "pipeline": [
                {"$match": {"department": department, "$expr": {"$lte": ["$joined_on", "$$day"]}}},
                {"$count": "count"},
            ],
            "as": "people",
        }},
        {"$lookup": {
            "from": "attendance_logs", "localField": "date", "foreignField": "date",
            "pipeline": [
                {"$lookup": {
                    "from": "employees", "localField": "emp_code", "foreignField": "emp_code",
                    "pipeline": [
                        {"$match": {"department": department}}, {"$project": {"_id": 0, "emp_code": 1}},
                    ],
                    "as": "employee",
                }},
                {"$unwind": "$employee"},
                {"$group": {
                    "_id": None, "present_count": {"$sum": present_expression()},
                    "late_count": {"$sum": {"$cond": [late_expression(), 1, 0]}},
                }},
            ],
            "as": "daily",
        }},
        {"$set": {
            "headcount": {"$ifNull": [{"$arrayElemAt": ["$people.count", 0]}, 0]},
            "present_count": {"$ifNull": [{"$arrayElemAt": ["$daily.present_count", 0]}, 0]},
            "late_count": {"$ifNull": [{"$arrayElemAt": ["$daily.late_count", 0]}, 0]},
        }},
        {"$set": {"attendance_rate": {"$cond": [
            {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
            half_up_expression({"$divide": [{"$toDecimal": "$present_count"}, "$headcount"]}, 4), None,
        ]}}},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {"moving_avg_7d": {
                "$avg": "$attendance_rate", "window": {"documents": [-6, 0]},
            }},
        }},
        {"$project": {
            "_id": 0, "date": 1, "is_working_day": 1, "headcount": 1,
            "present_count": 1, "late_count": 1, "attendance_rate": 1,
            "moving_avg_7d": half_up_expression("$moving_avg_7d", 4),
        }},
        {"$sort": {"date": 1}},
    ]
    return aggregate_command("employees", pipeline, "department_joined")


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: Month):
    rows = run_query(employee_monthly_query(emp_code, month))
    if not rows:
        raise HTTPException(404, "Employee not found")
    return json_ready(rows[0])


@app.get("/analytics/departments/summary")
def department_summary(month: Month, department: str | None = None):
    return {"month": month, "items": json_ready(run_query(department_summary_query(month, department)))}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: Month, department: str | None = None, limit: int = Query(10, ge=1, le=50),
):
    return {"month": month, "items": json_ready(run_query(late_leaderboard_query(month, department, limit)))}


@app.get("/analytics/departments/{department}/trend")
def department_trend(department: str, date_from: Day = Query(alias="from"), date_to: Day = Query(alias="to")):
    command = department_trend_query(department, date_from, date_to)
    rows = run_query(command)
    if not rows:
        raise HTTPException(404, "Department not found")
    return {"department": department, "items": json_ready(rows)}


def require_parameters(**values):
    missing = [key for key, value in values.items() if value is None]
    if missing:
        raise HTTPException(422, "Missing required parameters: " + ", ".join(missing))


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: ExplainName,
    emp_code: str | None = None,
    month: Month | None = None,
    department: str | None = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Day | None = None,
    date_to: Day | None = None,
    status: Status | None = None,
    trend_from: Day | None = Query(None, alias="from"),
    trend_to: Day | None = Query(None, alias="to"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    if endpoint == "attendance_list":
        command = attendance_query(emp_code, date_from, date_to, status, page, page_size)
    elif endpoint == "employee_monthly":
        require_parameters(emp_code=emp_code, month=month)
        command = employee_monthly_query(emp_code, month)
    elif endpoint == "department_summary":
        require_parameters(month=month)
        command = department_summary_query(month, department)
    elif endpoint == "late_leaderboard":
        require_parameters(month=month)
        command = late_leaderboard_query(month, department, limit)
    else:
        require_parameters(department=department, **{"from": trend_from, "to": trend_to})
        command = department_trend_query(department, trend_from, trend_to)
    plan = db.command("explain", command, verbosity="executionStats")
    return {
        "endpoint": endpoint,
        "collection": command.get("find", command.get("aggregate")),
        "explain": json.loads(json_util.dumps(plan, json_options=json_util.RELAXED_JSON_OPTIONS)),
    }
