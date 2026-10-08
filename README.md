# Fingerspot web: local Docker setup

This project is a server-rendered Flask attendance and HR application. The root
`app.py` is the entry point used by the original Windows launcher. It contains
about 11,400 lines and 100 routes. `fingerspot-new/` is a separate, partial
blueprint-based refactor; the Docker setup runs the root application.

The local database now contains a reconstructed demo schema and dummy data.
See [demo accounts and seeder instructions](database/README.md) for every login.
Admin: `admin` / `DemoAdmin123!`; employee: `9001` / `DemoUser123!`.

## Start and stop

Start Docker Desktop, then run these commands from this directory:

```powershell
# Only needed if .env does not already exist. Replace all example secrets.
Copy-Item .env.example .env
docker compose up -d --build
docker compose --profile tools run --rm seed
docker compose ps
```

Open http://localhost:8080/login. The initial setup already generated random
local secrets in `.env`; keep that file private. Change `WEB_PORT` there if
8080 is occupied. To see startup errors or stop the containers:

```powershell
docker compose logs --tail 100 web db
docker compose down
```

Database data persists in the `fingerspot-local_db_data` Docker volume. Uploaded
files and generated reports persist in the existing `uploads/` and `reports/`
directories. The legacy `mysql/data/` directory is not modified or mounted.
Shift and NIP mapping edits persist through file mounts. The existing personnel
and overtime example spreadsheets are mounted read-only for the import screens.
Public configuration lives in `config/`; the original root mappings remain local.
The database has no host port. Only the web port is exposed, on loopback.

## Database limitation

The supplied `mysql/` directory comes from MySQL 5.1.41. Its `fin_pro/` folder
contains 100 `.frm` table definitions and 19 MyISAM data/index pairs. Most tables
have only a `.frm` file. There is no `ibdata1`, no `.ibd` file, and no application
SQL export in this project. `.frm` files alone do not contain InnoDB row data.
The included system-table SQL scripts are not an export of `fin_pro`.

Docker initially creates an empty `fin_pro` database in MariaDB 10.11. The
optional seed service now creates a **demo schema with fictional data** so
local logins, dashboards, request lists and attendance views can be tried.
This is not a recovery of the original database. A healthy web container means
`/login` renders; it does not mean the original data has been restored.

If an SQL export becomes available, import it into the local database:

```powershell
docker compose cp 'C:\path\to\fin_pro.sql' db:/tmp/fin_pro.sql
docker compose exec -T db sh -c 'MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb -uroot fin_pro < /tmp/fin_pro.sql'
docker compose exec -T db rm /tmp/fin_pro.sql
```

Review compatibility of a legacy dump with MariaDB before import. This setup
does not claim to recover the original rows from the incomplete backup. Use a
separate empty database for a real-data restore, rather than mixing it with
the seeded demo tables and accounts.

## Application structure

- `app.py`: admin/employee login, employee and organization maintenance,
  attendance ingestion (`POST /api/absen`), shifts and monthly schedules,
  overtime, leave, absence and shift-swap approvals, reports and exports.
- `templates/`: 61 Jinja HTML templates; `static/`: browser assets.
- `files/`: Word templates used for generated HR forms.
  Private form templates are excluded from Git; supply your own for form exports.
- `uploads/`: imported Excel/CSV data and intermediate files.
- `connection.json`: legacy connection settings for running outside Docker.
  Docker uses `DB_*` environment variables instead and does not copy this file.
  The legacy connection editor is not supported in this Docker setup; change
  Compose environment settings to select a different database.
- `fingerspot-new/routes/` and `utils/`: modular refactor, with a separate
  environment-based database configuration; it is not used by the root launcher.
- `app_*backup*.py`, dated Python files and extraction scripts: historical
  versions and refactoring utilities, not container entry points.
- `endpoint-exe/`: Windows device-related executables, not run by Docker.

The container uses Python 3.11 and Gunicorn, with pandas/openpyxl/XlsxWriter for
spreadsheets, python-docx/LibreOffice for Word/PDF conversion, and
pdfkit/wkhtmltopdf plus xhtml2pdf for PDF exports. `requirements-docker.txt`
contains the root app's runtime packages; the original `requirements.txt`
also includes Windows-only and unrelated environment packages.

## Changes for Docker

- Added Dockerfile, Compose services, runtime requirements, ignore files,
  and environment examples.
- Added an Indonesian locale with portable startup fallback.
- Made the session secret and database connection configurable through the
  environment, retaining the legacy connection-file fallback outside Docker.
- Matched the upload path to the existing lowercase `uploads/` directory,
  since Linux paths are case sensitive.
- Made the wkhtmltopdf executable portable. LibreOffice's existing command
  discovery already supports `soffice` on Linux.
- Runs without Flask debug mode, under a non-root container user.
- Removed embedded SMTP credentials and uses `SMTP_EMAIL` / `SMTP_PASSWORD`.
- Public source uses safe configuration, fictional input spreadsheets, and a
  Git allowlist that excludes private data, screenshots, binaries and backups.

## Findings from source review

These are observed concerns, not a complete security audit:

1. Login compares submitted passwords directly to database columns, indicating
   plaintext password storage. Password hashing and account recovery need work.
2. The original source contained session, database and SMTP credentials. The
   published source removes them and uses environment configuration. Rotate
   any credentials from the original shared source before using them elsewhere.
3. `/api/absen` has no authentication check and does not guard against a missing
   JSON body before testing its fields.
4. There is no global CSRF protection configured in the main app. State-changing
   routes and their authorization checks should be audited together. The legacy
   `/edit_connection` and `/save_connection` routes also lack login checks.
5. Several upload paths use the client filename directly. Filename validation,
   size limits, and containment checks need strengthening. Pickle files are also
   loaded in an import workflow, which warrants review alongside upload access.
6. Broad exception handlers sometimes suppress errors, making failed database
   operations look successful. Some cleanup blocks reference connections that
   may not have been initialized if connecting failed.
7. The monolith and separate refactor duplicate business logic and connection
   settings. There is no supplied automated test suite or schema migration
   workflow. The original launcher enables Flask debug mode on all interfaces.
8. Static template checking found a reference to
   `modal_jam_kerja_khusus.html`, which is absent from the root `templates/`
   directory. The affected view needs its missing template restored.

Keep this setup local while resolving these issues. Full workflow verification
requires a valid application database; page rendering and container health
alone cannot verify attendance calculations, approvals, or export correctness.

## Verified locally

Both Docker services reached healthy status. A host HTTP request to `/login`
returned 200 with the expected page title. Unauthenticated `/` and `/pegawai`
requests redirected to `/login`; a missing page returned 404. Python syntax
and installed dependency checks passed. The application connected to local
MariaDB. After seeding, integration checks passed for both admins, all eight
active employees, populated dashboards/request views, QAQC/TA filtering,
invalid/inactive/reset login states, the ownership guard, and preservation of
all application rows across reseeding. The PDF converter produced a PDF and
LibreOffice is available. See `database/README.md` for demo counts and limitations.

## Public repository contents

The public version includes the main app, the partial refactor's source and web
assets, Docker configuration, demo schema/seeder, and fictional import examples.
It excludes `.env`, `connection.json`, real spreadsheets/mappings, MySQL files,
uploads/reports, private Word forms, screenshots, binaries, extraction outputs,
and historical source copies. Those original files remain in the local folder.
`scripts/audit_public.py` provides a focused credential/private-file check.
Email sending requires your own SMTP configuration; form exports require your
own Word templates. The demo login passwords are intentionally documented.
