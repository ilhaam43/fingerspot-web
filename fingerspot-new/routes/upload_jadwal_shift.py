from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, jsonify
import pandas as pd
import os
import json
from datetime import datetime, timedelta, date, time
from werkzeug.utils import secure_filename
from utils.db import get_db_connection

upload_jadwal_shift_bp = Blueprint('upload_jadwal_shift', __name__)

def is_admin_qaqc():
    return (
        session.get('role') == 'admin' and
        (session.get('username') == 'admin-qaqc' or session.get('id') == 2)
    )

@upload_jadwal_shift_bp.route('/upload-jadwal-shift', methods=['GET', 'POST'])
def upload_jadwal_shift():
    # route ini untuk halaman upload jadwal shift via file excel
    # admin bisa pilih tanggal mulai dan divisi, lalu upload file xlsx
    # setelah upload, file disimpan sementara dan ditampilkan preview di halaman view_uploaded_excel

    # cek dulu user sudah login dan role admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    if request.method == 'POST':
        # ambil data dari form
        start_date = request.form['startDate']
        division = request.form['division']
        # simpan start_date ke session supaya bisa dipakai di halaman preview
        session['start_date'] = start_date

        # kalau user admin qaqc, hanya boleh pilih QAQC atau TA
        # kalau pilih divisi lain, langsung balik ke halaman ini
        if is_admin_qaqc() and division not in ['QAQC', 'TA', 'QAQC-TA']:
            return redirect(request.url)

        # cek apakah ada file yang diupload
        if 'file' not in request.files:
            return redirect(request.url)
        
        file = request.files['file']
        # kalau nama file kosong (user tidak pilih file), balik lagi
        if file.filename == '':
            return redirect(request.url)
        
        # pastikan file berakhiran .xlsx
        if file and file.filename.endswith('.xlsx'):
            # simpan file ke folder UPLOAD_FOLDER
            filename = os.path.join(current_app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filename)
            # simpan nama file dan divisi ke session untuk preview
            session['uploaded_file'] = file.filename
            session['division'] = division
            # redirect ke halaman preview data excel yang baru diupload
            return redirect(url_for('upload_jadwal_shift.view_jadwal_shift', filename=file.filename))
        else:
            # kalau bukan xlsx, balik ke halaman upload
            return redirect(request.url)
    
    # kalau method GET, tampilkan halaman form upload
    return render_template('upload-jadwal-shift-excel.html')



@upload_jadwal_shift_bp.route('/view-jadwal-shift/<filename>')
def view_jadwal_shift(filename):
    # route ini untuk preview isi file excel jadwal shift yang baru diupload
    # data ditampilkan dalam tabel html sebelum disimpan ke database

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    # ambil start_date dan division dari session (disimpan saat upload)
    start_date_str = session.get('start_date')
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    division = session.get('division')
    
    # path file yang diupload
    file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    
    # panggil fungsi read_and_process_excel untuk baca dan proses excel
    # map_to_names=True supaya kode shift diganti jadi nama shift (misal LB jadi Libur)
    combined_df = read_and_process_excel(file_path, start_date, division, map_to_names=True)
    
    # ubah dataframe jadi list dict untuk dikirim ke template
    records = combined_df.to_dict('records')
    headers = combined_df.columns
    
    # tampilkan halaman preview tabel
    return render_template('view-data-upload-jadwal.html', headers=headers, records=records)


           
