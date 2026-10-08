from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, jsonify
import pandas as pd
import os
import json
from datetime import datetime, timedelta, date, time
from werkzeug.utils import secure_filename
from utils.db import get_db_connection

upload_mesin_lama_bp = Blueprint('upload_mesin_lama', __name__)

@upload_mesin_lama_bp.route('/upload-csv-mesin-lama', methods=['GET', 'POST'])
def upload_csv_mesin_lama():
    # route ini untuk upload file csv dari mesin absensi lama (mesin bawah)
    # admin pilih divisi, upload csv, lalu preview hasil pengolahan di view_csv

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    if request.method == 'POST':
        # ambil divisi dari form
        division = request.form.get('division')
        # cek ada file yang diupload
        if 'file' not in request.files:
            return redirect(request.url)
        file = request.files['file']
        # kalau tidak pilih file, balik lagi
        if file.filename == '':
            return redirect(request.url)
        if file:
            # pastikan divisi dipilih
            if not division:
                return redirect(request.url)
            # simpan file ke folder uploads
            filename = os.path.join(current_app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filename)
            # simpan divisi ke session untuk proses selanjutnya
            session['division_old_machine'] = division
            # redirect ke halaman preview hasil csv
            return redirect(url_for('upload_mesin_lama.hasil_upload_csv_mesin_lama', filename=file.filename))
    # kalau GET, tampilkan form upload
    return render_template('upload-csv-mesin-lama.html')

def load_nip_mapping():
    # fungsi kecil untuk baca mapping nip lama ke nip baru dari json
    # dipakai di proses csv mesin lama supaya nip bisa cocok dengan data pegawai
    with open('mapping_nip_old_absen_to_real_nip.json', 'r') as file:
        return json.load(file)

