# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.


## Completed review

The rows below refer to the original starter functions, before the added implementation. The main problems were around time handling, validation, concurrent writes and missing endpoints.

| # | Where | What was wrong | How to notice it | What changed |
|---|---|---|---|---|
| 1 | MongoDB setup | No indexes were created for uniqueness or the required queries. | Duplicate writes can slip through, and large queries can scan the collection. | Startup now creates nine indexes, including unique employee code and employee/date indexes. |
| 2 | MongoDB setup | Reads used naive datetimes, with no clear UTC handling. | Combining a stored datetime with an aware IST datetime can give an error or a wrong calculation. | Database reads use aware UTC dates. Legacy naive values are treated as UTC. |
| 3 | `compute_late_minutes` | The shift start was made in the punch datetime's timezone, not explicitly IST. | A timestamp handled in UTC can produce the wrong lateness. | Shift start is built using IST and the attendance date. |
| 4 | `compute_late_minutes` | The punch's calendar date was used for an overnight shift. | A 02:00 punch for a 22:00 shift belongs to the previous attendance day and is 240 minutes late. | The helper receives the attendance day calculated using the overnight rule. |
| 5 | `compute_late_minutes` | Minutes were truncated before checking the ten-minute grace period. | 09:40:01 was treated as on time for a 09:30 shift. | Elapsed seconds are compared with 600 first. Late minutes are then floored. |
| 6 | Time helpers and punches | Milliseconds were not consistently removed before calculations or storage. | A value such as 09:40:00.999 could be stored with its fractional second. | Incoming instants are truncated to whole seconds before use. |
| 7 | `compute_work_hours` | Python's `round()` did not guarantee the required decimal half-up rounding. | An exact halfway value can round differently from the contract. | Work hours use `Decimal` with `ROUND_HALF_UP`. |
| 8 | `compute_overtime` | Any positive overtime was counted. | Leaving five minutes late returned five overtime minutes. | Overtime is counted only when the floored difference is at least 30 minutes. |
| 9 | `compute_overtime` | An overnight shift's end stayed on the starting date. | A 22:00 to 06:00 shift ending at 06:40 the next day gets the wrong overtime. | Overnight shift end moves to the following calendar day. |
| 10 | `EmployeeIn` | Employee codes, field lengths, emails, dates and shift times had no proper constraints. | Invalid codes, impossible dates or a time such as 25:99 could be accepted. | Added the contract's field constraints and real calendar validation. |
| 11 | `EmployeeIn` | Equal shift start and end were accepted. | Creating an employee with 09:30 for both times succeeded. | A model validator rejects equal shift times. |
| 12 | `PunchInIn` | Timestamp inputs were coercible and had no range check. | A string, float, boolean or seconds-looking number could be treated as a timestamp. | Timestamp inputs must be strict integers within the allowed millisecond range. Explicit null is rejected; omission uses the current time. |
| 13 | `PunchInIn` | Any status string was allowed. | Punching in with `LEAVE`, `ABSENT` or an unknown status was accepted. | Punch-in accepts only `PRESENT`, `WFH` and `ON_DUTY`. |
| 14 | Request models | There was no explicit rejection of a supplied MongoDB `_id`. | A request could include `_id` without being rejected. | The shared request model rejects `_id`; employee identity stays with `emp_code`. |
| 15 | `health` | It always returned OK without checking the database. | A disconnected database still looked ready. | Readiness now checks MongoDB and returns 503 when it cannot be used. |
| 16 | `create_employee` | Checking for an employee before inserting was unsafe during simultaneous requests. | Two requests can both pass the lookup before either inserts. | The unique index decides the winner. A duplicate-key error becomes 409. |
| 17 | `create_employee` | `created_at` used naive local time. | A server outside UTC can store the wrong instant. | Creation time now comes from the aware UTC server clock. |
| 18 | Employee and attendance responses | Datetimes did not use the required integer epoch-millisecond format. | FastAPI serialized the datetime as an ISO string. | A shared serializer converts datetime fields, including nested audit timestamps. |
| 19 | `list_employees` | The skip value was `page * page_size`. | Page 1 missed the first page of employees. | Skip is now `(page - 1) * page_size`. |
| 20 | `list_employees` | `total` counted every employee even when a department filter was used. | The item list and total described different sets. | The count uses the same filter as the list query. |
| 21 | `list_employees` | There was no employee-code sort. | Insertion order could affect page contents. | Employees are sorted by `emp_code` ascending. |
| 22 | Both list endpoints | Pagination values had no useful bounds. | Requests with page 0 or page size 101 were accepted. | Page starts at 1 and page size is limited to 1 through 100. |
| 23 | List filters | Truthiness checks treated an empty supplied filter as an omitted one. | An empty department could unexpectedly return every department. | The code checks whether a parameter is `None`, preserving exact filter behavior. Invalid status values are rejected. |
| 24 | `punch_in` | An unknown employee was not handled. | Accessing `shift_start` on a missing employee caused an error. | A shared lookup returns 404 before any attendance calculation. |
| 25 | `punch_in` | `fromtimestamp()` and `now()` used local time, and a truthiness check treated zero as omitted. | Results depended on the host timezone, while zero silently became the current time. | Valid timestamps are converted through UTC. Only an omitted timestamp uses the clock. |
| 26 | `punch_in` | Attendance dates ignored IST conversion and the overnight exception. | A punch near midnight or before an overnight shift ends gets the wrong day. | One helper calculates the IST attendance date using the employee's shift. |
| 27 | `punch_in` | Check-then-insert allowed duplicate daily attendance during a race. | Simultaneous requests could both create a document. | A unique employee/date index allows one insert; the others return 409. |
| 28 | `punch_in` and `list_attendance` | Responses exposed an `id` derived from MongoDB's `_id`. | The returned JSON contained an identifier outside the contract. | Attendance responses include only the public contract fields. |
| 29 | `list_attendance` | Dates, reversed ranges and status filters were not validated. | Invalid dates or a start date after the end date reached the query. | Invalid parameters now return 422. |
| 30 | `list_attendance` | Every matching document was loaded and sorted in Python before pagination. | Memory use grows with the number of matching records. | MongoDB handles filtering, sorting, skipping and limiting. A separate filtered count supplies the total. |
| 31 | `list_attendance` | Sorting only by date missed the employee-code tie breaker. | Records on the same date could come back in an unstable order. | Sorting is date descending, then employee code ascending. |
| 32 | `list_attendance` | Legacy records did not get defaults for missing fields. | Records without `history` or `half_day` returned incomplete responses. | Response formatting supplies the documented empty, false, zero and null defaults. |
| 33 | TODO endpoints | Seven required endpoints were missing. | Punch-out, corrections, analytics and explain routes returned 404 or 405. | Added punch-out, correction, four analytics endpoints and the explain endpoint. |