def read_and_process_excel(file_path, start_date, division, map_to_names=False):
    # fungsi ini membaca file excel jadwal shift, proses per sheet
    # filter pegawai berdasarkan divisi, mapping shift, lalu gabung jadi satu dataframe besar
    
    # baca semua sheet di excel
    xls = pd.ExcelFile(file_path)
    sheet_names = xls.sheet_names
    all_data = []

    # load mapping shift khusus dari json
    shift_mappings = load_shift_mappings()
    # mapping default kalau kode shift tidak ada di mapping khusus
    default_mapping = {'LB': 10, 'D': 9, 'CT': 8, 'S': 11}

    # buka koneksi db untuk ambil data pegawai dan jam kerja
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # tentukan daftar divisi yang boleh diambil
    # kalau gabungan (HDTI-HDBS atau QAQC-TA), pakai list
    if division == 'HDTI-HDBS':
        divisi_list = ['HDTI', 'HDBS']
    elif division == 'QAQC-TA':
        divisi_list = ['QAQC', 'TA']
    else:
        divisi_list = [division]

    # kalau user admin qaqc, batasi hanya QAQC dan TA
    if is_admin_qaqc():
        divisi_list = [d for d in divisi_list if d in ['QAQC', 'TA']]

    # buat query ambil pegawai dari divisi yang dipilih
    placeholders = ','.join(['%s'] * len(divisi_list))
    query_pegawai = f"""
        SELECT p.pegawai_nip, p.pegawai_nama
        FROM pegawai p
        JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
        WHERE pb1.pembagian1_nama IN ({placeholders})
    """

    cursor.execute(query_pegawai, tuple(divisi_list))
    # buat dictionary nip → nama pegawai
    division_employees = {
        row['pegawai_nip']: row['pegawai_nama']
        for row in cursor.fetchall()
    }
    cursor.close()

    # ambil mapping jk_id → nama shift untuk preview (kalau map_to_names=True)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT jk_id, jk_name FROM jam_kerja")
    jam_kerja_map = {row['jk_id']: row['jk_name'] for row in cursor.fetchall()}
    cursor.close()
    conn.close()

    # proses tiap sheet di excel
    for sheet in sheet_names:
        # baca sheet jadi dataframe
        df = pd.read_excel(xls, sheet_name=sheet)

        try:
            # ambil kolom NIP, ubah ke string
            nip = df['NIP'].dropna().astype(int).astype(str)
        except KeyError:
            # kalau tidak ada kolom NIP, skip sheet ini
            continue

        # filter nip yang ada di divisi yang dipilih
        nip = nip[nip.isin(division_employees.keys())]
        # ambil data shift mulai kolom ke-6 (index 5)
        shift_data = df.iloc[nip.index, 5:]

        # cek apakah ada mapping shift khusus untuk nip-nip ini
        shift_mapping = {}
        for key, value in shift_mappings.items():
            if any(nip_value in value['nips'] for nip_value in nip):
                shift_mapping = value['shift_mapping']
                break

        # kalau ada mapping khusus, ganti kode shift sesuai mapping
        if shift_mapping:
            for row in range(shift_data.shape[0]):
                for col in range(shift_data.shape[1]):
                    cell_value = str(shift_data.iat[row, col])
                    if cell_value in shift_mapping:
                        shift_data.iat[row, col] = shift_mapping[cell_value]

        # ganti kode default (LB, D, CT, S) jadi jk_id
        for original_value, mapped_value in default_mapping.items():
            shift_data = shift_data.replace(original_value, mapped_value)

        # ubah semua ke integer, isi kosong jadi 0
        shift_data = shift_data.apply(pd.to_numeric, errors='coerce').fillna(0).astype(int)

        # buat dataframe baru dengan NIP dan Nama
        data = pd.DataFrame(shift_data.values, columns=df.columns[5:])
        nip_values = nip.values
        data.insert(0, 'NIP', nip_values)
        data.insert(1, 'Nama', [division_employees.get(n, 'N/A') for n in nip_values])
        all_data.append(data)

    # kalau tidak ada data sama sekali, kembalikan dataframe kosong
    if not all_data:
        return pd.DataFrame()

    # gabung semua sheet jadi satu dataframe besar
    combined_df = pd.concat(all_data, ignore_index=True)

    # hitung end_date berdasarkan jumlah kolom shift
    end_date = start_date + timedelta(days=combined_df.shape[1] - 3)
    # buat kolom tanggal dari start_date sampai end_date
    date_range = pd.date_range(start_date, end_date, freq='D').strftime('%Y/%m/%d')
    # ganti nama kolom jadi NIP, Nama, dan tanggal-tanggal
    combined_df.columns = ['NIP', 'Nama'] + date_range.tolist()

    # kalau map_to_names=True (untuk preview), ganti jk_id jadi nama shift
    if map_to_names:
        for col in date_range:
            combined_df[col] = combined_df[col].map(jam_kerja_map).fillna('-')

    # urutkan berdasarkan nama pegawai
    return combined_df.sort_values(by='Nama')




