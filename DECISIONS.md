# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.


## My decisions

1. I used nine indexes. `employee_code` and `employee_day` enforce uniqueness. `department_people` supports employee listing. `joined_department` and `department_joined` support headcount queries. `day_employee`, `status_day` and `employee_status_day` cover attendance filters and ordering. `employee_punch` finds the latest punch. I left out a separate status-only index because `status_day` already starts with status.

2. Both punch-in requests try to insert. MongoDB accepts one and rejects the other through the unique employee/date index. The winner gets 201. The duplicate becomes 409. There is no separate existence check before inserting.

3. The leaderboard uses `$rank` before applying the limit. If employees tie at the cutoff, all of them are returned. Employee code sorts tied rows without changing their rank, so ranks can go 1, 2, 2, 4.

4. The department query starts with employees who joined by month-end, then looks up attendance. Someone with no logs still adds one to headcount. Work hours are averaged across records, not employee averages.

5. At 100 times the data, I'd first check query plans and timings. Daily department summaries could help, but corrections would need to update them properly. I wouldn't add that extra storage without measuring the benefit.