## Things that were already okay

- `load_dotenv()` already lets real environment variables win. That behavior is kept.
- `MONGO_URI` and `MONGO_DB` are the right configuration names.
- Looking up employees by `emp_code` was correct. Using ObjectId here would not match the contract.
- The fixed UTC+05:30 offset fits the assignment's IST rules.
- Ignoring extra derived fields in request bodies was already Pydantic's default behavior. The new models keep this, while rejecting `_id` explicitly.
- An open punch correctly starts with null punch-out and work hours, zero overtime, false half-day and empty history.
- Attendance list pagination already used `(page - 1) * page_size` when slicing. The issue there was loading everything into Python, not that formula.

## Added behavior and checks

Punch-out selects the latest eligible punch-in and updates the record only if the version it read is still current. Corrections use the same check, recalculate the derived fields and append one audit entry containing only actual changes. A losing concurrent update returns 409 instead of overwriting another request's work.

The analytics are MongoDB aggregations. They use stored derived values, count half-days as 0.5, keep people with no logs in headcount and use competition ranking for late-minute ties. The trend builds its calendar inside MongoDB and uses the requested range for its moving window. Each explain endpoint runs the same main query builder as the corresponding endpoint.

The automated suite passed 36 tests on 13 October 2026, covering 279 API requests. This included simultaneous writes, timing boundaries, overnight shifts, audit history, legacy records and analytics edge cases. Query-plan checks also passed on 100,000 synthetic attendance records, with `IXSCAN` and no `COLLSCAN` in the checked plans. The runner removed its temporary database afterward.

The original starter is still present in `app/main.py`. Corrected definitions and a final FastAPI app were appended to keep that boilerplate intact. Uvicorn uses the final app, so the old routes are not registered on it. This preserves the supplied code, but leaves duplication that would be worth cleaning up if that constraint changes.