@upload_jadwal_shift_bp.route('/update-jadwal-shift', methods=['POST'])
def update_jadwal_shift():
    # route ini dipanggil setelah admin melihat preview excel jadwal shift
    # tugasnya: simpan data jadwal dari excel ke tabel shift_result_new
    # hanya update kalau jk_id sebelumnya kosong atau 0 (tidak overwrite data existing)

    # pastikan user admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    # ambil divisi dari session (disimpan saat upload)
    division = session.get('division')

    # admin qaqc hanya boleh proses QAQC dan TA
    # kalau divisi lain, balik ke halaman upload
    if is_admin_qaqc() and division not in ['QAQC', 'TA', 'QAQC-TA']:
        return redirect(url_for('upload_jadwal_shift.upload_jadwal_shift'))

    connection = None
    cursor = None
    
    try:
        # ambil nama file dan start_date dari session
        file_name = session.get('uploaded_file')
        start_date_str = session.get('start_date')
        
        # kalau tidak ada file atau tanggal, balik ke upload
        if not file_name:
            return redirect(url_for('upload_jadwal_shift.upload_jadwal_shift'))
        
        if not start_date_str:
            return redirect(url_for('upload_jadwal_shift.upload_jadwal_shift'))
        
        # ubah string tanggal jadi objek date
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')

        # path file excel yang sudah diupload
        file_path = os.path.join(current_app.config['UPLOAD_FOLDER'], file_name)
        
        # baca dan proses excel tanpa mapping nama (kita butuh jk_id numerik)
        combined_df = read_and_process_excel(file_path, start_date, division, map_to_names=False)
        
        # buka koneksi db
        connection = get_db_connection()
        cursor = connection.cursor()

        # loop tiap baris di dataframe (satu baris = satu pegawai)
        for index, row in combined_df.iterrows():
            nip = row['NIP']
            # kolom mulai dari index 2 adalah tanggal-tanggal
            for date in combined_df.columns[2:]:
                shift = row[date]
                # kalau shift bukan 0 (ada jadwal), proses
                if shift != 0:
                    # ubah string tanggal jadi objek date
                    date_obj = datetime.strptime(date, '%Y/%m/%d').date()

                    # cari pegawai_id berdasarkan nip
                    cursor.execute("SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s", (nip,))
                    result = cursor.fetchone()
                    if result:
                        pegawai_id = result[0]

                        # query upsert: insert kalau belum ada, update hanya kalau jk_id kosong/0
                        upsert_query = """
                        INSERT INTO shift_result_new (pegawai_id, tgl_shift, jk_id)
                        VALUES (%s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                        jk_id = CASE WHEN jk_id IS NULL OR jk_id = 0 THEN VALUES(jk_id) ELSE jk_id END
                        """
                        cursor.execute(upsert_query, (pegawai_id, date_obj, shift))
                        # commit per baris supaya aman kalau error di tengah jalan
                        connection.commit()
                    else:
                        # nip tidak ditemukan, skip saja
                        pass
    
        # commit setelah loop selesai, atau sudah per baris.
        return jsonify({'status': 'success', 'message': 'Jadwal shift berhasil disimpan ke database!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'Terjadi kesalahan saat menyimpan jadwal: {str(e)}'})
    
    finally:
        # tutup cursor dan koneksi supaya tidak memory leak
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()

def load_shift_mappings():
    """Load shift mappings with encoding handling"""
    encodings = ['utf-8', 'latin-1', 'cp1252']
    
    for encoding in encodings:
        try:
            with open('shift_mappings.json', 'r', encoding=encoding) as file:
                return json.load(file)
        except UnicodeDecodeError:
            continue
        except FileNotFoundError:
            return {}
        except Exception as e:
            return {}
    
    return {}

def get_employee_data():
    """Get employee data with proper error handling"""
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT pegawai_nip, pegawai_nama FROM pegawai")
        employees = cursor.fetchall()
        return {employee['pegawai_nip']: employee['pegawai_nama'] for employee in employees}
    except Exception as e:
        return {}
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

def get_jam_kerja_data():
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT jk_id, jk_name FROM jam_kerja")
        jam_kerja = cursor.fetchall()
        return {str(jk['jk_id']): jk['jk_name'] for jk in jam_kerja}
    except Error as e:
        return {}
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()