@upload_mesin_lama_bp.route('/hasil-upload-csv-mesin-lama/<filename>')
def hasil_upload_csv_mesin_lama(filename):
    # route ini untuk preview hasil pengolahan file csv dari mesin absensi lama (mesin bawah)
    # tugasnya: baca csv, kelompokkan scan masuk/keluar per nip dan tanggal
    # mapping nip lama ke nip baru, filter berdasarkan divisi, lalu tampilkan dalam tabel html
    # setelah itu simpan hasil scan ke shift_result_new dan isi record kosong untuk semua pegawai

    # pastikan user sudah login sebagai admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    # ambil path file csv yang diupload
    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    # ambil divisi dari session (disimpan saat upload)
    division = session.get('division_old_machine')
    
    
    # coba baca csv dengan beberapa encoding supaya tidak error unicode
    encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1', 'utf-16']
    df = None
    
    for encoding in encodings:
        try:
            df = pd.read_csv(filepath, encoding=encoding)
            break
        except UnicodeDecodeError:
            # kalau encoding salah, coba yang berikutnya
            continue
        except Exception as e:
            # kalau error lain, skip saja
            continue
    
    # kalau semua encoding gagal, balik ke dashboard
    if df is None:
        return redirect(url_for('dashboard'))

    # ubah nama kolom supaya konsisten
    df.columns = ['No', 'User id', 'User name', 'Card No', 'Date', 'Time', 'IN/OUT', 'Function keys', 'Door']

   
    # ubah kolom Date jadi datetime (format umum mm/dd/yyyy)
    try:
        df['Date'] = pd.to_datetime(df['Date'], format='%m/%d/%Y', errors='coerce')
    except:
        # kalau format tidak cocok, pakai parsing otomatis
        df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    
  
    # ubah kolom Time jadi time object
    try:
        df['Time'] = pd.to_datetime(df['Time'], format='%H:%M:%S', errors='coerce').dt.time
    except:
        # kalau format salah, pakai parsing otomatis
        df['Time'] = pd.to_datetime(df['Time'], errors='coerce').dt.time

    
    # buang baris yang Date atau Time-nya invalid
    df = df.dropna(subset=['Date', 'Time'])

    # kelompokkan per nip dan tanggal
    # ambil nama pertama, scan_in = waktu paling awal, scan_out = waktu paling akhir
    grouped_df = df.groupby(['User id', 'Date']).agg(
        nama=('User name', 'first'),
        scan_in=('Time', 'min'),
        scan_out=('Time', 'max')
    ).reset_index()

    # tambah nomor urut
    grouped_df['No'] = range(1, len(grouped_df) + 1)

    # buat dataframe final dengan kolom yang dibutuhkan
    final_df = grouped_df[['No', 'User id', 'nama', 'Date', 'scan_in', 'scan_out']].copy()

    
    # ubah User id jadi integer (nip)
    try:
        final_df['User id'] = pd.to_numeric(final_df['User id'], errors='coerce').astype('Int64')
      
        final_df = final_df.dropna(subset=['User id'])
        final_df['User id'] = final_df['User id'].astype(int)
    except:
        # kalau gagal konversi, balik ke dashboard
        return redirect(url_for('dashboard'))

    # ubah nama kolom supaya sesuai standar
    final_df.columns = ['no', 'nip', 'nama', 'tanggal shift', 'scan in', 'scan out']

    # format tanggal jadi yyyy-mm-dd
    final_df['tanggal shift'] = final_df['tanggal shift'].dt.strftime('%Y-%m-%d')

    # siapkan list untuk simpan jk_id dari database
    results = []
    connection = get_db_connection()
    cursor = connection.cursor()

    # ambil jk_id yang sudah ada di database untuk tiap nip dan tanggal
    for index, row in final_df.iterrows():
        nip = row['nip']
        tanggal_shift = row['tanggal shift']

        query = '''
            SELECT jk.jk_id
            FROM pegawai p
            JOIN shift_result_new sr ON p.pegawai_id = sr.pegawai_id
            JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            WHERE p.pegawai_nip = %s AND sr.tgl_shift = %s
        '''
        cursor.execute(query, (nip, tanggal_shift))
        jk_id_result = cursor.fetchone()

        if jk_id_result:
            jk_id = jk_id_result[0]
            results.append((nip, tanggal_shift, jk_id))
        else:
            results.append((nip, tanggal_shift, None))

    # daftar jk_id shift malam yang butuh penanganan khusus
    malam_jk_ids = {7, 15, 18, 22}

    # proses ulang untuk tentukan scan_in dan scan_out final berdasarkan jk_id
    for index, row in final_df.iterrows():
        nip = row['nip']
        tanggal_shift = row['tanggal shift']
        
        # cari jk_id dari hasil query sebelumnya
        try:
            jk_id = [r[2] for r in results if r[0] == nip and r[1] == tanggal_shift][0]
        except IndexError:
            continue  # skip kalau tidak ketemu

        scan_in_final = None
        scan_out_final = None

        # === kondisi 1: shift id 4 (shift malam spesial) ===
        if jk_id == 4:
            # scan in: cari di hari H mulai jam 04:00 sampai tengah malam
            scan_in_min = df[
                (df['User id'] == nip) &
                (df['Date'] == pd.to_datetime(tanggal_shift)) &
                (df['Time'] >= pd.to_datetime('04:00:00').time()) &
                (df['Time'] <= pd.to_datetime('23:59:59').time())
            ]['Time'].min()

            if pd.notna(scan_in_min):
                scan_in_final = f"{tanggal_shift} {scan_in_min}"

            # scan out: prioritas hari H (sore/malam)
            scan_out_today = df[
                (df['User id'] == nip) &
                (df['Date'] == pd.to_datetime(tanggal_shift)) &
                (df['Time'] >= pd.to_datetime('14:00:00').time()) &
                (df['Time'] <= pd.to_datetime('23:59:59').time())
            ]['Time'].max()

            if pd.notna(scan_out_today):
                scan_out_final = f"{tanggal_shift} {scan_out_today}"
            else:
                # kalau tidak ada di hari H, cari di hari berikutnya (subuh/pagi)
                next_day = (pd.to_datetime(tanggal_shift) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
                
                scan_out_tomorrow = df[
                    (df['User id'] == nip) &
                    (df['Date'] == pd.to_datetime(next_day)) &
                    (df['Time'] >= pd.to_datetime('00:00:00').time()) &
                    (df['Time'] <= pd.to_datetime('03:59:00').time())
                ]['Time'].max()

                if pd.notna(scan_out_tomorrow):
                    scan_out_final = f"{next_day} {scan_out_tomorrow}"
                    
        # === kondisi 2: shift malam lainnya (7,15,18,22) ===
        elif jk_id in malam_jk_ids:
            # scan in: 11:00 sampai 23:59 di hari H
            scan_in_min = df[
                (df['User id'] == nip) &
                (df['Date'] == pd.to_datetime(tanggal_shift)) &
                (df['Time'] >= pd.to_datetime('11:00:00').time()) &
                (df['Time'] <= pd.to_datetime('23:59:59').time())
            ]['Time'].min()

            next_day = (pd.to_datetime(tanggal_shift) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')

            # scan out: besok 00:00 sampai 12:00 di hari H+1
            scan_out_max = df[
                (df['User id'] == nip) &
                (df['Date'] == pd.to_datetime(next_day)) &
                (df['Time'] >= pd.to_datetime('00:00:00').time()) &
                (df['Time'] <= pd.to_datetime('12:00:00').time())
            ]['Time'].max()

            if pd.notna(scan_in_min) and scan_in_min:
                scan_in_final = f"{tanggal_shift} {scan_in_min}"
            
            if pd.notna(scan_out_max) and scan_out_max:
                scan_out_final = f"{next_day} {scan_out_max}"

        # === kondisi 3: shift normal/pagi ===
        else:
            # scan in: 03:00 sampai 13:59 hari H
            scan_in_min = df[
                (df['User id'] == nip) &
                (df['Date'] == pd.to_datetime(tanggal_shift)) &
                (df['Time'] >= pd.to_datetime('03:00:00').time()) &
                (df['Time'] <= pd.to_datetime('13:59:59').time())
            ]['Time'].min()

            # scan out: 14:00 sampai 23:59 hari H
            scan_out_max = df[
                (df['User id'] == nip) &
                (df['Date'] == pd.to_datetime(tanggal_shift)) &
                (df['Time'] >= pd.to_datetime('14:00:00').time()) &
                (df['Time'] <= pd.to_datetime('23:59:59').time())
            ]['Time'].max()

            if pd.notna(scan_in_min) and scan_in_min:
                scan_in_final = f"{tanggal_shift} {scan_in_min}"
            
            if pd.notna(scan_out_max) and scan_out_max:
                scan_out_final = f"{tanggal_shift} {scan_out_max}"

        # simpan hasil scan in/out final ke dataframe
        final_df.at[index, 'scan in'] = scan_in_final
        final_df.at[index, 'scan out'] = scan_out_final

    # load mapping nip lama ke nip baru
    nip_mapping = load_nip_mapping()

    # urutkan berdasarkan nama sebelum filter divisi
    final_df = final_df.sort_values(by='nama').reset_index(drop=True)
    final_df['no'] = final_df.index + 1
    
    # kalau ada divisi, filter nip hanya yang ada di divisi tersebut
    if division:
        conn_div = get_db_connection()
        cursor_div = conn_div.cursor(dictionary=True)
        query_pegawai = """
            SELECT p.pegawai_nip
            FROM pegawai p
            JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE pb1.pembagian1_nama = %s
        """
        cursor_div.execute(query_pegawai, (division,))
        division_nips = {row['pegawai_nip'] for row in cursor_div.fetchall()}
        cursor_div.close()
        conn_div.close()
        
        # ganti nip lama ke nip baru pakai mapping
        final_df['nip'] = final_df['nip'].astype(str).replace(nip_mapping)
        # filter hanya nip yang ada di divisi
        final_df = final_df[final_df['nip'].isin(division_nips)]

    # ganti lagi nip pakai mapping (untuk konsistensi)
    final_df['nip'] = final_df['nip'].astype(str).replace(nip_mapping)

    
    # buka koneksi db untuk simpan hasil scan ke shift_result_new
    connection = get_db_connection()
    cursor = connection.cursor()

    inserted_count = 0
    updated_count = 0
    skipped_count = 0

    for index, row in final_df.iterrows():
        nip = row['nip']
        tanggal_shift = row['tanggal shift']
        scan_in = row['scan in']
        scan_out = row['scan out']

        # kalau scan null, pakai default datetime nol
        db_scan_in = scan_in if scan_in is not None else '0000-00-00 00:00:00'
        db_scan_out = scan_out if scan_out is not None else '0000-00-00 00:00:00'

        # cari pegawai_id berdasarkan nip
        cursor.execute('SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s', (nip,))
        pegawai_id_result = cursor.fetchone()

        if pegawai_id_result:
            pegawai_id = pegawai_id_result[0]

            # cek apakah record sudah ada di shift_result_new
            cursor.execute('''
                SELECT * FROM shift_result_new 
                WHERE pegawai_id = %s AND tgl_shift = %s
            ''', (pegawai_id, tanggal_shift))

            if cursor.fetchone():
                # kalau sudah ada, update scan in/out tanpa menimpa data yang sudah ada (seperti DCAP)
                cursor.execute('''
                    UPDATE shift_result_new
                    SET scan_in_source = CASE WHEN (scan_in IS NULL OR scan_in = '0000-00-00 00:00:00') AND %s != '0000-00-00 00:00:00' THEN 'Mesin Lama' ELSE scan_in_source END,
                        scan_in = CASE WHEN scan_in IS NULL OR scan_in = '0000-00-00 00:00:00' THEN %s ELSE scan_in END,
                        scan_out_source = CASE WHEN (scan_out IS NULL OR scan_out = '0000-00-00 00:00:00') AND %s != '0000-00-00 00:00:00' THEN 'Mesin Lama' ELSE scan_out_source END,
                        scan_out = CASE WHEN scan_out IS NULL OR scan_out = '0000-00-00 00:00:00' THEN %s ELSE scan_out END 
                    WHERE pegawai_id = %s AND tgl_shift = %s
                ''', (db_scan_in, db_scan_in, db_scan_out, db_scan_out, pegawai_id, tanggal_shift))
                updated_count += 1
            else:
                # kalau belum ada, insert record baru
                cursor.execute('''
                    INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out, scan_in_source, scan_out_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (
                    pegawai_id, tanggal_shift, db_scan_in, db_scan_out,
                    'Mesin Lama' if db_scan_in else None,
                    'Mesin Lama' if db_scan_out else None
                ))
                inserted_count += 1
        else:
            skipped_count += 1

    # ambil semua tanggal shift yang ada di csv
    all_shift_dates = final_df['tanggal shift'].unique()
    # ambil semua pegawai aktif
    cursor.execute("SELECT pegawai_id FROM pegawai")
    all_pegawai = cursor.fetchall()

    # isi record kosong untuk semua pegawai di semua tanggal shift
    # supaya tidak ada pegawai yang hilang di rekap
    for tgl_shift in all_shift_dates:
        for pegawai in all_pegawai:
            pegawai_id = pegawai[0]

            # skip pegawai id 0 dan 1 (mungkin dummy/system)
            if pegawai_id in [0, 1]:
                continue

            # cek apakah record sudah ada
            check_query = "SELECT 1 FROM shift_result_new WHERE pegawai_id = %s AND tgl_shift = %s"
            cursor.execute(check_query, (pegawai_id, tgl_shift))
            exists = cursor.fetchone()

            if not exists:
                # kalau belum ada, insert record kosong
                insert_query = """
                INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out)
                VALUES (%s, %s, '0000-00-00 00:00:00', '0000-00-00 00:00:00')
                """
                cursor.execute(insert_query, (pegawai_id, tgl_shift))
                connection.commit()

    # commit semua perubahan
    connection.commit()

    # tutup koneksi
    cursor.close()
    connection.close()
    
    # ubah dataframe jadi html table untuk tampilan
    table_html = final_df.to_html(classes='data', header=True, index=False)

    # tampilkan halaman hasil dengan statistik upload
    return render_template(
        'hasil-upload-csv-mesin-lama.html',
        tables=table_html,
        filename=filename,
        inserted=inserted_count,
        updated=updated_count,
        skipped=skipped_count,
        total_rows=len(final_df)
    )


