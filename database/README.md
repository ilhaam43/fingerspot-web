# Local dummy data and accounts

The local Docker database has been seeded. Open http://localhost:8080/login.

| Access | Username / NIP | Password |
| --- | --- | --- |
| Admin | `admin` | `DemoAdmin123!` |
| QAQC admin | `admin-qaqc` | `DemoQaqc123!` |
| Employee, HDTI team leader | `9001` | `DemoUser123!` |
| Employee, HDTI staff | `9002` | `DemoUser123!` |
| Employee, HDBS supervisor | `9003` | `DemoUser123!` |
| Employee, HDBS staff | `9004` | `DemoUser123!` |
| Employee, QAQC | `9005` | `DemoUser123!` |
| Employee, TA | `9006` | `DemoUser123!` |
| Employee, SDMI | `9007` | `DemoUser123!` |
| Employee, project manager | `9010` | `DemoUser123!` |
| Employee requiring password reset | `9008` | `DemoUser123!` |
| Inactive employee, login intentionally rejected | `9009` | `DemoUser123!` |

The code defines only `admin` and `pegawai` session roles. QAQC restrictions
are a special case for username `admin-qaqc` / admin ID 2. Staff, team leader,
supervisor and project manager are job titles, not separate authorization roles.
The PM account uses employee ID 180 to exercise the application's existing
special handling. Card IDs are fictional `9900` + NIP, e.g. `99009001`.

## Run again

```powershell
docker compose up -d
docker compose --profile tools run --rm seed
docker compose --profile tools run --rm seed python /seed/verify_demo.py
```

No rebuild is needed when changing the seed files. The seed service uses the
web image built by `docker compose up -d --build` and mounts `database/` read-only.
It creates tables only in an empty database or a database already marked as
owned by this seeder. It requires the Compose service's explicit seed flag and
the `db` / `fin_pro` connection settings. It refuses a populated non-demo database.
There is no reset/drop option and no connection to the legacy remote server.

Reruns add missing sample rows while preserving existing passwords, edits,
approval decisions and records. They do not restore deleted rows' original IDs
or revert user changes. Schema creation is not a migration system: future schema
changes need their own migration. Database data persists in the Docker volume.

To anchor the attendance months to a particular date:

```powershell
docker compose --profile tools run --rm seed python /seed/seed_demo.py --date 2026-10-08
```

Dates default to the current date in Asia/Jakarta. The seeder adds full previous,
current, and next months. The initial run produced September-November 2026:
2 admins, 10 employees, 5 divisions, 4 job titles, 5 teams, 12 shift codes,
819 employee-day schedules, 642 device scans, and 7 rows each for leave,
overtime, absence and shift swaps. Requests include `Menunggu`, `OK`, and
`Ditolak`. Approved leave/sickness/swap examples have matching shift records.
Attendance includes present, late, early departure, missing checkout, absent,
leave, sickness, business trip and weekend examples. A fictional holiday is
explicitly labelled DEMO. The application's fixed approver display label has
been anonymized to `Demo Supervisor` for the public source.

`demo-schema.sql` is a **new demo schema inferred from the root app's queries**.
It is not the original MySQL 5.1 schema and does not restore real attendance.
It includes the tables used by the root app; it does not reproduce the separate
`fingerspot-new` refactor or all 100 legacy table definitions.

The existing login code expects plaintext passwords, so these public demo
passwords follow that behavior. Use them only in this localhost environment.
No email delivery is exercised by the seeder or verifier.

The verifier checks actual login/session flows, populated admin and employee
views, QAQC dashboard filtering, inactive/reset states, the seed ownership guard,
and unchanged application rows after reseeding. It does not claim a full
authorization audit: QAQC filtering is inconsistent across the existing app.

Existing limitations remain: the special-working-hours view references a missing
template, and new shift-swap submissions contain a hardcoded July 6, 2026 cutoff.
Seeded shift-swap records can be viewed, but the seeder does not change that rule.
