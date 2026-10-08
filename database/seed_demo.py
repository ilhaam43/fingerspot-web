"""Create repeatable dummy data in the isolated Docker database only.

Run: docker compose --profile tools run --rm seed
Existing demo rows are preserved on reruns; no real database is adopted or wiped.
"""
import argparse
import calendar
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import mysql.connector


ADMIN_PASSWORD = "DemoAdmin123!"
QAQC_PASSWORD = "DemoQaqc123!"
EMPLOYEE_PASSWORD = "DemoUser123!"
DEMO_VERSION = "fingerspot-demo-v1"

# These are job titles, not additional authorization roles.
EMPLOYEES = [
    (101, "9001", "Demo Andi HDTI", 1, 2, 1, 1),
    (102, "9002", "Demo Budi HDTI", 1, 1, 1, 1),
    (103, "9003", "Demo Citra HDBS", 2, 3, 2, 1),
    (104, "9004", "Demo Dewi HDBS", 2, 1, 2, 1),
    (105, "9005", "Demo Eka QAQC", 3, 1, 3, 1),
    (106, "9006", "Demo Farah TA", 4, 1, 4, 1),
    (107, "9007", "Demo Gita SDMI", 5, 1, 5, 1),
    (108, "9008", "Demo Hadi Reset", 1, 1, 1, 4),
    (109, "9009", "Demo Intan Inactive", 2, 1, 2, 0),
    (180, "9010", "Demo Joko PM", 5, 4, 5, 1),
]


def insert(cursor, table, rows):
    """Keep existing rows intact and only insert records with new natural keys."""
    for row in rows:
        columns = list(row)
        # All identifiers come from constants in this file, never user input.
        cursor.execute(
            f"INSERT INTO `{table}` ({','.join('`' + c + '`' for c in columns)}) "
            f"VALUES ({','.join(['%s'] * len(columns))}) "
            f"ON DUPLICATE KEY UPDATE `{columns[0]}` = `{columns[0]}`",
            tuple(row.values()),
        )


def initialize(cursor, connection):
    cursor.execute("SHOW TABLES")
    tables = {row[0] for row in cursor.fetchall()}
    if tables:
        if "demo_seed" not in tables:
            raise RuntimeError("Refusing to seed an existing non-demo database.")
        cursor.execute("SELECT version FROM demo_seed WHERE id = 1")
        marker = cursor.fetchone()
        if marker != (DEMO_VERSION,):
            raise RuntimeError("Demo database marker does not match this seeder.")
    else:
        # Commit ownership before DDL so an interrupted first run is retryable.
        cursor.execute("""CREATE TABLE demo_seed (
            id INT PRIMARY KEY, version VARCHAR(100) NOT NULL,
            seeded_at DATETIME, seed_date DATE
        ) ENGINE=InnoDB""")
        cursor.execute("INSERT INTO demo_seed (id, version) VALUES (1, %s)", (DEMO_VERSION,))
        connection.commit()

    schema = Path(__file__).with_name("demo-schema.sql").read_text(encoding="utf-8")
    for statement in schema.split(";"):
        if statement.strip():
            cursor.execute(statement)


