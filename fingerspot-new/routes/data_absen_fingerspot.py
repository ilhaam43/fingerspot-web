from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, jsonify
import pandas as pd
import os
import json
from datetime import datetime, timedelta, date, time
from werkzeug.utils import secure_filename
from utils.db import get_db_connection
from routes.dcap import process_dcap_scan_times

data_absen_fingerspot_bp = Blueprint('data_absen_fingerspot', __name__)

@data_absen_fingerspot_bp.route('/data-absen-fingerspot', methods=['GET'])
def data_absen_fingerspot():
    # route ini adalah halaman log absensi mentah dari tabel att_log
    # admin bisa lihat semua scan masuk/keluar per pegawai dalam rentang tanggal
    # ada filter tanggal dan divisi, plus penanganan khusus admin qaqc

    # cek login dan role admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        flash('Please login to access this page.', 'warning')
        return redirect(url_for('auth.auth_login'))

    # ambil parameter filter dari url
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    division_filter = request.args.get('division')
    hapus_card = request.args.get('hapus_card')

    # debug: tampilkan parameter yang diterima
    print(f"\n--- Request to /att_log ---")
    print(f"Start Date: {start_date}, End Date: {end_date}, Division: {division_filter}")

    # ambil semua nama divisi untuk dropdown filter
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("""
        SELECT DISTINCT pembagian1_nama
        FROM pembagian1
        WHERE pembagian1_nama IS NOT NULL AND pembagian1_nama != ''
        ORDER BY pembagian1_nama
    """)
    all_divisions = [r['pembagian1_nama'] for r in cur.fetchall()]
    cur.close()
    conn.close()

    # kalau user admin qaqc, batasi divisi yang bisa dipilih
    if session.get('username') == 'admin-qaqc':
        allowed = {'QAQC', 'TA', 'QAQC-TA'}
        divisions = [d for d in all_divisions if d in allowed]
        # kalau filter divisi tidak diizinkan, reset ke none
        if division_filter not in allowed:
            division_filter = None
    else:
        divisions = all_divisions

    # kalau tidak ada filter tanggal, langsung tampilkan halaman kosong
    if not start_date or not end_date:
        print("[DEBUG] No date filter")
        return render_template(
            'data-absen-fingerspot.html',
            headers=[],
            records=[],
            divisions=divisions,
            start_date=start_date,
            end_date=end_date,
            selected_division=division_filter,
            hapus_card=hapus_card
        )

    try:
        # buka koneksi db lagi untuk query utama
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        print("[DEBUG] DB connected")

        # query ambil log scan + info pegawai + shift existing
        # join att_log dengan pegawai, pembagian1, dan shift_result_new
        query = """
            SELECT
                p.pegawai_nama,
                al.pin,
                al.scan_date,
                al.verifymode,
                sr.tgl_shift,
                sr.scan_in AS sr_scan_in,
                sr.scan_out AS sr_scan_out,
                sr.jk_id,
                pb1.pembagian1_nama
            FROM att_log al
            JOIN pegawai p ON al.pin = p.pegawai_pin
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            LEFT JOIN shift_result_new sr
                ON sr.pegawai_id = p.pegawai_id
                AND DATE(al.scan_date) = sr.tgl_shift
            WHERE al.scan_date BETWEEN %s AND %s
        """

        # params: start_date 00:00:00 sampai end_date +1 hari 12:00:00
        # supaya tangkap semua scan sampai tengah hari berikutnya
        params = [
            start_date + " 00:00:00",
            (datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d') + " 12:00:00"
        ]

        # kalau ada filter divisi, tambah kondisi
        if division_filter:
            query += " AND pb1.pembagian1_nama = %s"
            params.append(division_filter)

        # urutkan berdasarkan nip dan waktu scan
        query += " ORDER BY al.pin, al.scan_date"

        print("[DEBUG] Executing query")
        cur.execute(query, params)
        rows = cur.fetchall()
        print(f"[DEBUG] Raw rows: {len(rows)}")

        # kalau tidak ada data, tampilkan halaman kosong
        if not rows:
            return render_template(
                'data-absen-fingerspot.html',
                headers=[],
                records=[],
                divisions=divisions,
                start_date=start_date,
                end_date=end_date,
                selected_division=division_filter,
                hapus_card=hapus_card
            )

        # ubah hasil query jadi dataframe pandas
        df = pd.DataFrame(rows)
        # pastikan scan_date jadi datetime
        df['scan_date'] = pd.to_datetime(df['scan_date'])

        # filter out card if hapus_card is 1
        if hapus_card == '1':
            df = df[(df['verifymode'] != 3) & (df['verifymode'] != '3')]
            
            # jika kosong setelah dihapus
            if df.empty:
                return render_template(
                    'data-absen-fingerspot.html',
                    headers=[],
                    records=[],
                    divisions=divisions,
                    start_date=start_date,
                    end_date=end_date,
                    selected_division=division_filter,
                    hapus_card=hapus_card
                )

        final_records = []
        # daftar jk_id shift malam yang butuh penanganan khusus
        malam_jk_ids = {7, 15, 18, 22}

        # kelompokkan per nip dan tanggal (group by pin + date(scan_date))
        grouped = df.groupby(['pin', df['scan_date'].dt.date])

        # proses tiap grup (satu grup = satu pegawai satu tanggal)
        for (pin, tgl), g in grouped:
            nama = g['pegawai_nama'].iloc[0]
            jk_id = g['jk_id'].iloc[0]
            divisi = g['pembagian1_nama'].iloc[0]

            # ambil scan_in/out dari shift_result_new kalau ada
            sr_scan_in = g['sr_scan_in'].dropna().iloc[0] if g['sr_scan_in'].notna().any() else None
            sr_scan_out = g['sr_scan_out'].dropna().iloc[0] if g['sr_scan_out'].notna().any() else None
            
            verifymodes = g['verifymode'].dropna().unique().tolist()
            mode_map = {1: 'Finger', '1': 'Finger', 3: 'Card', '3': 'Card'}
            mapped_modes = [mode_map.get(m, str(m)) for m in verifymodes]
            verifymode_str = ", ".join(mapped_modes) if mapped_modes else "-"

            scan_in = None
            scan_out = None
            tanggal_shift = tgl

            # fallback ke data mesin kalau tidak ada di shift_result_new
            if jk_id in malam_jk_ids:
                # shift malam: scan in mulai 11:00 hari H
                scan_in = g[g['scan_date'].dt.time >= time(11, 0)]['scan_date'].min()
                # scan out: cari di hari berikutnya sampai jam 12:00
                next_day = df[
                    (df['pin'] == pin) &
                    (df['scan_date'].dt.date == tgl + timedelta(days=1))
                ]
                scan_out = next_day[next_day['scan_date'].dt.time <= time(12, 0)]['scan_date'].max()
            else:
                # shift normal/pagi: scan in 03:00 - 13:59 hari H
                scan_in = g[
                    (g['scan_date'].dt.time >= time(3, 0)) &
                    (g['scan_date'].dt.time <= time(13, 59))
                ]['scan_date'].min()
                # scan out: 14:00 - 23:59 hari H
                scan_out = g[
                    (g['scan_date'].dt.time >= time(14, 0))
                ]['scan_date'].max()

            # kalau ada data dari shift_result_new, override
            if sr_scan_in and str(sr_scan_in) != '0000-00-00 00:00:00':
                scan_in = sr_scan_in
                tanggal_shift = sr_scan_in.date()

            if sr_scan_out and str(sr_scan_out) != '0000-00-00 00:00:00':
                scan_out = sr_scan_out

            # simpan hasil ke list
            final_records.append({
                'nama_pegawai': nama,
                'pin': pin,
                'tanggal_scan': tanggal_shift,
                'scan_in': scan_in,
                'scan_out': scan_out,
                'verifymode': verifymode_str
            })

        # ubah ke dataframe dan buang duplikat
        final_df = pd.DataFrame(final_records)
        final_df = final_df.drop_duplicates(subset=['pin', 'tanggal_scan'])
        # urutkan berdasarkan nama pegawai
        final_df.sort_values('nama_pegawai', inplace=True)

        # format waktu scan jadi HH:MM, kalau kosong jadi --:--
        final_df['scan_in'] = final_df['scan_in'].apply(
            lambda x: x.strftime('%H:%M') if pd.notna(x) else '--:--'
        )
        final_df['scan_out'] = final_df['scan_out'].apply(
            lambda x: x.strftime('%H:%M') if pd.notna(x) else '--:--'
        )

    except Exception as e:
        # kalau ada error, log dan tampilkan halaman kosong
        print(f"[ERROR] {e}")
        return render_template(
            'data-absen-fingerspot.html',
            headers=[],
            records=[],
            divisions=divisions,
            start_date=start_date,
            end_date=end_date,
            selected_division=division_filter,
            hapus_card=hapus_card
        )
    finally:
        # tutup koneksi db
        if conn.is_connected():
            cur.close()
            conn.close()

    # tampilkan halaman dengan tabel hasil
    return render_template(
        'data-absen-fingerspot.html',
        headers=['Nama Pegawai', 'PIN', 'Tanggal Scan', 'Scan In', 'Scan Out', 'Verify Mode'],
        records=final_df.to_dict('records'),
        divisions=divisions,
        start_date=start_date,
        end_date=end_date,
        selected_division=division_filter,
        hapus_card=hapus_card
    )

@data_absen_fingerspot_bp.route('/update-hasil-shift-fingerspot', methods=['POST'])
def update_hasil_shift_fingerspot():
    # route ini dipanggil saat admin edit scan in/out dari halaman att_log
    # tugasnya: update scan_in dan scan_out di shift_result_new untuk record yang dipilih

    print("\n========== DEBUG update_shift_result ==========")

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        print("[AUTH] Unauthorized")
        return redirect(url_for('auth.auth_login'))

    print("[AUTH] User authenticated")
    print(f"[SESSION] username: {session.get('username')}")

    # debug: tampilkan semua data form yang dikirim
    print("\n[FORM DATA RAW]")
    for k, v in request.form.items():
        print(f"  {k}: {v}")

    # ambil list record yang dipilih (format: pin|tanggal_shift)
    selected_records = request.form.getlist('selected_records')
    print(f"\n[SELECTED RECORDS] {selected_records}")

    # kalau tidak ada yang dipilih, balik ke att_log
    if not selected_records:
        print("[ABORT] No records selected")
        flash("Tidak ada data yang dipilih untuk diupdate.", "warning")
        return redirect(url_for('data_absen_fingerspot.data_absen_fingerspot'))

    updated_count = 0

    try:
        conn = get_db_connection()
        cur = conn.cursor(dictionary=True)
        print("[DB] Database connected")

        # proses tiap record yang dipilih
        for record in selected_records:
            print("\n--------------------------------------")
            print(f"[PROCESS RECORD] {record}")

            # pisah pin dan tanggal dari string record
            pin, tanggal_shift = record.split('|')
            print(f"[DATA] PIN: {pin}")
            print(f"[DATA] Tanggal Shift: {tanggal_shift}")

            # ambil nilai scan_in dan scan_out baru dari form
            scan_in_str = request.form.get(f'scan_in_{pin}_{tanggal_shift}')
            scan_out_str = request.form.get(f'scan_out_{pin}_{tanggal_shift}')

            print(f"[SCAN STR] scan_in: {scan_in_str}")
            print(f"[SCAN STR] scan_out: {scan_out_str}")

            # Ambil jk_id dan pegawai_id untuk diproses threshold-nya
            cur.execute("""
                SELECT sr.jk_id, p.pegawai_id 
                FROM shift_result_new sr
                JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
                WHERE p.pegawai_pin = %s AND sr.tgl_shift = %s
            """, (pin, tanggal_shift))
            row = cur.fetchone()

            if not row:
                print(f"[SKIP] No schedule found for pin {pin} at {tanggal_shift}")
                continue

            jk_id = row['jk_id']
            pegawai_id = row['pegawai_id']

            # Proses dengan threshold yang sama persis seperti DCAP
            scan_in_val = scan_in_str if scan_in_str and scan_in_str != '--:--' else None
            scan_out_val = scan_out_str if scan_out_str and scan_out_str != '--:--' else None

            scan_in_final, scan_out_final = process_dcap_scan_times(
                jk_id, 
                tanggal_shift, 
                scan_in_val, 
                scan_out_val
            )

            set_clauses = []
            params = []

            # Tambahkan klausa update TANPA meniban data yang sudah ada (mirip DCAP)
            if scan_in_final:
                set_clauses.append("scan_in_source = CASE WHEN (scan_in IS NULL OR scan_in = '0000-00-00 00:00:00') AND %s != '0000-00-00 00:00:00' THEN 'Fingerspot' ELSE scan_in_source END")
                set_clauses.append("scan_in = CASE WHEN scan_in IS NULL OR scan_in = '0000-00-00 00:00:00' THEN %s ELSE scan_in END")
                params.extend([scan_in_final, scan_in_final])
                print(f"[UPDATE CANDIDATE] scan_in = {scan_in_final}")

            if scan_out_final:
                set_clauses.append("scan_out_source = CASE WHEN (scan_out IS NULL OR scan_out = '0000-00-00 00:00:00') AND %s != '0000-00-00 00:00:00' THEN 'Fingerspot' ELSE scan_out_source END")
                set_clauses.append("scan_out = CASE WHEN scan_out IS NULL OR scan_out = '0000-00-00 00:00:00' THEN %s ELSE scan_out END")
                params.extend([scan_out_final, scan_out_final])
                print(f"[UPDATE CANDIDATE] scan_out = {scan_out_final}")

            # kalau tidak ada yang diubah (kosong atau diblok oleh threshold), skip record ini
            if not set_clauses:
                print("[SKIP] No valid scan_in / scan_out after threshold processing")
                continue

            # query update: gunakan safe update
            update_sql = f"""
                UPDATE shift_result_new
                SET {', '.join(set_clauses)}
                WHERE pegawai_id = %s AND tgl_shift = %s
            """

            params.extend([pegawai_id, tanggal_shift])

            cur.execute(update_sql, tuple(params))
            conn.commit()

            # kalau ada baris yang berubah, tambah counter
            if cur.rowcount > 0:
                updated_count += 1
                print("[SUCCESS] Row updated")
            else:
                print("[INFO] No matching row or data already existed")

    except Exception as e:
        print(f"[ERROR] {e}")
        flash(f'Terjadi kesalahan saat mengupdate data: {e}', 'error')

    finally:
        # tutup koneksi db
        if conn.is_connected():
            cur.close()
            conn.close()
            print("[DB] Connection closed")

    print(f"\n[TOTAL UPDATED] {updated_count} record(s)")
    print("========== END DEBUG update_shift_result ==========\n")

    if updated_count > 0:
        flash(f'Berhasil mengupdate {updated_count} data absensi!', 'success')
    elif not request.form.get('selected_records'):
        # Just in case, this is handled above, but if it reaches here and count is 0
        flash('Tidak ada data absensi yang berhasil diupdate.', 'warning')

    # kembali ke halaman att_log dengan filter yang sama
    return redirect(url_for(
        'data_absen_fingerspot.data_absen_fingerspot',
        start_date=request.form.get('start_date'),
        end_date=request.form.get('end_date'),
        division=request.form.get('division')
    ))




