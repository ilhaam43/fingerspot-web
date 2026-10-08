"""Verify real demo login flows, populated views, and non-destructive reseeding.

Run: docker compose --profile tools run --rm seed python /seed/verify_demo.py
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, "/app")
import app
import seed_demo


def snapshot(connection):
    cursor = connection.cursor()
    cursor.execute("SHOW TABLES")
    tables = [row[0] for row in cursor.fetchall() if row[0] != "demo_seed"]
    result = {}
    for table in tables:
        cursor.execute(f"SELECT * FROM `{table}`")
        rows = sorted(json.dumps(row, default=str) for row in cursor.fetchall())
        result[table] = (len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest())
    cursor.close()
    connection.commit()
    return result


def login(username, password, role, location):
    client = app.app.test_client()
    response = client.post("/login", data={"username": username, "password": password})
    assert response.status_code == 302 and response.headers["Location"] == location, username
    with client.session_transaction() as session:
        assert session.get("loggedin") is True and session.get("role") == role, username
    return client


def page(client, path, contains=None, excludes=None):
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code)
    content = response.get_data(as_text=True)
    for value in contains or []:
        assert value in content, (path, "missing", value)
    for value in excludes or []:
        assert value not in content, (path, "unexpected", value)
    return response


def main():
    if os.environ.get("DEMO_SEED_ENABLED") != "1":
        raise SystemExit("Run with the Compose seed service.")
    app.app.config["TESTING"] = True
    connection = app.get_db_connection()
    try:
        before = snapshot(connection)
        cursor = connection.cursor()
        cursor.execute("SELECT seed_date FROM demo_seed WHERE id = 1")
        anchor = cursor.fetchone()[0]
        seed_demo.initialize(cursor, connection)
        seed_demo.seed(cursor, anchor)
        connection.commit()
        cursor.close()
        assert snapshot(connection) == before, "Reseeding changed existing demo rows"
        print("PASS: reseeding preserves all application rows and counts")

        # Test the ownership guard against an actual incorrect marker in a
        # transaction, restoring it immediately by rolling the transaction back.
        cursor = connection.cursor()
        cursor.execute("UPDATE demo_seed SET version = 'invalid-test-marker' WHERE id = 1")
        try:
            seed_demo.initialize(cursor, connection)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Seeder accepted an incorrect ownership marker")
        finally:
            connection.rollback()
            cursor.close()
        print("PASS: mismatched database marker is rejected before seeding")

        admin = login("admin", seed_demo.ADMIN_PASSWORD, "admin", "/")
        qaqc = login("admin-qaqc", seed_demo.QAQC_PASSWORD, "admin", "/")
        for emp_id, nip, name, _, _, _, status in seed_demo.EMPLOYEES:
            if status == 1:
                employee = login(nip, seed_demo.EMPLOYEE_PASSWORD, "pegawai", "/beranda")
                page(employee, "/beranda", contains=[name])
                page(employee, "/akun-saya", contains=[name])
        print("PASS: both admin accounts and all eight active employees log in")

        page(admin, "/", contains=["Demo Andi HDTI", "Demo Eka QAQC"])
        page(admin, "/pegawai", contains=["Demo Andi HDTI", "Demo Eka QAQC"])
        page(qaqc, "/", contains=["Demo Eka QAQC", "Demo Farah TA"], excludes=["Demo Andi HDTI", "Demo Citra HDBS"])
        page(qaqc, "/pegawai", contains=["Demo Eka QAQC", "Demo Farah TA"], excludes=["Demo Andi HDTI", "Demo Citra HDBS"])
        for path in ("/fungsi", "/daftar-cuti", "/daftar-lembur", "/daftar-status-kehadiran",
                     "/daftar-tukar-shift", "/cek_jadwal", "/visualisasi_absen"):
            page(admin, path)
        for path in ("/daftar-cuti", "/daftar-lembur", "/daftar-status-kehadiran", "/daftar-tukar-shift"):
            page(admin, path, contains=["DEMO-9001-"])
        print("PASS: populated admin views and QAQC/TA filtering")

        employee = login("9001", seed_demo.EMPLOYEE_PASSWORD, "pegawai", "/beranda")
        page(employee, "/beranda", contains=["Demo Andi HDTI"], excludes=["Demo Budi HDTI", "Demo Eka QAQC"])
        for path in ("/pengajuan-cuti", "/pengajuan-lembur", "/status-kehadiran", "/tukar-shift", "/kalender-cuti"):
            page(employee, path)
        for path in ("/cuti-saya", "/lemburan-saya", "/status-kehadiran-saya", "/tukar-shift-saya"):
            page(employee, path, contains=["DEMO-9001-"], excludes=["DEMO-9002-"])
        for path in ("/", "/pegawai", "/daftar-cuti"):
            response = employee.get(path)
            assert response.status_code == 302 and response.headers["Location"] == "/login", path
        response = admin.get("/beranda")
        assert response.status_code == 302, "Admin should not enter employee view"
        print("PASS: employee views contain own data; role boundaries redirect")

        for username, password in (("admin", "incorrect"), ("9009", seed_demo.EMPLOYEE_PASSWORD)):
            client = app.app.test_client()
            response = client.post("/login", data={"username": username, "password": password})
            assert response.status_code == 200
            with client.session_transaction() as session:
                assert not session.get("loggedin"), username
        reset = app.app.test_client()
        response = reset.post("/login", data={"username": "9008", "password": seed_demo.EMPLOYEE_PASSWORD})
        assert response.status_code == 200
        with reset.session_transaction() as session:
            assert session.get("force_reset") is True
        print("PASS: invalid password, inactive employee, and forced-reset account")
        print("All demo checks passed. No emails sent or approval states changed.")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