def seed(cursor, today):
    divisions = ["HDTI", "HDBS", "QAQC", "TA", "SDMI"]
    insert(cursor, "login_web", [
        {"id_akun": 1, "username": "admin", "password": ADMIN_PASSWORD},
        {"id_akun": 2, "username": "admin-qaqc", "password": QAQC_PASSWORD},
    ])
    insert(cursor, "pembagian1", [
        {"pembagian1_id": i, "pembagian1_nama": name, "pembagian1_ket": "Local demo division"}
        for i, name in enumerate(divisions, 1)
    ])
    insert(cursor, "pembagian2", [
        {"pembagian2_id": i, "pembagian2_nama": name, "pembagian2_ket": "Local demo job title"}
        for i, name in enumerate(["Staff", "Team Leader", "Supervisor", "Project Manager"], 1)
    ])
    insert(cursor, "pembagian3", [
        {"pembagian3_id": i, "pembagian3_nama": f"Demo {name}"}
        for i, name in enumerate(divisions, 1)
    ])
    insert(cursor, "tim", [
        {"id": i, "pembagian1_nama": name, "tim_nama": f"Demo {name}"}
        for i, name in enumerate(divisions, 1)
    ])
    insert(cursor, "ref_nama_lembur", [
        {"id": 1, "nama": "Demo Andi HDTI"},
        {"id": 2, "nama": "Demo Citra HDBS"},
        {"nama": "Demo Supervisor"},
    ])
    insert(cursor, "ref_lokasi_kerja", [
        {"id": 1, "lokasi": "Demo Kantor Jakarta"},
        {"id": 2, "lokasi": "Demo Data Center"},
    ])
    insert(cursor, "pegawai", [
        {"pegawai_id": emp_id, "pegawai_pin": nip, "pegawai_nip": nip,
         "pegawai_nama": name, "pegawai_alias": name.split()[1],
         "pegawai_nik_lintas": "9900" + nip, "pegawai_pwd": EMPLOYEE_PASSWORD,
         "pegawai_status": status, "pembagian1_id": division,
         "pembagian2_id": title, "pembagian3_id": team,
         "sisa_cuti": 11 if emp_id in (102, 105) else 12}
        for emp_id, nip, name, division, title, team, status in EMPLOYEES
    ])
    shifts = [
        (1, "Demo Reguler", "08:00:00", "17:00:00"),
        (2, "Demo Pagi", "07:00:00", "15:00:00"),
        (3, "Demo Siang", "15:00:00", "23:00:00"),
        (5, "Demo Malam", "23:00:00", "07:00:00"),
        (8, "Cuti Tahunan", None, None), (9, "Dinas", None, None),
        (10, "Libur", None, None), (11, "Sakit", None, None),
        (29, "Libur Bergilir", None, None), (30, "Libur Dinas Luar", None, None),
        (31, "Libur Dinas", None, None), (34, "Izin", None, None),
    ]
    insert(cursor, "jam_kerja", [
        {"jk_id": key, "jk_name": name, "jk_bcin": start, "jk_ecout": end}
        for key, name, start, end in shifts
    ])

    # Entire previous/current/next months keep monthly and weekly views populated.
    month_start = today.replace(day=1)
    start = (month_start - timedelta(days=1)).replace(day=1)
    next_month = (month_start + timedelta(days=32)).replace(day=1)
    end = next_month.replace(day=calendar.monthrange(next_month.year, next_month.month)[1])
    request_day = today - timedelta(days=today.weekday()) + timedelta(days=1)
    day = start
    while day <= end:
        for offset, (emp_id, nip, name, _, _, _, status) in enumerate(EMPLOYEES):
            if status == 0:
                continue
            pattern = (day.toordinal() + offset) % 10
            scan_in = scan_out = None
            if day.weekday() >= 5:
                jk_id, note = 10, "Demo: weekend"
            elif pattern == 5:
                jk_id, note = 8, "Demo: leave"
            elif pattern == 6:
                jk_id, note = 11, "Demo: sick"
            elif pattern == 7:
                jk_id, note = 9, "Demo: business trip"
            elif pattern == 8:
                jk_id, note = 1, "Demo: absent"
            else:
                jk_id = 1
                scan_in = datetime.combine(day, time(8, 20) if pattern == 2 else time(7, 55))
                scan_out = datetime.combine(day, time(16) if pattern == 3 else time(17, 5))
                if pattern == 4:
                    scan_out = None
                note = "Demo: " + {2: "late", 3: "early departure", 4: "missing checkout"}.get(pattern, "present")
            # Approved demo requests have matching attendance on distinct days.
            if emp_id in (102, 105):
                if day == request_day + timedelta(days=1):
                    jk_id, scan_in, scan_out, note = 8, None, None, "Demo: approved leave"
                elif day == request_day + timedelta(days=2):
                    jk_id, scan_in, scan_out, note = 11, None, None, "Demo: approved sick request"
                elif day == request_day + timedelta(days=3):
                    jk_id = 2
                    scan_in, scan_out = datetime.combine(day, time(6, 55)), datetime.combine(day, time(15, 5))
                    note = "Demo: approved shift swap"
            insert(cursor, "shift_result_new", [{
                "pegawai_id": emp_id, "tgl_shift": day, "jk_id": jk_id,
                "scan_in": scan_in, "scan_out": scan_out, "notes": note,
            }])
            for mode, scan in enumerate((scan_in, scan_out)):
                if scan:
                    insert(cursor, "att_log", [{
                        "sn": "DEMO-DEVICE-001", "scan_date": scan, "pin": nip,
                        "verifymode": 1, "inoutmode": mode,
                        "reserved": 0, "work_code": 0, "att_id": emp_id,
                    }])
        day += timedelta(days=1)

    # Normal reruns preserve user edits and existing records.
    insert(cursor, "cuti_libur_nasional", [{
        "tanggal": today + timedelta(days=21),
        "keterangan": "DEMO holiday (fictional, not an official holiday)",
        "jenis": "Libur Nasional",
    }])
    for i, (emp_id, nip, name, division, _, _, _) in enumerate(EMPLOYEES[:7], 1):
        status = ["Menunggu", "OK", "Ditolak"][(i - 1) % 3]
        submitted = datetime.combine(today - timedelta(days=i), time(9))
        common = {
            "pegawai_id": emp_id, "pegawai_nip": nip,
            "nomor_surat": f"DEMO-{nip}-{{kind}}-{today:%Y%m}",
            "nama_mengetahui": "Demo Andi HDTI", "nama_menyetujui": "Demo Supervisor",
            "status": status, "tanggal_pengajuan": submitted,
        }
        def request_row(kind, **fields):
            return {**common, "nomor_surat": common["nomor_surat"].format(kind=kind), **fields}
        insert(cursor, "pengajuan_lembur", [request_row(
            "OT", penerima_tugas=name, tanggal_lembur=request_day,
            lokasi_kerja="Demo Kantor Jakarta", jam_lembur_awal="18:00:00",
            jam_lembur_akhir="20:00:00", keterangan_lembur="Demo: maintenance exercise",
            pemberi_tugas="Demo Citra HDBS",
        )])
        insert(cursor, "pengajuan_cuti", [request_row(
            "CT", kategori_cuti="CT", tim=f"Demo {divisions[division - 1]}",
            personil_backup="Demo Budi HDTI", alasan="Demo: personal leave",
            tanggal_cuti_mulai=request_day + timedelta(days=1),
            tanggal_cuti_akhir=request_day + timedelta(days=1),
        )])
        insert(cursor, "status_kehadiran", [request_row(
            "ABS", nomor_id_bi="DEMO-" + nip, kategori_alasan="Sakit",
            tim=f"Demo {divisions[division - 1]}", alasan_detail="Demo: medical rest",
            tanggal_ketidakhadiran=request_day + timedelta(days=2),
        )])
        target = EMPLOYEES[1] if emp_id != 102 else EMPLOYEES[0]
        insert(cursor, "pengajuan_tukar_shift", [request_row(
            "SWAP", pegawai_nik_lintas="9900" + nip, tim=f"Demo {divisions[division - 1]}",
            tanggal_mulai=request_day + timedelta(days=3),
            tanggal_akhir=request_day + timedelta(days=3),
            shift_awal_id=1, shift_awal_nama="Demo Reguler",
            shift_baru_id=2, shift_baru_nama="Demo Pagi",
            pegawai_tukar_id=target[0], pegawai_tukar_nip=target[1], pegawai_tukar_nama=target[2],
            alasan_tukar_shift="Demo: schedule exchange",
        )])
    return start, end


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, default=datetime.now(ZoneInfo("Asia/Jakarta")).date(),
                        help="Anchor date YYYY-MM-DD; defaults to today in Jakarta")
    args = parser.parse_args()
    if (os.environ.get("DEMO_SEED_ENABLED") != "1"
            or os.environ.get("DB_HOST") != "db"
            or os.environ.get("DB_NAME") != "fin_pro"):
        raise SystemExit("Use the Compose seed service; seeding is restricted to its local db/fin_pro.")
    connection = mysql.connector.connect(
        host="db", port=3306, database="fin_pro", user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"], charset="utf8",
    )
    cursor = connection.cursor()
    try:
        initialize(cursor, connection)
        start, end = seed(cursor, args.date)
        cursor.execute("UPDATE demo_seed SET seeded_at = NOW(), seed_date = %s WHERE id = 1", (args.date,))
        connection.commit()
        for table in ("login_web", "pegawai", "shift_result_new", "att_log", "pengajuan_cuti",
                      "pengajuan_lembur", "status_kehadiran", "pengajuan_tukar_shift"):
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            print(f"{table}: {cursor.fetchone()[0]} rows")
        print(f"Dummy attendance range: {start} through {end}")
        print("Demo seed complete. Credentials are documented in database/README.md.")
    except Exception:
        connection.rollback()
        raise
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    main()
