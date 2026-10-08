from flask import Flask, render_template, request, redirect, url_for, flash, send_file, session, jsonify
import pandas as pd
import mysql.connector
from mysql.connector import Error
from datetime import time, datetime, timedelta, date
import os
import csv
import io
import xlsxwriter
import locale
import pdfkit

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32)
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
FILES_FOLDER = 'files'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['FILES_FOLDER'] = FILES_FOLDER


for time_locale in ('id_ID.UTF-8', 'indonesian_indonesia', 'C.UTF-8', 'C'):
    try:
        locale.setlocale(locale.LC_TIME, time_locale)
        break
    except locale.Error:
        continue
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)

def get_db_connection():
   
    json_file_path = os.path.join(os.path.dirname(__file__), 'connection.json')
    

    if os.environ.get('DB_HOST'):
        config = {
            'user': os.environ.get('DB_USER', 'fingerspot'),
            'password': os.environ.get('DB_PASSWORD', ''),
            'host': os.environ['DB_HOST'],
            'database': os.environ.get('DB_NAME', 'fin_pro'),
            'port': int(os.environ.get('DB_PORT', '3306')),
            'charset': os.environ.get('DB_CHARSET', 'utf8'),
        }
    else:
        with open(json_file_path, 'r') as json_file:
            config = json.load(json_file)
    

    return mysql.connector.connect(
        user=config['user'],
        password=config['password'],
        host=config['host'],
        database=config['database'],
        port=config['port'],
        charset=config['charset']
    )



@app.errorhandler(404)
def page_not_found(error):
    return render_template('404.html'), 404


@app.errorhandler(403)
def page_not_found(error):
    return render_template('403.html'), 403

@app.errorhandler(405)
def page_not_found(error):
    return render_template('405.html'), 405



@app.route('/api/absen', methods=['POST'])
def post_absen():
    # route ini adalah api khusus untuk terima data absensi real-time
    # dari mesin fingerspot via aplikasi pihak ketiga (bukan app bawaan fingerspot)
    # data dikirim dalam format json, lalu disimpan ke tabel att_log pakai INSERT IGNORE
    # supaya kalau data duplikat (sn + scan_date + pin sama), tidak error dan tidak duplikat

    # ambil data json dari body request
    data = request.get_json()

    # daftar field wajib yang harus ada di json
    required_fields = ['sn', 'scan_date', 'pin', 'verifymode', 'inoutmode', 'reserved', 'work_code', 'att_id']
    
    # cek apakah semua field wajib ada
    # kalau kurang satu saja, balas error 400
    if not all(field in data for field in required_fields):
        return jsonify({'status': 'error', 'message': 'Missing fields'}), 400

    # buka koneksi db
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # query insert ignore: kalau data sudah ada (kombinasi unik sn, scan_date, pin), skip saja
        query = """
        INSERT IGNORE INTO att_log (sn, scan_date, pin, verifymode, inoutmode, reserved, work_code, att_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        
        # jalankan query dengan data dari json
        cursor.execute(query, (
            data['sn'], data['scan_date'], data['pin'], data['verifymode'],
            data['inoutmode'], data['reserved'], data['work_code'], data['att_id']
        ))

        # commit supaya data tersimpan
        conn.commit()

        # balas success kalau ok
        return jsonify({'status': 'success'}), 200
    except Exception as e:
        # kalau ada error (misal db mati atau data invalid), balas error 500
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        # pastikan cursor dan koneksi ditutup
        cursor.close()
        conn.close()

     

@app.route('/update_reload', methods=['POST'])
def update_shift():
    # route ini untuk fitur copy jadwal shift massal oleh admin
    # admin pilih tanggal referensi dan target, lalu copy jk_id dari referensi ke target
    # bisa untuk satu divisi atau semua (kecuali nip tertentu)
    # ada opsi apakah ikut copy shift BIFAST atau tidak
    # juga bisa pilih apakah cuti/dinas/sakit/izin ikut dicopy atau tidak

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    try:
        # ambil data dari form
        target_date = request.form['target_date']
        reference_date = request.form['reference_date']
        # checkbox apakah ikut update shift bifast
        update_bifast_shifts = request.form.get('update_bifast_shifts') == 'true'
        division = request.form.get('division')
        
        # checkbox apakah cuti/dinas/sakit/izin ikut dicopy
        cuti = request.form.get('cuti') == 'true'
        dinas = request.form.get('dinas') == 'true'
        sakit = request.form.get('sakit') == 'true'
        izin = request.form.get('izin') == 'true'

        # buka koneksi db
        connection = get_db_connection()
        cursor = connection.cursor()


        # kumpulin jk_id yang tidak boleh dicopy (yang dikecualikan)
        excluded_jk_ids = []
        
        # kalau cuti tidak dicentang, jangan copy jk_id cuti (8)
        if not cuti:
            excluded_jk_ids.extend([8])  
        if not dinas:
            excluded_jk_ids.extend([9])   # dinas
        if not sakit:
            excluded_jk_ids.extend([11])  # sakit
        if not izin:
            excluded_jk_ids.extend([34])  # izin (asumsi 34 adalah kode izin)
        
        
        # jk_id yang selalu dikecualikan (shift malam, overtime, dll)
        base_excluded_jk_ids = [5, 6, 7, 32, 33, 30]
        # gabung semua yang dikecualikan, hilangkan duplikat
        all_excluded_jk_ids = base_excluded_jk_ids + excluded_jk_ids
        all_excluded_jk_ids = list(set(all_excluded_jk_ids))

        # kalau update bifast nyala, copy semua termasuk bifast
        if update_bifast_shifts:
            # query update: copy jk_id dari tanggal referensi ke target
            # join dengan pegawai supaya bisa filter divisi atau exclude nip
            query = """
            UPDATE 
                shift_result_new AS sr14
            JOIN 
                pegawai ON sr14.pegawai_id = pegawai.pegawai_id
            JOIN 
                shift_result_new AS sr11 ON sr14.pegawai_id = sr11.pegawai_id
            SET 
                sr14.jk_id = sr11.jk_id
            WHERE 
                sr14.tgl_shift = %s 
                AND sr11.tgl_shift = %s
            """
            params = [target_date, reference_date]
            # kalau ada divisi, tambah filter divisi
            if division:
                query += " AND pegawai.pembagian1_id = (SELECT pembagian1_id FROM pembagian1 WHERE pembagian1_nama = %s)"
                params.append(division)
            else:
                # kalau tidak ada divisi, exclude nip-nip tertentu (mungkin pegawai spesial)
                query += """ AND pegawai.pegawai_nip NOT IN (
                    '2752', '2864', '3086', '2087', '2353', '1274', '1555', 
                    '1905', '1273', '2014', '1344', '2542', '2763', '1767', 
                    '3272', '2764'
                )"""
        else:
            # kalau bifast tidak dicentang, copy hanya shift normal (kecuali excluded jk_ids)
            excluded_jk_ids_str = ','.join(map(str, all_excluded_jk_ids))
            query = f"""
            UPDATE 
                shift_result_new AS sr14
            JOIN 
                pegawai ON sr14.pegawai_id = pegawai.pegawai_id
            JOIN 
                shift_result_new AS sr11 ON sr14.pegawai_id = sr11.pegawai_id
            SET 
                sr14.jk_id = sr11.jk_id
            WHERE 
                sr14.tgl_shift = %s 
                AND sr11.tgl_shift = %s
                AND sr11.jk_id NOT IN ({excluded_jk_ids_str})
            """
            params = [target_date, reference_date]
            if division:
                query += " AND pegawai.pembagian1_id = (SELECT pembagian1_id FROM pembagian1 WHERE pembagian1_nama = %s)"
                params.append(division)
            else:
                query += """ AND pegawai.pegawai_nip NOT IN (
                    '2752', '2864', '3086', '2087', '2353', '1274', '1555', 
                    '1905', '1273', '2014', '1344', '2542', '2763', '1767', 
                    '3272', '2764'
                )"""


        # jalankan query update massal
        cursor.execute(query, tuple(params))
        # commit perubahan
        connection.commit()


    except Exception as e:
        # kalau error, skip saja (tidak crash)
        pass
    finally:
        # tutup cursor dan koneksi
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'connection' in locals() and connection:
            connection.close()
    
    # kembali ke halaman sebelumnya (biasanya dashboard atau form)
    return redirect(request.referrer)



import pytz
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username_or_nip = request.form['username']
        password = request.form['password']
        login_time = datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%Y-%m-%d %H:%M:%S %Z')

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT * FROM login_web WHERE username = %s AND password = %s", (username_or_nip, password))
        admin = cursor.fetchone()

        if admin:
            session['loggedin'] = True
            session['role'] = 'admin'
            session['id'] = admin['id_akun']
            session['username'] = admin['username']
            session.permanent = True
            cursor.close()
            conn.close()
            return redirect(url_for('index'))

        cursor.execute("SELECT * FROM pegawai WHERE pegawai_nip = %s AND pegawai_pwd = %s AND pegawai_status IN (1, 3, 4)", (username_or_nip, password))
        user = cursor.fetchone()

        if user:
            session['loggedin'] = True
            session['role'] = 'pegawai'
            session['id'] = user['pegawai_id']
            session['nip'] = user['pegawai_nip']
            session['nik_lintas'] = user['pegawai_nik_lintas']
            session['nama'] = user['pegawai_nama']
            session['force_reset'] = (user['pegawai_status'] == 4)
            session.permanent = True
            cursor.close()
            conn.close()
            if session['force_reset']:
                return render_template('login.html', force_reset=True)
            return redirect(url_for('beranda'))
        else:
            cursor.close()
            conn.close()
            return render_template('login.html', force_reset=False)

    preview = request.args.get('new') == '1'
    template_name = 'login_new.html' if preview else 'login.html'

    return render_template(template_name, force_reset=False)

@app.route('/forgot-password', methods=['POST'])
def forgot_password():
    import string
    import random
    data = request.get_json()
    nip = data.get('nip')
    card_id = data.get('card_id')

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

 
        if not nip or not card_id:
            return jsonify({'success': False, 'error': 'NIP sama ID kartu Lintasarta harus diisi'})
        if not card_id.isdigit() or len(card_id) != 8:
            return jsonify({'success': False, 'error': 'ID kartu Lintasarta harus 8 digit'})

       
        cursor.execute("SELECT pegawai_nip FROM pegawai WHERE pegawai_nip = %s AND pegawai_status = 1", (nip,))
        user = cursor.fetchone()

        if not user:
            return jsonify({'success': False, 'error': 'NIP enggak ditemukan atau akun enggak aktif'})

       
        if card_id[-4:] != nip:
            return jsonify({'success': False, 'error': 'ID kartu enggak sesuai sama NIP'})

     
        characters = string.ascii_letters + string.digits + "!@#$%^&*()_+-=[]{}|;:,.<>?"
        random_password = ''.join(random.choice(characters) for _ in range(6))

      
        cursor.execute("UPDATE pegawai SET pegawai_status = 3, pegawai_pwd = %s WHERE pegawai_nip = %s", (random_password, nip))
        conn.commit()
        return jsonify({'success': True, 'message': 'Password telah direset. Tunggu 1x24 jam admin akan memprosesnya.'})

    except Error as e:
        return jsonify({'success': False, 'error': f'Gagal memproses: {str(e)}'})

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/reset-password-requests', methods=['GET'])
def reset_password_requests():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Mau apa kemari?'}), 403

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        query = """
            SELECT p.pegawai_nip, p.pegawai_nama, b1.pembagian1_nama, b2.pembagian2_nama
            FROM pegawai p
            LEFT JOIN pembagian1 b1 ON p.pembagian1_id = b1.pembagian1_id
            LEFT JOIN pembagian2 b2 ON p.pembagian2_id = b2.pembagian2_id
            WHERE p.pegawai_status = 3
            ORDER BY p.pegawai_nama ASC
        """
        cursor.execute(query)
        pegawai = cursor.fetchall()
        return jsonify({'success': True, 'pegawai': pegawai})

    except Error as e:
        return jsonify({'success': False, 'error': f'Gagal mengambil data: {str(e)}'})

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/reset-user-password', methods=['POST'])
def reset_user_password():
    data = request.get_json()
    nip = data.get('nip')

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

       
        cursor.execute("SELECT pegawai_nip FROM pegawai WHERE pegawai_nip = %s AND pegawai_status = 3", (nip,))
        user = cursor.fetchone()

        if not user:
            return jsonify({'success': False, 'error': 'NIP enggak ditemukan atau akun enggak dalam status reset'})

        
        cursor.execute("UPDATE pegawai SET pegawai_pwd = %s, pegawai_status = 4 WHERE pegawai_nip = %s", ('Lintas@rta123', nip))
        conn.commit()
        return jsonify({'success': True, 'message': 'Password berhasil direset ke password default (Lintas@rta123)'})

    except Error as e:
        return jsonify({'success': False, 'error': f'Gagal reset password: {str(e)}'})

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()

@app.route('/set-new-password', methods=['POST'])
def set_new_password():
    import re
    data = request.get_json()
    nip = session.get('nip')
    new_password = data.get('new_password')
    confirm_password = data.get('confirm_password')

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        
        if not new_password or not confirm_password:
            return jsonify({'success': False, 'error': 'Semua kolom password harus diisi'})
        if new_password != confirm_password:
            return jsonify({'success': False, 'error': 'Password baru dan konfirmasi password enggak cocok'})

        if len(new_password) < 6:
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})
        if not re.search(r"[A-Z]", new_password):
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})
        if not re.search(r"[0-9]", new_password):
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})
        if not re.search(r"[!@#$%^&*()_+\-=\[\]{};:'\"\\|,.<>/?]", new_password):
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})

        
        cursor.execute("UPDATE pegawai SET pegawai_pwd = %s, pegawai_status = 1 WHERE pegawai_nip = %s", (new_password, nip))
        conn.commit()
        
        session['force_reset'] = False
        return jsonify({'success': True, 'message': 'Password berhasil diubah', 'redirect': url_for('beranda')})

    except Error as e:
        return jsonify({'success': False, 'error': f'Gagal mengubah password: {str(e)}'})

    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()



@app.route('/logout')
def logout():
    login_time = datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%Y-%m-%d %H:%M:%S %Z')
    role = session.get('role')
    user_info = "Unknown"
    
    if role == 'admin':
        user_info = f"Admin, Username={session.get('username', 'Unknown')}"
    elif role == 'pegawai':
        user_info = f"Pegawai, NIP={session.get('nip', 'Unknown')}, Name={session.get('nama', 'Unknown')}"
    
    session.clear()
    return redirect(url_for('login'))

@app.route('/update_notes', methods=['POST'])
def update_notes():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized'}), 403
    
    data = request.get_json()
    pegawai_id = data.get('pegawai_id')
    tgl_shift = data.get('tgl_shift')
    notes = data.get('notes')
    
    if not pegawai_id or not tgl_shift:
        return jsonify({'success': False, 'message': 'Missing data'}), 400
        
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        query = "UPDATE shift_result_new SET notes = %s WHERE pegawai_id = %s AND tgl_shift = %s"
        cursor.execute(query, (notes, pegawai_id, tgl_shift))
        conn.commit()
        return jsonify({'success': True, 'message': 'Notes updated'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        if 'cursor' in locals() and cursor:
            cursor.close()
        if 'conn' in locals() and conn:
            conn.close()



@app.route('/')
def index():
    from datetime import datetime, timedelta, date, time
    
    # route ini adalah dashboard utama admin
    # tugasnya: tampilkan rekap absensi harian semua pegawai dalam rentang tanggal tertentu
    # plus statistik, filter nama/divisi/keterangan, dan opsi tampilkan PM (pegawai id 180)
    
    # pertama-tama cek apakah user sudah login dan benar-benar admin
    # kalau belum, langsung arahkan ke halaman login
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    # cek apakah user ini admin qaqc
    # admin qaqc dibatasi hanya boleh lihat pegawai dari divisi QAQC dan TA
    is_admin_qaqc = (
        session.get('username') == 'admin-qaqc'
        or session.get('id') == 2
    )

    # ambil semua filter yang dikirim lewat url (bisa dari form atau link)
    # ini memungkinkan user ubah rentang tanggal, cari nama, filter divisi, dll
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    keterangan = request.args.get('keterangan')
    search = request.args.get('search_name')
    pembagian1_nama = request.args.get('pembagian1_nama')
    # checkbox show_pm untuk tampilkan pegawai PM (id 180) meski tidak ada data
    show_pm = request.args.get('show_pm', 'off') == 'on'
    
    # kalau user tidak pilih tanggal, otomatis pakai hari ini
    if not start_date:
        start_date = date.today().strftime('%Y-%m-%d')
    if not end_date:
        end_date = date.today().strftime('%Y-%m-%d')
    
    try:
        # buka koneksi ke database
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # ambil semua daftar shift yang ada
        # ini untuk dropdown di modal edit jadwal shift
        cursor.execute("SELECT jk_id, jk_name, jk_bcin, jk_ecout FROM jam_kerja ORDER BY jk_name")
        jam_kerja_list = cursor.fetchall()
        
        # ambil aturan jam kerja khusus (misal ada perubahan jam masuk/pulang di tanggal tertentu)
        # hanya ambil yang ada di rentang tanggal filter
        cursor.execute("""
            SELECT tanggal, jk_id, jam_masuk_khusus, jam_pulang_khusus 
            FROM jam_kerja_khusus 
            WHERE tanggal BETWEEN %s AND %s
        """, (start_date, end_date))
        khusus_rows = cursor.fetchall()
        
        # simpan aturan khusus dalam dictionary supaya nanti cepat dicari
        # key-nya kombinasi tanggal + jk_id
        khusus_map = {}
        for row in khusus_rows:
            key = (row['tanggal'], row['jk_id'])
            khusus_map[key] = row
        
        # query utama: ambil semua data absensi + info pegawai + shift + divisi
        # hanya ambil pegawai aktif (status != 0) dan bukan id 0
        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, 
                   sr.scan_in, sr.scan_out, jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name,
                   pb1.pembagian1_nama, sr.notes
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE sr.tgl_shift BETWEEN %s AND %s 
            AND p.pegawai_status != 0 
            AND p.pegawai_id != 0
        """
        params = [start_date, end_date]

        # kalau user admin qaqc, tambah filter supaya hanya lihat divisi QAQC dan TA
        if is_admin_qaqc:
            query += " AND pb1.pembagian1_nama IN (%s, %s)"
            params.extend(['QAQC', 'TA'])

        # kalau ada kata kunci pencarian, cari di nama atau nip
        if search:
            like = f"%{search}%"
            query += " AND (p.pegawai_nama LIKE %s OR p.pegawai_nip LIKE %s)"
            params += [like, like]
        
        # filter divisi, ada opsi grup (HDTI-HDBS, QAQC-TA) atau divisi tunggal
        if pembagian1_nama:
            if pembagian1_nama == "HDTI-HDBS":
                query += " AND pb1.pembagian1_nama IN (%s, %s)"
                params.extend(["HDTI", "HDBS"])
            elif pembagian1_nama == "QAQC-TA":
                query += " AND pb1.pembagian1_nama IN (%s, %s)"
                params.extend(["QAQC", "TA"])
            else:
                query += " AND pb1.pembagian1_nama = %s"
                params.append(pembagian1_nama)
        
        # jalankan query dan ambil semua data
        cursor.execute(query, params)
        records = cursor.fetchall()
        
        # kalau user pilih show_pm, tambah data dummy untuk PM (id 180)
        # supaya PM tetap muncul meski tidak ada scan di tanggal tertentu
        if show_pm:
            # catat tanggal yang sudah ada untuk PM
            pm_dates = {r['tgl_shift'] for r in records if r['pegawai_id'] == 180}
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            
            # ambil info dasar PM
            cursor.execute("""
                SELECT p.pegawai_id, p.pegawai_nip, p.pegawai_nama, pb1.pembagian1_nama
                FROM pegawai p
                LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
                WHERE p.pegawai_id = 180
            """)
            pm_data = cursor.fetchone()
            
            if pm_data:
                cur = start_dt
                while cur <= end_dt:
                    if cur not in pm_dates:
                        # tambah record kosong untuk tanggal yang hilang
                        records.append({
                            'pegawai_id': 180,
                            'pegawai_nip': pm_data['pegawai_nip'],
                            'pegawai_nama': pm_data['pegawai_nama'],
                            'tgl_shift': cur,
                            'scan_in': None,
                            'scan_out': None,
                            'jk_id': None,
                            'jk_bcin': None,
                            'jk_ecout': None,
                            'jk_name': None,
                            'pembagian1_nama': pm_data['pembagian1_nama'],
                            'notes': None
                        })
                    cur += timedelta(days=1)
        
        # kalau show_pm dimatikan, hapus semua data PM dari list
        if not show_pm:
            records = [r for r in records if r['pegawai_id'] != 180]
        
        # fungsi kecil untuk ubah time atau timedelta jadi datetime lengkap
        # dipakai nanti untuk bandingkan jam dengan benar
        def time_to_datetime(base_dt, time_value):
            if time_value is None:
                return None
            
            if isinstance(time_value, time):
                target_time = time_value
            elif isinstance(time_value, timedelta):
                target_time = (datetime.min + time_value).time()
            else:
                return base_dt
                
            return datetime.combine(base_dt.date(), target_time)
        
        # sekarang proses tiap record satu per satu
        # tujuannya: tentukan keterangan (H, TL, TP, dll), format waktu untuk tampilan
        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            tgl_shift = record['tgl_shift']
            
            # siapkan waktu untuk tampilan (selalu pakai jam kerja standar)
            jk_bcin_display = None
            jk_ecout_display = None
            
            # siapkan waktu untuk hitung status (bisa diubah kalau ada jam khusus)
            jk_bcin_eval = None
            jk_ecout_eval = None
            
            if jk_bcin and jk_ecout:
                # ubah durasi timedelta atau time jadi objek time
                bc_time = (datetime.min + jk_bcin).time() if isinstance(jk_bcin, timedelta) else jk_bcin
                ec_time = (datetime.min + jk_ecout).time() if isinstance(jk_ecout, timedelta) else jk_ecout
                
                # pilih tanggal dasar untuk display (shift malam pakai tanggal scan_in kalau ada)
                if jk_id in [5, 6, 7] and scan_in:
                    base_date = scan_in.date()
                else:
                    base_date = tgl_shift if isinstance(tgl_shift, date) else date.today()
                
                jk_bcin_display = datetime.combine(base_date, bc_time)
                jk_ecout_display = datetime.combine(base_date, ec_time)
                
                # mulai evaluasi status dengan jam standar
                jk_bcin_eval = jk_bcin_display
                jk_ecout_eval = jk_ecout_display
            
            # cek apakah hari ini sabtu/minggu
            is_weekend = isinstance(tgl_shift, date) and (tgl_shift.weekday() in [5, 6])
            

            # kalau shift ini punya kode khusus, langsung kasih keterangan tanpa cek scan
            if jk_id == 10:
                record['keterangan'] = "OT" if (scan_in and scan_out) else "Libur"
            elif jk_id == 9:
                record['keterangan'] = "D"
            elif jk_id == 8:
                record['keterangan'] = "CT"
            elif jk_id == 11:
                record['keterangan'] = "S"
            elif jk_id == 29:
                record['keterangan'] = "LB"
            elif jk_id == 30:
                record['keterangan'] = "LDL"
            elif jk_id == 31:
                record['keterangan'] = "LD"
            else:
                # cek apakah ada jam khusus di tanggal ini untuk shift ini
                khusus = khusus_map.get((tgl_shift, jk_id))
                if khusus:
                    # kalau ada, gunakan jam khusus untuk hitung status
                    if khusus.get('jam_masuk_khusus') is not None and jk_bcin_eval:
                        khusus_masuk = time_to_datetime(jk_bcin_eval, khusus['jam_masuk_khusus'])
                        if khusus_masuk:
                            jk_bcin_eval = khusus_masuk
                    
                    if khusus.get('jam_pulang_khusus') is not None and jk_ecout_eval:
                        khusus_pulang = time_to_datetime(jk_ecout_eval, khusus['jam_pulang_khusus'])
                        if khusus_pulang:
                            jk_ecout_eval = khusus_pulang
                
                # sekarang hitung status berdasarkan scan dan jam evaluasi
                if scan_in and not scan_out:
                    # masuk ada, pulang tidak
                    if jk_bcin_eval:
                        bc_time_limit = (jk_bcin_eval + timedelta(minutes=1)).time()
                        if scan_in.time() >= bc_time_limit:
                            record['keterangan'] = "TL"
                        else:
                            record['keterangan'] = "TP"
                    else:
                        record['keterangan'] = "TP"
                
                elif not scan_in and scan_out:
                    record['keterangan'] = "TL"
                
                elif scan_in and scan_out:
                    # lengkap, cek apakah tepat waktu atau tidak
                    if jk_bcin_eval and jk_ecout_eval:
                        scan_in_time = scan_in.time()
                        scan_out_time = scan_out.time()
                        
                        bc_time_limit = (jk_bcin_eval + timedelta(minutes=1)).time()
                        
                        # kalau bulan puasa (19 feb - 20 mar 2026), pulang lebih awal 30 menit
                        if isinstance(tgl_shift, date) and date(2026, 2, 19) <= tgl_shift <= date(2026, 3, 20):
                            ec_time_limit = (jk_ecout_eval - timedelta(minutes=30)).time()
                        else:
                            ec_time_limit = jk_ecout_eval.time()
                        
                        # cek pulang valid berdasarkan jam
                        is_out_valid = scan_out_time >= ec_time_limit
                        
                        # cek tambahan pakai waktu lengkap (penting buat shift malam)
                        if scan_out >= jk_ecout_eval:
                            is_out_valid = True
                        
                        # tentukan status akhir
                        if scan_in_time >= bc_time_limit and is_out_valid:
                            record['keterangan'] = "TL"
                        elif scan_in_time <= bc_time_limit and is_out_valid:
                            record['keterangan'] = "H"
                        else:
                            record['keterangan'] = "TP"
                    else:
                        record['keterangan'] = "A"
                else:
                    record['keterangan'] = "A"
            
            # format waktu untuk tampilan di halaman (selalu pakai jam standar)
            if record['keterangan'] in ["LB", "CT", "LDL", "LD", "S", "Libur"]:
                # kalau libur/cuti/sakit, kosongkan semua kolom waktu
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
            else:
                if scan_in:
                    record['scan_in'] = scan_in.strftime('%H:%M')
                if scan_out:
                    record['scan_out'] = scan_out.strftime('%H:%M')
                
                if jk_bcin_display:
                    record['jk_bcin'] = jk_bcin_display.strftime('%H:%M')
                if jk_ecout_display:
                    # tampilan puasa: kurangi 30 menit hanya di layar
                    if isinstance(tgl_shift, date) and date(2026, 2, 19) <= tgl_shift <= date(2026, 3, 20):
                        adjusted_display = (jk_ecout_display - timedelta(minutes=30))
                        record['jk_ecout'] = adjusted_display.strftime('%H:%M')
                    else:
                        record['jk_ecout'] = jk_ecout_display.strftime('%H:%M')
        
        # kalau user pilih filter keterangan tertentu, hapus record yang tidak cocok
        if keterangan:
            records = [r for r in records if r['keterangan'] == keterangan]
        
        # hitung statistik supaya bisa tampilkan ringkasan (berapa orang H, TL, dll)
        stats = {
            'H': 0, 'TP': 0, 'TL': 0, 'D': 0, 'CT': 0, 
            'S': 0, 'A': 0, 'Libur': 0, 'LDL': 0, 'OT': 0, 'LB': 0
        }
        
        for r in records:
            if r['keterangan'] in stats:
                stats[r['keterangan']] += 1
        
        # urutkan data supaya tampilan lebih rapi
        # prioritas: status baik dulu → nama alfabet → divisi HDTI duluan
        SORT_ORDER = {
            "H": 1, "TP": 2, "TL": 3, "LB": 4, "CT": 5, 
            "D": 6, "A": 7
        }
        
        records.sort(key=lambda x: (
            SORT_ORDER.get(x['keterangan'], float('inf')),
            x['pegawai_nama'].lower(),
            0 if x['pembagian1_nama'] == 'HDTI' else 1,
            x['pembagian1_nama'].lower() if x['pembagian1_nama'] else ''
        ))
        
        # kirim semua data ke template untuk ditampilkan
        return render_template(
            'index.html',
            records=records,
            stats=stats,
            start_date=start_date,
            end_date=end_date,
            jam_kerja_list=jam_kerja_list
        )
        
    except Exception as e:
        # kalau ada error (db mati, query salah, dll), tampilkan halaman kosong
        print(f"Error di dashboard utama: {e}")
        return render_template('index.html', records=[], stats={}, jam_kerja_list=[])
        
    finally:
        # pastikan koneksi db ditutup biar tidak memory leak
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()



# Route untuk buka modal (GET) & simpan data (POST)
@app.route('/jam-kerja-khusus', methods=['GET', 'POST'])
def jam_kerja_khusus():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    if request.method == 'POST':
        try:
            tanggal = request.form.get('tanggal')
            jk_id = request.form.get('jk_id')
            jam_masuk_khusus = request.form.get('jam_masuk_khusus') or None
            jam_pulang_khusus = request.form.get('jam_pulang_khusus') or None

            cursor.execute("""
                INSERT INTO jam_kerja_khusus (tanggal, jk_id, jam_masuk_khusus, jam_pulang_khusus)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    jam_masuk_khusus = VALUES(jam_masuk_khusus),
                    jam_pulang_khusus = VALUES(jam_pulang_khusus)
            """, (tanggal, jk_id, jam_masuk_khusus, jam_pulang_khusus))

            connection.commit()
        except Exception as e:
            pass
        finally:
            cursor.close()
            connection.close()

        return redirect(url_for('index'))

    else:  # GET → ambil daftar shift untuk ditampilkan di modal
        cursor.execute("SELECT jk_id, jk_name FROM jam_kerja ORDER BY jk_name")
        jam_kerja_list = cursor.fetchall()
        cursor.close()
        connection.close()
        return render_template('modal_jam_kerja_khusus.html', jam_kerja_list=jam_kerja_list)



@app.route('/export_excel_month', methods=['GET'])
def export_excel_month():
    month = request.args.get('month')
    division = request.args.get('division')

    if not month or not division:
        return redirect(url_for('index'))

    if division == "HDBS":
        start_date = f"{month}-01"
        end_date = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    elif division == "HDTI":
        start_date = f"{month}-06"
        end_date = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=32)).replace(day=5)
    else:
        return redirect(url_for('index'))

    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    end_date = end_date.strftime("%Y-%m-%d")

    today = datetime.now().strftime('%d-%m-%Y')
    reports_dir = "reports"
    os.makedirs(reports_dir, exist_ok=True)

    output_file = os.path.join(reports_dir, f'report_month_{month}_division_{division}.xlsx')



    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, sr.scan_in, sr.scan_out, 
                jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            WHERE sr.tgl_shift BETWEEN %s AND %s
            AND p.pegawai_status != 0
            AND p.pembagian1_id = (
                SELECT pembagian1_id FROM pembagian1 WHERE pembagian1_nama = %s
            )
        """

        params = [start_date, end_date, division]

        cursor.execute(query, params)
        records = cursor.fetchall()

        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            jk_name = record.get('jk_name')
            tgl_shift = record['tgl_shift']
            

            jk_bcin_time = (datetime.min + jk_bcin).time() if jk_bcin else None
            jk_ecout_time = (datetime.min + jk_ecout).time() if jk_ecout else None

            if jk_id in [5, 6, 7]:
                if scan_in:
                    jk_bcin_time = datetime.combine(scan_in.date(), jk_bcin_time)
                if scan_in and jk_ecout:
                    jk_ecout_time = datetime.combine(scan_in.date() , jk_ecout_time)
            else:
                if jk_bcin and jk_ecout:
                    jk_bcin_time = datetime.min + jk_bcin
                    jk_ecout_time = datetime.min + jk_ecout

           
            is_weekend = tgl_shift.weekday() in [5, 6]

            if record['pegawai_id'] == 180:
                is_weekend = tgl_shift.weekday() in [5, 6]  
                if is_weekend:
                    record['keterangan'] = ""
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
                    record['jk_name'] = "-"
                else:
                    record['keterangan'] = "H"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
                    record['jk_name'] = "-"
            elif jk_id == 10:
                record['keterangan'] = ""
            elif jk_id == 9:
                record['keterangan'] = "D"
            elif jk_id == 8:
                record['keterangan'] = "CT"
            elif jk_id == 11:
                record['keterangan'] = "S"
            elif jk_id == 29:
                record['keterangan'] = "LB"
            elif jk_id == 30:
                record['keterangan'] = "LDL"
            elif jk_id == 31:
                record['keterangan'] = "LD"
            else:
                if scan_in and scan_out:
                    if jk_bcin_time and jk_ecout_time:
                        scan_in_time = scan_in.time()
                        scan_out_time = scan_out.time()
                        bc_time_limit = (jk_bcin_time + timedelta(minutes=1)).time()

                        
                        if tgl_shift >= date(2025, 3, 1) and tgl_shift <= date(2025, 3, 31):
                            ec_time_limit = (jk_ecout_time - timedelta(minutes=30)).time()
                        else:
                            ec_time_limit = jk_ecout_time.time()

                        if scan_in_time >= bc_time_limit and scan_out_time >= ec_time_limit:
                            record['keterangan'] = "TL"
                        elif scan_in_time <= bc_time_limit and scan_out_time >= ec_time_limit:
                            record['keterangan'] = "H"
                        elif scan_in_time >= bc_time_limit and scan_out_time < ec_time_limit:
                            record['keterangan'] = "TP"
                        elif scan_in_time <= bc_time_limit and scan_out_time < ec_time_limit:
                            record['keterangan'] = "TP"
                        else:
                            record['keterangan'] = "A"
                    else:
                        record['keterangan'] = "A"
                else:
                    record['keterangan'] = "A"

            if record['keterangan'] in ["LB", "CT", "D", "LDL", "LD", "S", "Libur"]:
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
            else:
                if jk_id in [5, 6, 7]:
                
                    if scan_in:
                        record['scan_in'] = scan_in.strftime('%m/%d/%Y %H:%M')
                    if scan_out:
                        record['scan_out'] = scan_out.strftime('%m/%d/%Y %H:%M')
                    if jk_bcin_time:
                        record['jk_bcin'] = jk_bcin_time.strftime('%m/%d/%Y %H:%M')
                    if jk_ecout_time:
                        record['jk_ecout'] = jk_ecout_time.strftime('%m/%d/%Y %H:%M')
                else:
                    if scan_in:
                        record['scan_in'] = scan_in.strftime('%H:%M')
                    if scan_out:
                        record['scan_out'] = scan_out.strftime('%H:%M:')
                    if jk_bcin is not None:
                        record['jk_bcin'] = jk_bcin_time.strftime('%H:%M') if jk_bcin_time else None
                    if jk_ecout is not None:
                        record['jk_ecout'] = jk_ecout_time.strftime('%H:%M') if jk_ecout_time else None

        df = pd.DataFrame(records)
        df['tgl_shift'] = pd.to_datetime(df['tgl_shift']).dt.strftime('%Y-%m-%d')

        unique_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        unique_names = df['pegawai_nama'].unique().tolist()

        data = []
        for name in unique_names:
            row = {'NAMA PEGAWAI': name}
            for date in unique_dates:
                keterangan = df[(df['pegawai_nama'] == name) & (df['tgl_shift'] == date)]['keterangan']
                if keterangan.empty:
                    
                    date_obj = datetime.strptime(date, '%Y-%m-%d')
                    if date_obj.weekday() in [5, 6]: 
                        row[date] = ''
                    else:
                        row[date] = 'A'
                else:
                    row[date] = keterangan.values[0]
            data.append(row)

        result_df = pd.DataFrame(data)

        
        result_df = result_df.sort_values('NAMA PEGAWAI')

        locale.setlocale(locale.LC_TIME, 'id_ID.UTF-8')

        result_df.columns = ['NAMA PEGAWAI'] + [
            f"{datetime.strptime(date, '%Y-%m-%d').day} {datetime.strptime(date, '%Y-%m-%d').strftime('%A')}" 
            for date in unique_dates
        ]
        result_df.insert(0, 'NO', range(1, len(result_df) + 1))

        with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
            result_df.to_excel(writer, index=False, sheet_name='Sheet1', startrow=1)

            workbook = writer.book
            worksheet = writer.sheets['Sheet1']

            worksheet.set_column('A:A', 4)
            worksheet.set_column('B:B', 30)
            for i in range(2, len(result_df.columns)):
                worksheet.set_column(i, i, 9)

            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'middle',
                'align': 'center',
                'border': 1
            })

            worksheet.merge_range('A1:A2', 'NO', header_format)  
            worksheet.merge_range('B1:B2', 'NAMA PEGAWAI', header_format)
            for col_num in range(2, len(result_df.columns)):
                worksheet.write(0, col_num, result_df.columns[col_num].split()[0], header_format)
                worksheet.write(1, col_num, result_df.columns[col_num].split()[1], header_format)

            for col_num, value in enumerate(result_df.columns.values):
                if col_num < 2:
                    worksheet.write(1, col_num, value, header_format)

            format_border = workbook.add_format({
                'bold': False,
                'text_wrap': True,
                'valign': 'middle',
                'align': 'center',
                'border': 1
            })

           
            if division == "HDBS":
                worksheet.conditional_format('A1:AG55', {'type': 'no_blanks', 'format': format_border})
                worksheet.conditional_format('A1:AG55', {'type': 'blanks', 'format': format_border})
            elif division == "HDTI":
                worksheet.conditional_format('A1:AG135', {'type': 'no_blanks', 'format': format_border})
                worksheet.conditional_format('A1:AG135', {'type': 'blanks', 'format': format_border})

        return send_file(output_file, as_attachment=True)
    
    except Exception as e:
        return redirect(url_for('index'))
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/export_excel_month_reg', methods=['GET'])
def export_excel_month_reg():

    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    month = request.args.get('month_reg')
    division = request.args.get('division_reg')

    if not month or not division:
        return redirect(url_for('index'))

    start_date = f"{month}-01"
    end_date = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=32)).replace(day=1) - timedelta(days=1)

    start_date = datetime.strptime(start_date, "%Y-%m-%d")
    end_date = end_date.strftime("%Y-%m-%d")

    today = datetime.now().strftime('%d-%m-%Y')
    reports_dir = "reports"
    os.makedirs(reports_dir, exist_ok=True)

    output_file = os.path.join(reports_dir, f'report_month_{month}_division_{division}.xlsx')

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, p2.pembagian2_nama AS jabatan, 
                sr.tgl_shift, sr.scan_in, sr.scan_out, jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            LEFT JOIN pembagian2 p2 ON p.pembagian2_id = p2.pembagian2_id
            WHERE sr.tgl_shift BETWEEN %s AND %s
            AND p.pegawai_status != 0
            AND p.pembagian1_id = (
                SELECT pembagian1_id FROM pembagian1 WHERE pembagian1_nama = %s
            )
        """

        params = [start_date, end_date, division]

        cursor.execute(query, params)
        records = cursor.fetchall()

        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            jk_name = record.get('jk_name')
            tgl_shift = record['tgl_shift']

            jk_bcin_time = (datetime.min + jk_bcin).time() if jk_bcin else None
            jk_ecout_time = (datetime.min + jk_ecout).time() if jk_ecout else None

            if jk_id in [5, 6, 7]:
                if scan_in:
                    jk_bcin_time = datetime.combine(scan_in.date(), jk_bcin_time)
                if scan_in and jk_ecout:
                    jk_ecout_time = datetime.combine(scan_in.date(), jk_ecout_time)
            else:
                if jk_bcin and jk_ecout:
                    jk_bcin_time = datetime.min + jk_bcin
                    jk_ecout_time = datetime.min + jk_ecout

            is_weekend = tgl_shift.weekday() in [5, 6]

            if record['pegawai_id'] == 180:
                is_weekend = tgl_shift.weekday() in [5, 6]  
                if is_weekend:
                    record['keterangan'] = ""
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
                    record['jk_name'] = "-"
                else:
                    record['keterangan'] = "H"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
                    record['jk_name'] = "-"
            elif jk_id == 10:
                record['keterangan'] = ""
            elif jk_id == 9:
                record['keterangan'] = "D"
            elif jk_id == 8:
                record['keterangan'] = "CT"
            elif jk_id == 11:
                record['keterangan'] = "S"
            elif jk_id == 29:
                record['keterangan'] = "LB"
            elif jk_id == 30:
                record['keterangan'] = "LDL"
            elif jk_id == 31:
                record['keterangan'] = "LD"
            else:
                if scan_in and scan_out:
                    if jk_bcin_time and jk_ecout_time:
                        scan_in_time = scan_in.time()
                        scan_out_time = scan_out.time()
                        bc_time_limit = (jk_bcin_time + timedelta(minutes=1)).time()

                       
                        if tgl_shift >= date(2025, 3, 1) and tgl_shift <= date(2025, 3, 31):
                            ec_time_limit = (jk_ecout_time - timedelta(minutes=30)).time() 
                        else:
                            ec_time_limit = jk_ecout_time.time()

                        if scan_in_time >= bc_time_limit and scan_out_time >= ec_time_limit:
                            record['keterangan'] = "TL"
                        elif scan_in_time <= bc_time_limit and scan_out_time >= ec_time_limit:
                            record['keterangan'] = "H"
                        elif scan_in_time >= bc_time_limit and scan_out_time < ec_time_limit:
                            record['keterangan'] = "TP"
                        elif scan_in_time <= bc_time_limit and scan_out_time < ec_time_limit:
                            record['keterangan'] = "TP"
                        else:
                            record['keterangan'] = "A"
                    else:
                        record['keterangan'] = "A"
                else:
                    record['keterangan'] = "A"

            if record['keterangan'] in ["LB", "CT", "D", "LDL", "LD", "S"]:
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
            else:
                if jk_id in [5, 6, 7]:
                
                    if scan_in:
                        record['scan_in'] = scan_in.strftime('%m/%d/%Y %H:%M')
                    if scan_out:
                        record['scan_out'] = scan_out.strftime('%m/%d/%Y %H:%M')
                    if jk_bcin_time:
                        record['jk_bcin'] = jk_bcin_time.strftime('%m/%d/%Y %H:%M')
                    if jk_ecout_time:
                        record['jk_ecout'] = jk_ecout_time.strftime('%m/%d/%Y %H:%M')
                else:
                    if scan_in:
                        record['scan_in'] = scan_in.strftime('%H:%M')
                    if scan_out:
                        record['scan_out'] = scan_out.strftime('%H:%M:')
                    if jk_bcin is not None:
                        record['jk_bcin'] = jk_bcin_time.strftime('%H:%M') if jk_bcin_time else None
                    if jk_ecout is not None:
                        record['jk_ecout'] = jk_ecout_time.strftime('%H:%M') if jk_ecout_time else None

        df = pd.DataFrame(records)
        df['tgl_shift'] = pd.to_datetime(df['tgl_shift']).dt.strftime('%Y-%m-%d')

        unique_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        unique_names = df['pegawai_nama'].unique().tolist()

        df = pd.DataFrame(records)
        df['tgl_shift'] = pd.to_datetime(df['tgl_shift']).dt.strftime('%Y-%m-%d')

        unique_dates = pd.date_range(start=start_date, end=end_date).strftime('%Y-%m-%d').tolist()
        unique_names = df['pegawai_nama'].unique().tolist()

        data = []
        for name in unique_names:
            row = {
                'NAMA PEGAWAI': name,
                'JABATAN': df[df['pegawai_nama'] == name]['jabatan'].iloc[0]
            }
            for date in unique_dates:
                keterangan = df[(df['pegawai_nama'] == name) & (df['tgl_shift'] == date)]['keterangan']
                if keterangan.empty:
                    date_obj = datetime.strptime(date, '%Y-%m-%d')
                    if date_obj.weekday() in [5, 6]: 
                        row[date] = ''
                    else:
                        row[date] = 'A'
                else:
                    row[date] = keterangan.values[0]
            data.append(row)

        result_df = pd.DataFrame(data)

        nip_dict = {name: df[df['pegawai_nama'] == name]['pegawai_nip'].iloc[0] for name in unique_names}
        result_df.insert(0, 'NIP PEGAWAI', result_df['NAMA PEGAWAI'].map(nip_dict))
        
        result_df = result_df.sort_values('NAMA PEGAWAI')

        locale.setlocale(locale.LC_TIME, 'id_ID.UTF-8')

        result_df.columns = ['NIP PEGAWAI', 'NAMA PEGAWAI', 'JABATAN'] + [
            f"{datetime.strptime(date, '%Y-%m-%d').day} {datetime.strptime(date, '%Y-%m-%d').strftime('%A')}" 
            for date in unique_dates
        ]
        result_df.insert(0, 'NO', range(1, len(result_df) + 1))

        with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
            result_df.to_excel(writer, index=False, sheet_name='Sheet1', startrow=1)

            workbook = writer.book
            worksheet = writer.sheets['Sheet1']

            worksheet.set_column('A:A', 4)
            worksheet.set_column('B:B', 15)
            worksheet.set_column('C:C', 30)
            worksheet.set_column('D:D', 20)
            for i in range(4, len(result_df.columns)):
                worksheet.set_column(i, i, 9)

            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'middle',
                'align': 'center',
                'border': 1
            })

            worksheet.merge_range('A1:A2', 'NO', header_format)  
            worksheet.merge_range('B1:B2', 'NIP PEGAWAI', header_format)  
            worksheet.merge_range('C1:C2', 'NAMA PEGAWAI', header_format)
            worksheet.merge_range('D1:D2', 'JABATAN', header_format)

            for col_num in range(4, len(result_df.columns)):
                worksheet.write(0, col_num, result_df.columns[col_num].split()[0], header_format)
                worksheet.write(1, col_num, result_df.columns[col_num].split()[1], header_format)

            format_border = workbook.add_format({
                'bold': False,
                'text_wrap': True,
                'valign': 'middle',
                'align': 'center',
                'border': 1
            })

            worksheet.conditional_format('A1:AG165', {'type': 'no_blanks', 'format': format_border})
            worksheet.conditional_format('A1:AG165', {'type': 'blanks', 'format': format_border})

        return send_file(output_file, as_attachment=True)

    except Exception as e:
        return redirect(url_for('index'))

    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


def is_admin_qaqc():
    return (
        session.get('role') == 'admin' and
        (session.get('username') == 'admin-qaqc' or session.get('id') == 2)
    )

@app.route('/upload_excel', methods=['GET', 'POST'])
def upload_excel():
    # route ini untuk halaman upload jadwal shift via file excel
    # admin bisa pilih tanggal mulai dan divisi, lalu upload file xlsx
    # setelah upload, file disimpan sementara dan ditampilkan preview di halaman view_uploaded_excel

    # cek dulu user sudah login dan role admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

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
            filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filename)
            # simpan nama file dan divisi ke session untuk preview
            session['uploaded_file'] = file.filename
            session['division'] = division
            # redirect ke halaman preview data excel yang baru diupload
            return redirect(url_for('view_uploaded_excel', filename=file.filename))
        else:
            # kalau bukan xlsx, balik ke halaman upload
            return redirect(request.url)
    
    # kalau method GET, tampilkan halaman form upload
    return render_template('upload_excel.html')



@app.route('/view_uploaded_excel/<filename>')
def view_uploaded_excel(filename):
    # route ini untuk preview isi file excel jadwal shift yang baru diupload
    # data ditampilkan dalam tabel html sebelum disimpan ke database

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    # ambil start_date dan division dari session (disimpan saat upload)
    start_date_str = session.get('start_date')
    start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
    division = session.get('division')
    
    # path file yang diupload
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # panggil fungsi read_and_process_excel untuk baca dan proses excel
    # map_to_names=True supaya kode shift diganti jadi nama shift (misal LB jadi Libur)
    combined_df = read_and_process_excel(file_path, start_date, division, map_to_names=True)
    
    # ubah dataframe jadi list dict untuk dikirim ke template
    records = combined_df.to_dict('records')
    headers = combined_df.columns
    
    # tampilkan halaman preview tabel
    return render_template('view_data.html', headers=headers, records=records)


           
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




@app.route('/update-shifts', methods=['POST'])
def update_shifts():
    # route ini dipanggil setelah admin melihat preview excel jadwal shift
    # tugasnya: simpan data jadwal dari excel ke tabel shift_result_new
    # hanya update kalau jk_id sebelumnya kosong atau 0 (tidak overwrite data existing)

    # pastikan user admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    # ambil divisi dari session (disimpan saat upload)
    division = session.get('division')

    # admin qaqc hanya boleh proses QAQC dan TA
    # kalau divisi lain, balik ke halaman upload
    if is_admin_qaqc() and division not in ['QAQC', 'TA', 'QAQC-TA']:
        return redirect(url_for('upload_excel'))

    connection = None
    cursor = None
    
    try:
        # ambil nama file dan start_date dari session
        file_name = session.get('uploaded_file')
        start_date_str = session.get('start_date')
        
        # kalau tidak ada file atau tanggal, balik ke upload
        if not file_name:
            return redirect(url_for('upload_excel'))
        
        if not start_date_str:
            return redirect(url_for('upload_excel'))
        
        # ubah string tanggal jadi objek date
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')

        # path file excel yang sudah diupload
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], file_name)
        
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
    
    except Error as e:
        # kalau ada error database, skip saja (tidak crash)
        pass
    
    finally:
        # tutup cursor dan koneksi supaya tidak memory leak
        if cursor is not None:
            cursor.close()
        if connection is not None and connection.is_connected():
            connection.close()
    
    # setelah selesai simpan, balik ke halaman upload untuk upload lagi kalau perlu
    return redirect(url_for('upload_excel'))
            

@app.route('/upload', methods=['GET', 'POST'])
def upload_shift():
    # route ini untuk upload file csv dari mesin absensi lama (mesin bawah)
    # admin pilih divisi, upload csv, lalu preview hasil pengolahan di view_csv

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

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
            filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filename)
            # simpan divisi ke session untuk proses selanjutnya
            session['division_old_machine'] = division
            # redirect ke halaman preview hasil csv
            return redirect(url_for('view_csv', filename=file.filename))
    # kalau GET, tampilkan form upload
    return render_template('upload_shift.html')

def load_nip_mapping():
    # fungsi kecil untuk baca mapping nip lama ke nip baru dari json
    # dipakai di proses csv mesin lama supaya nip bisa cocok dengan data pegawai
    with open('mapping_nip_old_absen_to_real_nip.json', 'r') as file:
        return json.load(file)

@app.route('/view_csv/<filename>')
def view_csv(filename):
    # route ini untuk preview hasil pengolahan file csv dari mesin absensi lama (mesin bawah)
    # tugasnya: baca csv, kelompokkan scan masuk/keluar per nip dan tanggal
    # mapping nip lama ke nip baru, filter berdasarkan divisi, lalu tampilkan dalam tabel html
    # setelah itu simpan hasil scan ke shift_result_new dan isi record kosong untuk semua pegawai

    # pastikan user sudah login sebagai admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    # ambil path file csv yang diupload
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
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
                # kalau sudah ada, update scan in/out (pakai COALESCE supaya tidak overwrite kalau sudah ada data)
                cursor.execute('''
                    UPDATE shift_result_new 
                    SET scan_in = COALESCE(%s, scan_in), scan_out = COALESCE(%s, scan_out) 
                    WHERE pegawai_id = %s AND tgl_shift = %s
                ''', (db_scan_in, db_scan_out, pegawai_id, tanggal_shift))
            else:
                # kalau belum ada, insert record baru
                cursor.execute('''
                    INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out)
                    VALUES (%s, %s, %s, %s)
                ''', (pegawai_id, tanggal_shift, db_scan_in, db_scan_out))

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

    # tampilkan halaman preview dengan tabel hasil
    return render_template('view_csv.html', tables=table_html, filename=filename)


@app.route('/dcap', methods=['GET', 'POST'])
def upload_dcap():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)
        if file:
            filename = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filename)
            return redirect(url_for('view_dcap', filename=file.filename))
            
    return render_template('upload_dcap.html')

@app.route('/view_dcap/<filename>')
def view_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        # header di baris ke 2 (index 1)
        df = pd.read_excel(filepath, header=1)
        
        # bersihkan data jika barisnya full kosong
        df = df.dropna(how='all')
        
        # hapus satu baris paling bawah (catatan tanggal pembuatan file)
        if not df.empty:
            df = df.iloc[:-1]
            
        # Hapus baris jika Waktu Masuk dan Waktu Keluar keduanya kosong
        if 'Waktu Masuk' in df.columns and 'Waktu Keluar' in df.columns:
            df = df.dropna(subset=['Waktu Masuk', 'Waktu Keluar'], how='all')
            
        # Ambil NIK dari kolom ke-6 (index 5) jika ada
        if len(df.columns) > 5:
            df.rename(columns={df.columns[5]: 'NIK'}, inplace=True)
            
        if 'NIK' in df.columns:
            df['NIK'] = pd.to_numeric(df['NIK'], errors='coerce').astype('Int64')
        
        # format Pin jadi integer
        if 'Pin' in df.columns:
            df['Pin'] = pd.to_numeric(df['Pin'], errors='coerce').astype('Int64')
            
        # format Tanggal jadi dd/mm/yyyy
        if 'Tanggal' in df.columns:
            df['Tanggal'] = pd.to_datetime(df['Tanggal'], errors='coerce').dt.strftime('%d/%m/%Y')
        
        # konversi dataframe ke html (na_rep='' akan mengubah NaN menjadi kosong)
        table_html = df.to_html(classes='dataframe', index=False, na_rep='')
        
        return render_template('view_dcap.html', tables=table_html, filename=filename)
    except Exception as e:
        return f"Gagal membaca file: {str(e)}"

@app.route('/convert_dcap/<filename>')
def convert_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        # header di baris ke 2 (index 1)
        df = pd.read_excel(filepath, header=1)
        
        # bersihkan data jika barisnya full kosong
        df = df.dropna(how='all')
        
        # hapus satu baris paling bawah (catatan tanggal)
        if not df.empty:
            df = df.iloc[:-1]
            
        # Hapus baris jika Waktu Masuk dan Waktu Keluar keduanya kosong
        if 'Waktu Masuk' in df.columns and 'Waktu Keluar' in df.columns:
            df = df.dropna(subset=['Waktu Masuk', 'Waktu Keluar'], how='all')
            
        # Ambil NIK dari kolom ke-6 (index 5) jika ada
        if len(df.columns) > 5:
            df.rename(columns={df.columns[5]: 'NIK'}, inplace=True)
            
        if 'NIK' in df.columns:
            df['NIK'] = pd.to_numeric(df['NIK'], errors='coerce').astype('Int64')
            df['NIP'] = df['NIK'].astype(str).str.strip().str.replace('<NA>', '', regex=False).str[-4:]
            df['NIP'] = pd.to_numeric(df['NIP'], errors='coerce').astype('Int64')
            
            # Terapkan pemetaan NIK LAMA -> NIK BARU pada NIP
            pin_mapping = load_dcap_pin_mapping()
            if pin_mapping:
                df['NIP'] = df['NIP'].apply(lambda x: pin_mapping.get(x, x) if pd.notna(x) else x)
                df['NIP'] = pd.to_numeric(df['NIP'], errors='coerce').astype('Int64')
            
        # ambil kolom yang diperlukan saja (termasuk NIK dan NIP agar muncul di view html)
        kolom_pilihan = ['Pin', 'Nama', 'Tanggal', 'Waktu Masuk', 'Waktu Keluar', 'Lokasi Masuk', 'NIK', 'NIP']
        kolom_yang_ada = [col for col in kolom_pilihan if col in df.columns]
        df = df[kolom_yang_ada]
        
        # format Pin jadi integer
        if 'Pin' in df.columns:
            df['Pin'] = pd.to_numeric(df['Pin'], errors='coerce').astype('Int64')
            
        # format Tanggal jadi dd/mm/yyyy
        if 'Tanggal' in df.columns:
            df['Tanggal'] = pd.to_datetime(df['Tanggal'], errors='coerce').dt.strftime('%d/%m/%Y')
        
        # konversi dataframe ke html
        table_html = df.to_html(classes='dataframe', index=False, na_rep='')
        
        return render_template('convert_dcap.html', tables=table_html, filename=filename)
    except Exception as e:
        return f"Gagal membaca/convert file: {str(e)}"

def load_dcap_pin_mapping():
    try:
        personil_df = pd.read_excel('personil-dcap.xlsx')
        mapping = {}
        for _, row in personil_df.iterrows():
            nik_lama = row.get('NIK LAMA')
            nik_baru = row.get('NIK BARU')
            
            if pd.isna(nik_lama):
                continue
                
            nik_lama_str = str(nik_lama).strip().strip("'").split('.')[0]
            if not nik_lama_str:
                continue
                
            db_nip = nik_lama_str[-4:] if len(nik_lama_str) >= 4 else nik_lama_str
            
            if pd.notna(nik_baru):
                nik_baru_str = str(nik_baru).strip().strip("'").split('.')[0]
                if nik_baru_str:
                    dcap_pin = nik_baru_str[-4:] if len(nik_baru_str) >= 4 else nik_baru_str
                    try:
                        mapping[int(dcap_pin)] = int(db_nip)
                    except ValueError:
                        pass
        return mapping
    except Exception as e:
        print(f"Error loading personil mapping: {e}")
        return {}

@app.route('/mapping-dcap')
def mapping_dcap():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
        
    try:
        personil_df = pd.read_excel('personil-dcap.xlsx')
        
        result_data = []
        for _, row in personil_df.iterrows():
            nama = row.get('NAMA')
            nik_lama = row.get('NIK LAMA')
            nik_baru = row.get('NIK BARU')
            
            if pd.isna(nik_lama):
                continue
                
            nik_lama_str = str(nik_lama).strip().strip("'").split('.')[0]
            if not nik_lama_str:
                continue
                
            db_nip = nik_lama_str[-4:] if len(nik_lama_str) >= 4 else nik_lama_str
            
            dcap_pin = ""
            if pd.notna(nik_baru):
                nik_baru_str = str(nik_baru).strip().strip("'").split('.')[0]
                if nik_baru_str:
                    dcap_pin = nik_baru_str[-4:] if len(nik_baru_str) >= 4 else nik_baru_str
                    
            result_data.append({
                'NAMA': nama,
                'NIK LAMA (Mentah)': nik_lama,
                'NIK BARU (Mentah)': nik_baru,
                'PIN DCAP (Key)': dcap_pin,
                'NIP DB (Value)': db_nip
            })
            
        result_df = pd.DataFrame(result_data)
        table_html = result_df.to_html(classes='table table-bordered table-striped', index=False)
        
        html = f"""
        <html>
        <head>
            <title>Preview Mapping DCAP</title>
            <link rel="stylesheet" href="https://stackpath.bootstrapcdn.com/bootstrap/4.5.2/css/bootstrap.min.css">
        </head>
        <body>
            <div class="container mt-5">
                <h2>Pratinjau Hasil Parsing personil-dcap.xlsx</h2>
                <p>Halaman ini untuk memverifikasi apakah fungsi <code>load_dcap_pin_mapping()</code> sudah mengekstrak 4 digit NIK dengan benar.</p>
                <a href="/dashboard" class="btn btn-secondary mb-3">Kembali ke Dashboard</a>
                <div class="table-responsive">
                    {{{{ table_html | safe }}}}
                </div>
            </div>
        </body>
        </html>
        """
        
        from flask import render_template_string
        return render_template_string(html, table_html=table_html)
    except Exception as e:
        return f"Gagal membaca mapping: {{str(e)}}"

def process_dcap_scan_times(jk_id, tanggal_shift, waktu_masuk_str, waktu_keluar_str):
    scan_in_final = None
    scan_out_final = None
    
    try:
        waktu_masuk_time = pd.to_datetime(waktu_masuk_str, format='%H:%M:%S').time() if waktu_masuk_str and waktu_masuk_str != '00:00:00' else None
    except:
        waktu_masuk_time = None
        
    try:
        waktu_keluar_time = pd.to_datetime(waktu_keluar_str, format='%H:%M:%S').time() if waktu_keluar_str and waktu_keluar_str != '00:00:00' else None
    except:
        waktu_keluar_time = None
        
    next_day = (pd.to_datetime(tanggal_shift) + pd.Timedelta(days=1)).strftime('%Y-%m-%d')
    malam_jk_ids = {7, 15, 18, 22}
    
    if jk_id == 4:
        # scan in: 04:00 - 23:59
        if waktu_masuk_time and pd.to_datetime('04:00:00').time() <= waktu_masuk_time <= pd.to_datetime('23:59:59').time():
            scan_in_final = f"{tanggal_shift} {waktu_masuk_str}"
            
        if waktu_keluar_time:
            # scan out: 14:00 - 23:59 (Hari H)
            if pd.to_datetime('14:00:00').time() <= waktu_keluar_time <= pd.to_datetime('23:59:59').time():
                scan_out_final = f"{tanggal_shift} {waktu_keluar_str}"
            # scan out: 00:00 - 03:59 (Hari H+1)
            elif pd.to_datetime('00:00:00').time() <= waktu_keluar_time <= pd.to_datetime('03:59:59').time():
                scan_out_final = f"{next_day} {waktu_keluar_str}"
                
    elif jk_id in malam_jk_ids:
        # scan in: 11:00 - 23:59
        if waktu_masuk_time and pd.to_datetime('11:00:00').time() <= waktu_masuk_time <= pd.to_datetime('23:59:59').time():
            scan_in_final = f"{tanggal_shift} {waktu_masuk_str}"
            
        # scan out: 00:00 - 12:00 (Hari H+1)
        if waktu_keluar_time and pd.to_datetime('00:00:00').time() <= waktu_keluar_time <= pd.to_datetime('12:00:00').time():
            scan_out_final = f"{next_day} {waktu_keluar_str}"
            
    else:
        # scan in: 03:00 - 13:59
        if waktu_masuk_time and pd.to_datetime('03:00:00').time() <= waktu_masuk_time <= pd.to_datetime('13:59:59').time():
            scan_in_final = f"{tanggal_shift} {waktu_masuk_str}"
            
        # scan out: 14:00 - 23:59
        if waktu_keluar_time and pd.to_datetime('14:00:00').time() <= waktu_keluar_time <= pd.to_datetime('23:59:59').time():
            scan_out_final = f"{tanggal_shift} {waktu_keluar_str}"
            
    return scan_in_final, scan_out_final

@app.route('/apply_rules_dcap/<filename>')
def apply_rules_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        df = pd.read_excel(filepath, header=1)
        df = df.dropna(how='all')
        if not df.empty:
            df = df.iloc[:-1]
            
        # Hapus baris jika Waktu Masuk dan Waktu Keluar keduanya kosong
        if 'Waktu Masuk' in df.columns and 'Waktu Keluar' in df.columns:
            df = df.dropna(subset=['Waktu Masuk', 'Waktu Keluar'], how='all')
            
        # Ambil NIK dari kolom ke-6 (index 5) jika ada
        if len(df.columns) > 5:
            df.rename(columns={df.columns[5]: 'NIK'}, inplace=True)
            
        if 'NIK' in df.columns:
            df['NIK'] = pd.to_numeric(df['NIK'], errors='coerce').astype('Int64')
            df['NIP'] = df['NIK'].astype(str).str.strip().str.replace('<NA>', '', regex=False).str[-4:]
            df['NIP'] = pd.to_numeric(df['NIP'], errors='coerce').astype('Int64')
            
            # Terapkan pemetaan NIK LAMA -> NIK BARU pada NIP
            pin_mapping = load_dcap_pin_mapping()
            if pin_mapping:
                df['NIP'] = df['NIP'].apply(lambda x: pin_mapping.get(x, x) if pd.notna(x) else x)
                df['NIP'] = pd.to_numeric(df['NIP'], errors='coerce').astype('Int64')

        kolom_pilihan = ['Pin', 'Nama', 'Tanggal', 'Waktu Masuk', 'Waktu Keluar', 'Lokasi Masuk', 'NIK', 'NIP']
        kolom_yang_ada = [col for col in kolom_pilihan if col in df.columns]
        df = df[kolom_yang_ada]
        
        if 'Pin' in df.columns:
            df['Pin'] = pd.to_numeric(df['Pin'], errors='coerce').astype('Int64')
            
        if 'Tanggal' in df.columns:
            # Format tanggal ke YYYY-MM-DD agar mudah dicocokkan dengan DB
            df['Tanggal'] = pd.to_datetime(df['Tanggal'], errors='coerce').dt.strftime('%Y-%m-%d')

        # Koneksi ke database untuk mengecek jk_id
        connection = get_db_connection()
        cursor = connection.cursor()

        df['Scan In Final'] = None
        df['Scan Out Final'] = None

        malam_jk_ids = {7, 15, 18, 22}

        for index, row in df.iterrows():
            nip = str(row['Pin']) if pd.notna(row['Pin']) else None
            tanggal_shift = row['Tanggal']
            waktu_masuk = row['Waktu Masuk']
            waktu_keluar = row['Waktu Keluar']

            if not nip or not tanggal_shift:
                continue

            # Ambil jk_id
            query = '''
                SELECT jk.jk_id
                FROM pegawai p
                JOIN shift_result_new sr ON p.pegawai_id = sr.pegawai_id
                JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
                WHERE p.pegawai_nip = %s AND sr.tgl_shift = %s
            '''
            cursor.execute(query, (nip, tanggal_shift))
            jk_id_result = cursor.fetchone()
            jk_id = jk_id_result[0] if jk_id_result else None

            waktu_masuk_str = str(waktu_masuk).strip() if pd.notna(waktu_masuk) else ""
            waktu_keluar_str = str(waktu_keluar).strip() if pd.notna(waktu_keluar) else ""

            scan_in_final, scan_out_final = process_dcap_scan_times(jk_id, tanggal_shift, waktu_masuk_str, waktu_keluar_str)

            df.at[index, 'Scan In Final'] = scan_in_final
            df.at[index, 'Scan Out Final'] = scan_out_final

        cursor.close()
        connection.close()
        
        table_html = df.to_html(classes='dataframe', index=False, na_rep='')
        
        return render_template('apply_rules_dcap.html', tables=table_html, filename=filename)
    except Exception as e:
        return f"Gagal membaca/apply rules file: {str(e)}"

@app.route('/save_dcap/<filename>', methods=['POST'])
def save_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    try:
        df = pd.read_excel(filepath, header=1)
        df = df.dropna(how='all')
        if not df.empty:
            df = df.iloc[:-1]
            
        # Hapus baris jika Waktu Masuk dan Waktu Keluar keduanya kosong
        if 'Waktu Masuk' in df.columns and 'Waktu Keluar' in df.columns:
            df = df.dropna(subset=['Waktu Masuk', 'Waktu Keluar'], how='all')
            
        # Ambil NIK dari kolom ke-6 (index 5) jika ada
        if len(df.columns) > 5:
            df.rename(columns={df.columns[5]: 'NIK'}, inplace=True)
            
        if 'NIK' in df.columns:
            df['NIK'] = pd.to_numeric(df['NIK'], errors='coerce').astype('Int64')
            df['NIP'] = df['NIK'].astype(str).str.strip().str.replace('<NA>', '', regex=False).str[-4:]
            df['NIP'] = pd.to_numeric(df['NIP'], errors='coerce').astype('Int64')
            
            # Terapkan pemetaan NIK LAMA -> NIK BARU pada NIP
            pin_mapping = load_dcap_pin_mapping()
            if pin_mapping:
                df['NIP'] = df['NIP'].apply(lambda x: pin_mapping.get(x, x) if pd.notna(x) else x)
                df['NIP'] = pd.to_numeric(df['NIP'], errors='coerce').astype('Int64')

        # Filter "Lokasi Masuk" kosong jika checkbox tidak dicentang
        include_empty_lokasi = request.form.get('include_empty_lokasi') == 'yes'
        if not include_empty_lokasi and 'Lokasi Masuk' in df.columns:
            mask = df['Lokasi Masuk'].isna() | (df['Lokasi Masuk'].astype(str).str.strip() == '') | (df['Lokasi Masuk'].astype(str).str.strip().str.lower() == 'nan')
            
            # NIP Khusus Rumjab: tetap masuk ke DB walaupun lokasi masuk kosong
            nip_khusus = [1360, 1359, 2350, 2355, 1316]
            if 'NIP' in df.columns:
                mask_khusus = df['NIP'].isin(nip_khusus)
                df = df[~mask | mask_khusus]
            else:
                df = df[~mask]

        if 'Pin' in df.columns:
            df['Pin'] = pd.to_numeric(df['Pin'], errors='coerce').astype('Int64')
            
        if 'Tanggal' in df.columns:
            df['Tanggal'] = pd.to_datetime(df['Tanggal'], errors='coerce').dt.strftime('%Y-%m-%d')

        connection = get_db_connection()
        cursor = connection.cursor()

        malam_jk_ids = {7, 15, 18, 22}
        all_shift_dates = set()

        for index, row in df.iterrows():
            nip = str(row['NIP']) if 'NIP' in row and pd.notna(row['NIP']) else None
            if not nip and 'Pin' in row:
                nip = str(row['Pin']) if pd.notna(row['Pin']) else None
            tanggal_shift = row['Tanggal']
            waktu_masuk = row['Waktu Masuk']
            waktu_keluar = row['Waktu Keluar']

            if not nip or not tanggal_shift:
                continue

            all_shift_dates.add(tanggal_shift)

            # Ambil jk_id dan pegawai_id
            query = '''
                SELECT jk.jk_id, p.pegawai_id
                FROM pegawai p
                JOIN shift_result_new sr ON p.pegawai_id = sr.pegawai_id
                JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
                WHERE p.pegawai_nip = %s AND sr.tgl_shift = %s
            '''
            cursor.execute(query, (nip, tanggal_shift))
            jk_id_result = cursor.fetchone()
            
            if not jk_id_result:
                # Jika tidak ada jadwal shift, cari setidaknya pegawai_id
                cursor.execute('SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s', (nip,))
                pegawai_id_res = cursor.fetchone()
                if not pegawai_id_res:
                    continue
                pegawai_id = pegawai_id_res[0]
                jk_id = None
            else:
                jk_id = jk_id_result[0]
                pegawai_id = jk_id_result[1]

            waktu_masuk_str = str(waktu_masuk).strip() if pd.notna(waktu_masuk) else ""
            waktu_keluar_str = str(waktu_keluar).strip() if pd.notna(waktu_keluar) else ""

            scan_in_final, scan_out_final = process_dcap_scan_times(jk_id, tanggal_shift, waktu_masuk_str, waktu_keluar_str)

            db_scan_in = scan_in_final if scan_in_final else '0000-00-00 00:00:00'
            db_scan_out = scan_out_final if scan_out_final else '0000-00-00 00:00:00'

            # Cek apakah record absen sudah ada
            cursor.execute('SELECT * FROM shift_result_new WHERE pegawai_id = %s AND tgl_shift = %s', (pegawai_id, tanggal_shift))
            if cursor.fetchone():
                cursor.execute('''
                    UPDATE shift_result_new 
                    SET scan_in = CASE WHEN scan_in IS NULL OR scan_in = '0000-00-00 00:00:00' THEN %s ELSE scan_in END,
                        scan_out = CASE WHEN scan_out IS NULL OR scan_out = '0000-00-00 00:00:00' THEN %s ELSE scan_out END 
                    WHERE pegawai_id = %s AND tgl_shift = %s
                ''', (db_scan_in, db_scan_out, pegawai_id, tanggal_shift))
            else:
                cursor.execute('''
                    INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out)
                    VALUES (%s, %s, %s, %s)
                ''', (pegawai_id, tanggal_shift, db_scan_in, db_scan_out))

        # Pengisian record kosong (dummy) untuk semua pegawai di semua tanggal shift yang diproses
        cursor.execute("SELECT pegawai_id FROM pegawai")
        all_pegawai = cursor.fetchall()
        for tgl_shift in all_shift_dates:
            for pegawai in all_pegawai:
                p_id = pegawai[0]
                if p_id in [0, 1]:
                    continue
                cursor.execute("SELECT 1 FROM shift_result_new WHERE pegawai_id = %s AND tgl_shift = %s", (p_id, tgl_shift))
                if not cursor.fetchone():
                    cursor.execute("""
                    INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out)
                    VALUES (%s, %s, '0000-00-00 00:00:00', '0000-00-00 00:00:00')
                    """, (p_id, tgl_shift))

        connection.commit()
        cursor.close()
        connection.close()
        
        flash('Data DCAP berhasil diproses dan disimpan ke database!', 'success')
        return redirect(url_for('index'))
    except Exception as e:
        return f"Gagal memproses save ke DB: {str(e)}"

import json

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


def save_shift_mappings(shift_mappings):
    """Save shift mappings with UTF-8 encoding"""
    try:
        with open('shift_mappings.json', 'w', encoding='utf-8') as file:
            json.dump(shift_mappings, file, indent=4, ensure_ascii=False)
    except Exception as e:
        pass

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

def detect_file_encoding(filepath):
    """Detect the encoding of a file"""
    try:
        import chardet
        with open(filepath, 'rb') as file:
            raw_data = file.read()
            result = chardet.detect(raw_data)
            return result['encoding']
    except ImportError:
        return None
    except Exception as e:
        return None
    
@app.route('/get_shift_mappings', methods=['GET'])
def get_shift_mappings():
    shift_mappings = load_shift_mappings()
    return jsonify(shift_mappings)

@app.route('/get_employee_data', methods=['GET'])
def get_employee_data_route():
    employee_data = get_employee_data()
    return jsonify(employee_data)

@app.route('/get_jam_kerja_data', methods=['GET'])
def get_jam_kerja_data_route():
    jam_kerja_data = get_jam_kerja_data()
    return jsonify(jam_kerja_data)


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




@app.route('/edit_shift_mappings', methods=['GET', 'POST'])                                                
def edit_shift_mappings():

    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        shift_name = request.form['shift_name']
        nips = request.form.getlist('nips')
        shift_mapping_keys = request.form.getlist('shift_mapping_keys')
        shift_mapping_values = request.form.getlist('shift_mapping_values')

        shift_mapping = {key: int(value) for key, value in zip(shift_mapping_keys, shift_mapping_values)}

        shift_mappings = load_shift_mappings()

        shift_mappings[shift_name] = {
            'nips': nips,
            'shift_mapping': shift_mapping
        }

        save_shift_mappings(shift_mappings)

        return redirect(url_for('edit_shift_mappings'))

    shift_mappings = load_shift_mappings()
    return render_template('edit_shift_mappings.html', shift_mappings=shift_mappings)


@app.template_filter('time')
def time_filter(value):
    if value is None:
        return None
    try:
        return value.strftime('%H:%M:%S')
    except Exception:
        return value
    
@app.route('/export_excel', methods=['GET'])
def export_excel():
    from datetime import datetime, timedelta, time, date
    import pandas as pd
    import os
    
    # route ini untuk export rekap absensi ke file excel
    # dipakai dari dashboard admin, hasilnya sama persis dengan tampilan dashboard tapi dalam format xlsx
    # user bisa filter tanggal, nama, divisi, keterangan, dan opsi tampilkan PM
    
    # ambil semua parameter filter dari url (sama seperti dashboard)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    keterangan = request.args.get('keterangan')
    search = request.args.get('search_name')
    division_ext = request.args.get('division_ext')
    # checkbox show_pm default nyala
    show_pm = request.args.get('show_pm', 'on') == 'on'
    
    # siapkan nama file dan folder reports
    today = datetime.now().strftime('%d-%m-%Y')
    today2 = datetime.now().strftime('%d-%b-%Y')
    reports_dir = "reports"
    os.makedirs(reports_dir, exist_ok=True)

    # nama file output: kalau ada divisi spesifik, sertakan nama divisi di filename
    if division_ext:
        output_file = os.path.join(reports_dir, f'report_{division_ext}_{start_date}.xlsx')
    else:
        output_file = os.path.join(reports_dir, f'report_{start_date}.xlsx')

    try:
        # buka koneksi db
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        # query utama: ambil data absensi + pegawai + shift + divisi + tim
        # mirip dashboard, tapi tambah pembagian3_nama (tim) karena perlu di kolom excel
        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, sr.scan_in, sr.scan_out, 
                   jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name, p.pembagian1_id, pb.pembagian1_nama,
                   pb3.pembagian3_nama as tim_nama, sr.notes
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            LEFT JOIN pembagian1 pb ON p.pembagian1_id = pb.pembagian1_id
            LEFT JOIN pembagian3 pb3 ON p.pembagian3_id = pb3.pembagian3_id
            WHERE sr.tgl_shift BETWEEN %s AND %s AND p.pegawai_status != 0 AND p.pegawai_id != 1
        """

        # kalau show_pm mati, jangan sertakan PM (id 180)
        if not show_pm:
            query += " AND p.pegawai_id != 180"

        params = [start_date, end_date]

        # filter pencarian nama atau nip
        if search:
            search_string = f'%{search}%'
            query += ' AND (p.pegawai_nama LIKE %s OR p.pegawai_nip LIKE %s)'
            params.extend([search_string, search_string])
        
        # filter divisi, ada opsi grup HDTI-HDBS dan QAQC-TA
        if division_ext:
            if division_ext == "HDTI-HDBS":
                query += " AND pb.pembagian1_nama IN (%s, %s)"
                params.extend(["HDTI", "HDBS"])
            elif division_ext == "QAQC-TA":
                query += " AND pb.pembagian1_nama IN (%s, %s)"
                params.extend(["QAQC", "TA"])
            else:
                query += " AND pb.pembagian1_nama = %s"
                params.append(division_ext)

        # jalankan query dan ambil semua record
        cursor.execute(query, params)
        records = cursor.fetchall()

        # kalau show_pm nyala, tambah record dummy untuk PM di tanggal yang kosong
        if show_pm:
            # kumpulin tanggal yang sudah ada untuk PM
            pm_dates = set()
            for record in records:
                if record['pegawai_id'] == 180:
                    pm_dates.add(record['tgl_shift'])
            
            start_dt = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_dt = datetime.strptime(end_date, '%Y-%m-%d').date()
            
            # ambil info dasar PM
            cursor.execute("""
                SELECT p.pegawai_id, p.pegawai_nip, p.pegawai_nama, pb1.pembagian1_nama, pb3.pembagian3_nama as tim_nama
                FROM pegawai p
                LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
                LEFT JOIN pembagian3 pb3 ON p.pembagian3_id = pb3.pembagian3_id
                WHERE p.pegawai_id = 180
            """)
            pm_data = cursor.fetchone()
            
            if pm_data:
                current_date = start_dt
                while current_date <= end_dt:
                    if current_date not in pm_dates:
                        # tambah record virtual PM tanpa scan
                        virtual_record = {
                            'pegawai_id': 180,
                            'pegawai_nip': pm_data['pegawai_nip'],
                            'pegawai_nama': pm_data['pegawai_nama'],
                            'tgl_shift': current_date,
                            'scan_in': None,
                            'scan_out': None,
                            'jk_id': None,
                            'jk_bcin': None,
                            'jk_ecout': None,
                            'jk_name': None,
                            'pembagian1_id': None,
                            'pembagian1_nama': pm_data['pembagian1_nama'],
                            'tim_nama': pm_data['tim_nama'],
                            'notes': None
                        }
                        records.append(virtual_record)
                    current_date += timedelta(days=1)

        # --- mulai proses data untuk excel ---
        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            jk_name = record.get('jk_name')
            tgl_shift = record['tgl_shift']

            # format tanggal jadi mm-dd untuk kolom excel
            record['tanggal'] = tgl_shift.strftime('%m-%d') if tgl_shift else '-'
            # ambil nama tim kalau ada
            record['tim'] = record['tim_nama'] if record['tim_nama'] else '-'
            
            # siapkan kolom TL dan TP (akan diisi nanti)
            record['TL'] = ""
            record['TP'] = ""

            # konversi jam kerja ke time object
            jk_bcin_time = (datetime.min + jk_bcin).time() if jk_bcin else None
            jk_ecout_time = (datetime.min + jk_ecout).time() if jk_ecout else None

            # kalau shift cross-day (5,6,7), pakai tanggal scan_in untuk base
            if jk_id in [5, 6, 7]:
                if scan_in:
                    jk_bcin_time = datetime.combine(scan_in.date(), jk_bcin_time)
                if scan_in and jk_ecout:
                    jk_ecout_time = datetime.combine(scan_in.date(), jk_ecout_time)
            else:
                # shift normal pakai datetime.min + durasi
                if jk_bcin and jk_ecout:
                    jk_bcin_time = datetime.min + jk_bcin
                    jk_ecout_time = datetime.min + jk_ecout

            # cek apakah hari weekend
            is_weekend = tgl_shift.weekday() in [5, 6]

            # penanganan khusus PM (id 180)
            if record['pegawai_id'] == 180:
                if is_weekend:
                    record['keterangan'] = ""
                else:
                    record['keterangan'] = "H"
                # kosongkan semua kolom waktu
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
                record['jk_name'] = "-"
            # shift khusus langsung kasih keterangan
            elif jk_id == 10:
                record['keterangan'] = ""
            elif jk_id == 29:
                record['keterangan'] = "LB"
            elif jk_id == 9:
                record['keterangan'] = "D"
            elif jk_id == 8:
                record['keterangan'] = "CT"
            elif jk_id == 11:
                record['keterangan'] = "S"
            elif jk_id == 30:
                record['keterangan'] = "LDL"
            elif jk_id == 31:
                record['keterangan'] = "LD"
            else:
                # --- kondisi 1: scan in ada, scan out kosong ---
                if scan_in and not scan_out:
                    record['keterangan'] = "H"
                    record['scan_out'] = "-"
                    record['JUMLAH JAM KERJA'] = "-"
                    
                    # cek apakah terlambat
                    is_late = False
                    if jk_bcin_time:
                        if isinstance(jk_bcin_time, datetime):
                            bc_limit = (jk_bcin_time + timedelta(minutes=1)).time()
                        else:
                            bc_limit = (datetime.combine(date.min, jk_bcin_time) + timedelta(minutes=1)).time()
                        
                        if scan_in.time() >= bc_limit:
                            is_late = True
                    
                    # kalau terlambat, tandai TL saja
                    # kalau tidak terlambat, tandai TP (sesuai aturan)
                    if is_late:
                        record['TL'] = "TL"
                        record['TP'] = ""
                    else:
                        record['TL'] = ""
                        record['TP'] = "TP"

                # --- kondisi 2: scan in kosong, scan out ada ---
                elif not scan_in and scan_out:
                    record['keterangan'] = "H"
                    record['TL'] = "TL"
                    record['TP'] = ""
                    record['scan_in'] = "-"
                    record['JUMLAH JAM KERJA'] = "-"

                # --- kondisi 3: tidak scan sama sekali ---
                elif not scan_in and not scan_out:
                    record['keterangan'] = "A"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['TL'] = ""
                    record['TP'] = ""
                    record['JUMLAH JAM KERJA'] = "-"
                
                # --- kondisi 4: scan lengkap (masuk dan pulang) ---
                else:
                    if scan_in and scan_out:
                        if jk_bcin_time and jk_ecout_time:
                            scan_in_time = scan_in.time()
                            scan_out_time = scan_out.time()
                            
                            # tentukan batas terlambat masuk
                            if isinstance(jk_bcin_time, datetime):
                                bc_time_limit = (jk_bcin_time + timedelta(minutes=1)).time()
                            else:
                                bc_time_limit = (datetime.combine(date.min, jk_bcin_time) + timedelta(minutes=1)).time()

                            # aturan puasa: pulang maju 30 menit
                            if tgl_shift >= date(2026, 2, 19) and tgl_shift <= date(2026, 3, 20):
                                if isinstance(jk_ecout_time, datetime):
                                     ec_time_limit = (jk_ecout_time - timedelta(minutes=30)).time()
                                else:
                                     ec_time_limit = (datetime.combine(date.min, jk_ecout_time) - timedelta(minutes=30)).time()
                            else:
                                if isinstance(jk_ecout_time, datetime):
                                    ec_time_limit = jk_ecout_time.time()
                                else:
                                    ec_time_limit = jk_ecout_time

                            # cek pulang valid
                            is_out_valid = scan_out_time >= ec_time_limit
                            
                            # kalau tanggal pulang lebih baru dari masuk, anggap valid (cross-day)
                            if scan_out.date() > scan_in.date():
                                is_out_valid = True

                            # tentukan status akhir
                            if scan_in_time >= bc_time_limit and not is_out_valid:
                                record['keterangan'] = "TP"
                                record['TL'] = ""   
                                record['TP'] = "TP" 
                            
                            elif scan_in_time >= bc_time_limit and is_out_valid:
                                record['keterangan'] = "TL"
                                record['TL'] = "TL"
                            
                            elif scan_in_time <= bc_time_limit and not is_out_valid:
                                record['keterangan'] = "TP"
                                record['TP'] = "TP"
                                
                            elif scan_in_time <= bc_time_limit and is_out_valid:
                                record['keterangan'] = "H"
                                
                            else:
                                record['keterangan'] = "A"
                        else:
                            record['keterangan'] = "A"
                    else:
                        record['keterangan'] = "A"

            # kalau status libur/cuti/sakit, kosongkan kolom waktu
            if record['keterangan'] in ["LB", "CT", "LDL", "LD", "S", ""]:
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
                record['TL'] = ""
                record['TP'] = ""
                record['JUMLAH JAM KERJA'] = "-"
            else:
                # format waktu masuk dan pulang real
                if scan_in and scan_in != "-":
                    record['scan_in'] = scan_in.strftime('%H:%M')
                if scan_out and scan_out != "-":
                    record['scan_out'] = scan_out.strftime('%H:%M')
                
                # format jam kerja standar
                if jk_bcin_time and jk_bcin_time != "-":
                    if isinstance(jk_bcin_time, datetime):
                        record['jk_bcin'] = jk_bcin_time.strftime('%H:%M')
                    else:
                        record['jk_bcin'] = jk_bcin_time.strftime('%H:%M')
                
                if jk_ecout_time and jk_ecout_time != "-":
                    if isinstance(jk_ecout_time, time):  
                        jk_ecout_datetime = datetime.combine(date.today(), jk_ecout_time)
                    else:
                        jk_ecout_datetime = jk_ecout_time

                    # kalau puasa, tampilan pulang dikurangi 30 menit
                    if tgl_shift >= date(2025, 3, 1) and tgl_shift <= date(2025, 3, 31):
                        adjusted_time = (jk_ecout_datetime - timedelta(minutes=30)).time()
                        record['jk_ecout'] = adjusted_time.strftime('%H:%M')
                    else:
                        record['jk_ecout'] = jk_ecout_datetime.time().strftime('%H:%M')

        # kalau ada filter keterangan, hapus record yang tidak cocok
        if keterangan:
            records = [record for record in records if record['keterangan'] == keterangan]

        # ubah list record jadi dataframe pandas
        df = pd.DataFrame(records)
        
        # pilih kolom yang dibutuhkan untuk excel
        df = df[['pegawai_nama', 'jk_bcin', 'jk_ecout', 'scan_in', 'scan_out', 'keterangan', 'tanggal', 'tim', 'TL', 'TP', 'notes']]

        # ubah nama kolom supaya sesuai header excel
        df.columns = ['NAMA PEGAWAI', 'MASUK', 'PULANG', 'MASUK REAL', 'PULANG REAL', 'KETERANGAN', 'TANGGAL', 'TIM', 'TL', 'TP', 'NOTES']

        # isi kolom kosong dengan string kosong
        df['TL'] = df['TL'].fillna('')
        df['TP'] = df['TP'].fillna('')

        # kalau ada TL atau TP, ubah keterangan jadi H (karena tetap dianggap hadir)
        df.loc[df['TL'] == 'TL', 'KETERANGAN'] = 'H' 
        df.loc[df['TP'] == 'TP', 'KETERANGAN'] = 'H'

        # fungsi hitung durasi jam kerja (masuk real ke pulang real)
        def calculate_hours(row):
            if row['MASUK REAL'] == '-' or row['PULANG REAL'] == '-':
                return '-'
            if row['MASUK REAL'] == '-' or row['PULANG REAL'] == '-':
                return 'N/A'
            try:
                masuk = datetime.strptime(row['MASUK REAL'], '%H:%M')
                pulang = datetime.strptime(row['PULANG REAL'], '%H:%M')

                # kalau pulang lebih kecil dari masuk, anggap besok (cross-day)
                if pulang < masuk:
                    pulang += timedelta(days=1)

                duration = pulang - masuk
                duration_in_hours = duration.seconds // 3600
                duration_in_minutes = (duration.seconds % 3600) // 60
                return f'{duration_in_hours:02}:{duration_in_minutes:02}'
            except:
                return '-'

        # safety net: kalau TL/TP kosong tapi seharusnya ada, paksa isi
        df['TL'] = df.apply(lambda row: 'TL' if (row['TL'] == '') and (row['MASUK REAL'] == '-') and row['PULANG REAL'] != '-' else row['TL'], axis=1)
        
        # safety net untuk TP (hanya kalau bukan TL)
        df['TP'] = df.apply(lambda row: 'TP' if (row['TP'] == '') and row['MASUK REAL'] != '-' and row['PULANG REAL'] == '-' and row['TL'] != 'TL' else row['TP'], axis=1)

        # kalau status D (dinas), kosongkan scan masuk/pulang
        df['MASUK REAL'] = df.apply(lambda row: '-' if row['KETERANGAN'] == 'D' and row['MASUK REAL'] in [None, ''] else row['MASUK REAL'], axis=1)
        df['PULANG REAL'] = df.apply(lambda row: '-' if row['KETERANGAN'] == 'D' and row['PULANG REAL'] in [None, ''] else row['PULANG REAL'], axis=1)
        df.loc[(df['KETERANGAN'] == 'D') & (df['MASUK REAL'] == '-') & (df['PULANG REAL'] == '-'), ['MASUK REAL', 'PULANG REAL']] = '-'

        # hitung jumlah jam kerja untuk tiap baris
        df['JUMLAH JAM KERJA'] = df.apply(calculate_hours, axis=1)

        # urutkan kolom sesuai urutan yang diinginkan
        df = df[['NAMA PEGAWAI', 'MASUK', 'PULANG', 'MASUK REAL', 'PULANG REAL', 'TL', 'TP', 'JUMLAH JAM KERJA', 'KETERANGAN', 'TANGGAL', 'TIM', 'NOTES']]

        # urutkan berdasarkan nama pegawai
        df = df.sort_values(by='NAMA PEGAWAI')

        # tambah kolom nomor urut
        df.insert(0, 'NO', range(1, len(df) + 1))

        # tambah baris footer: tanggal cetak
        date_info = pd.DataFrame([['Tanggal Cetak:', today2]], columns=['NAMA PEGAWAI', 'MASUK'])
        df = pd.concat([df, date_info], ignore_index=True, sort=False)
        df.at[len(df)-1, 'MASUK'] = f'{today2}'

        # simpan ke excel dengan format rapi
        with pd.ExcelWriter(output_file, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=None, sheet_name='Sheet1', startrow=1)

            workbook = writer.book
            worksheet = writer.sheets['Sheet1']

            # atur lebar kolom supaya rapi
            worksheet.set_column('A:A', 4)    
            worksheet.set_column('B:B', 50)  
            worksheet.set_column('C:D', 18)   
            worksheet.set_column('E:F', 18)   
            worksheet.set_column('G:H', 7)   
            worksheet.set_column('I:I', 18)   
            worksheet.set_column('J:J', 14)  
            worksheet.set_column('K:K', 10)   
            worksheet.set_column('L:L', 15)   
            worksheet.set_column('M:M', 30)   

            # format header (merge cell dan bold)
            header_format = workbook.add_format({
                'bold': True,
                'text_wrap': True,
                'valign': 'middle',
                'align': 'center',
                'border': 1
            })

            worksheet.merge_range('A1:A2', 'NO', header_format)
            worksheet.merge_range('B1:B2', 'NAMA PEGAWAI', header_format)
            worksheet.merge_range('C1:D1', 'WAKTU KERJA SESUAI KETENTUAN', header_format)
            worksheet.merge_range('E1:F1', 'WAKTU KERJA REAL', header_format)
            worksheet.merge_range('G1:H1', 'KETERANGAN', header_format)
            worksheet.merge_range('I1:I2', 'JUMLAH JAM KERJA', header_format)
            worksheet.merge_range('J1:J2', 'KETERANGAN', header_format)
            worksheet.merge_range('K1:K2', 'TANGGAL', header_format)
            worksheet.merge_range('L1:L2', 'TIM', header_format)
            worksheet.merge_range('M1:M2', 'NOTES', header_format)

            # tulis header lagi di baris 2
            for col_num, value in enumerate(df.columns.values):
                worksheet.write(1, col_num, value, header_format)

            # format border untuk semua sel
            format_border = workbook.add_format({
                'bold': False,
                'text_wrap': False,
                'valign': 'middle',
                'align': 'center',
                'border': 1,
                'bg_color': '#FFFFFF',
            })

            num_rows = len(df)
            range_start = f'A1:M{num_rows + 1}'  
            format_border.set_border(1)
            worksheet.conditional_format(range_start, {'type': 'no_blanks', 'format': format_border})
            worksheet.conditional_format(range_start, {'type': 'blanks', 'format': format_border})

        # kirim file excel ke browser sebagai download
        return send_file(output_file, as_attachment=True)

    except Exception as e:
        # kalau error, log ke console dan kembali ke dashboard
        print(f"Error: {e}")
        return redirect(url_for('index'))
    finally:
        # tutup koneksi db
        if connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/update-schedule', methods=['GET', 'POST'])
def update_schedule():
    # route ini untuk koreksi manual jadwal dan scan absensi satu pegawai satu tanggal
    # admin bisa ubah jk_id (shift), scan_in, scan_out dari form edit
    # kalau POST: simpan perubahan pakai upsert (insert kalau belum ada, update kalau sudah)
    # kalau GET: tampilkan form edit dengan data existing + dropdown semua shift

    # cek dulu user sudah login sebagai admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        # ambil data dari form edit
        pegawai_id = request.form['pegawai_id']
        tgl_shift = request.form['tgl_shift']
        jk_id = request.form['jk_id']
        scan_in = request.form['scan_in']
        scan_out = request.form['scan_out']
        notes = request.form.get('notes', '')
        # simpan referrer supaya balik ke halaman sebelumnya (misal att_log atau dashboard)
        referrer = request.form.get('referrer', url_for('index'))

        try:
            # buka koneksi db
            connection = get_db_connection()
            cursor = connection.cursor()

            # query upsert: insert kalau belum ada record, update kalau sudah ada
            # selalu overwrite jk_id, scan_in, scan_out (tidak kondisional)
            update_query = """
                INSERT INTO shift_result_new (pegawai_id, tgl_shift, jk_id, scan_in, scan_out, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                jk_id = VALUES(jk_id),
                scan_in = VALUES(scan_in),
                scan_out = VALUES(scan_out),
                notes = VALUES(notes)
            """
            cursor.execute(update_query, (pegawai_id, tgl_shift, jk_id, scan_in, scan_out, notes))
            # commit perubahan
            connection.commit()
        except Error as e:
            # kalau error db, skip saja (tidak kasih pesan ke user)
            pass
        finally:
            # tutup koneksi supaya aman
            if connection.is_connected():
                cursor.close()
                connection.close()
        
        # setelah simpan, balik ke halaman sebelumnya
        return redirect(referrer)
    
    else:
        # kalau GET: tampilkan form edit
        # simpan referrer supaya bisa balik setelah submit
        referrer = request.referrer or url_for('index')
        # ambil pegawai_id dan tgl_shift dari query string
        pegawai_id = request.args.get('pegawai_id')
        tgl_shift = request.args.get('tgl_shift')
        try:
            # buka koneksi db
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)

            # ambil semua daftar shift untuk dropdown di form
            cursor.execute("SELECT jk_id, jk_name FROM jam_kerja")
            jadwal_kerja = cursor.fetchall()

            # ambil data jadwal existing untuk pegawai dan tanggal ini
            cursor.execute("""
                SELECT sr.pegawai_id, sr.tgl_shift, sr.jk_id, sr.scan_in, sr.scan_out, p.pegawai_nama, sr.notes
                FROM shift_result_new sr
                JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
                WHERE sr.pegawai_id = %s AND sr.tgl_shift = %s
            """, (pegawai_id, tgl_shift))
            schedule = cursor.fetchone()

            # kalau ada data, format scan_in/out jadi format datetime-local (yyyy-mm-ddThh:mm)
            if schedule:
                schedule['scan_in'] = schedule['scan_in'].strftime('%Y-%m-%dT%H:%M') if schedule['scan_in'] else ''
                schedule['scan_out'] = schedule['scan_out'].strftime('%Y-%m-%dT%H:%M') if schedule['scan_out'] else ''

        except Error as e:
            # kalau error db, kosongkan data
            jadwal_kerja = []
            schedule = {}
        finally:
            # tutup koneksi
            if connection.is_connected():
                cursor.close()
                connection.close()

        # tampilkan form edit dengan data yang sudah diambil
        return render_template(
            'update_schedule.html',
            jadwal_kerja=jadwal_kerja,
            schedule=schedule,
            referrer=referrer
        )


@app.route('/att_log', methods=['GET'])
def att_log():
    # route ini adalah halaman log absensi mentah dari tabel att_log
    # admin bisa lihat semua scan masuk/keluar per pegawai dalam rentang tanggal
    # ada filter tanggal dan divisi, plus penanganan khusus admin qaqc

    # cek login dan role admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        flash('Please login to access this page.', 'warning')
        return redirect(url_for('login'))

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
            'att_log.html',
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
                'att_log.html',
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
                    'att_log.html',
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
            'att_log.html',
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
        'att_log.html',
        headers=['Nama Pegawai', 'PIN', 'Tanggal Scan', 'Scan In', 'Scan Out', 'Verify Mode'],
        records=final_df.to_dict('records'),
        divisions=divisions,
        start_date=start_date,
        end_date=end_date,
        selected_division=division_filter,
        hapus_card=hapus_card
    )

@app.route('/update_shift_result', methods=['POST'])
def update_shift_result():
    # route ini dipanggil saat admin edit scan in/out dari halaman att_log
    # tugasnya: update scan_in dan scan_out di shift_result_new untuk record yang dipilih

    print("\n========== DEBUG update_shift_result ==========")

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        print("[AUTH] Unauthorized")
        return redirect(url_for('login'))

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
        return redirect(url_for('att_log'))

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

            set_clauses = []
            params = []

            # kalau ada scan_in baru dan bukan placeholder, tambah ke update
            if scan_in_str and scan_in_str != '--:--':
                scan_in = datetime.strptime(
                    f"{tanggal_shift} {scan_in_str}",
                    "%Y-%m-%d %H:%M"
                )
                set_clauses.append("scan_in = %s")
                params.append(scan_in)
                print(f"[UPDATE] scan_in = {scan_in}")

            # kalau ada scan_out baru dan bukan placeholder, tambah ke update
            if scan_out_str and scan_out_str != '--:--':
                scan_out = datetime.strptime(
                    f"{tanggal_shift} {scan_out_str}",
                    "%Y-%m-%d %H:%M"
                )

                # kalau scan_out lebih kecil dari scan_in, anggap lewat tengah malam
                if scan_in_str and scan_in_str != '--:--':
                    scan_in_tmp = datetime.strptime(
                        f"{tanggal_shift} {scan_in_str}",
                        "%Y-%m-%d %H:%M"
                    )
                    if scan_out < scan_in_tmp:
                        scan_out += timedelta(days=1)

                set_clauses.append("scan_out = %s")
                params.append(scan_out)
                print(f"[UPDATE] scan_out = {scan_out}")

            # kalau tidak ada yang diubah, skip record ini
            if not set_clauses:
                print("[SKIP] No valid scan_in / scan_out")
                continue

            # query update: hanya ubah scan_in/out di record yang cocok
            update_sql = f"""
                UPDATE shift_result_new
                SET {', '.join(set_clauses)}
                WHERE pegawai_id = (
                    SELECT pegawai_id FROM pegawai WHERE pegawai_pin = %s
                )
                AND tgl_shift = %s
            """

            params.extend([pin, tanggal_shift])

            cur.execute(update_sql, tuple(params))
            conn.commit()

            # kalau ada baris yang berubah, tambah counter
            if cur.rowcount > 0:
                updated_count += 1
                print("[SUCCESS] Row updated")
            else:
                print("[INFO] No matching row in shift_result_new")

    except Exception as e:
        print(f"[ERROR] {e}")

    finally:
        # tutup koneksi db
        if conn.is_connected():
            cur.close()
            conn.close()
            print("[DB] Connection closed")

    print(f"\n[TOTAL UPDATED] {updated_count} record(s)")
    print("========== END DEBUG update_shift_result ==========\n")

    # kembali ke halaman att_log dengan filter yang sama
    return redirect(url_for(
        'att_log',
        start_date=request.form.get('start_date'),
        end_date=request.form.get('end_date'),
        division=request.form.get('division')
    ))



def get_pembagian_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian1_id, pembagian1_nama FROM pembagian1")
    pembagian = cursor.fetchall()
    cursor.close()
    conn.close()
    return pembagian


def get_pembagian2_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian2_id, pembagian2_nama FROM pembagian2")
    pembagian = cursor.fetchall()
    cursor.close()
    conn.close()
    return pembagian



def get_pembagian_list():
    # fungsi kecil ini ambil semua data divisi (pembagian1) dari database
    # dipakai buat dropdown di form create/edit pegawai
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    # ambil id dan nama divisi, urutkan alfabet
    cursor.execute("SELECT pembagian1_id, pembagian1_nama FROM pembagian1")
    pembagian = cursor.fetchall()
    cursor.close()
    conn.close()
    # kembalikan list dict supaya mudah dipakai di template
    return pembagian


def get_pembagian2_list():
    # sama seperti atas, tapi ambil daftar jabatan/fungsi (pembagian2)
    # buat dropdown jabatan di form pegawai
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian2_id, pembagian2_nama FROM pembagian2")
    pembagian = cursor.fetchall()
    cursor.close()
    conn.close()
    return pembagian




@app.route('/pegawai')
def index_pegawai():
    # halaman master data pegawai – daftar semua karyawan
    # admin bisa lihat detail, edit, hapus dari sini
    # admin qaqc dibatasi hanya lihat pegawai divisi QAQC dan TA

    # cek login dan role admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    # cek apakah user ini admin qaqc (username atau id=2)
    is_admin_qaqc = (
        session.get('username') == 'admin-qaqc'
        or session.get('id') == 2
    )

    # buka koneksi db
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # query ambil semua pegawai (kecuali id 1 yang mungkin sistem/dummy)
    # join divisi, jabatan, tim supaya tampil lengkap
    query = """
        SELECT p.pegawai_id, p.pegawai_pin, p.pegawai_nip, p.pegawai_nama, 
               p.pegawai_alias, p.pegawai_nik_lintas, p.pegawai_status, p.sisa_cuti,
               b1.pembagian1_nama, b2.pembagian2_nama, b3.pembagian3_nama
        FROM pegawai p
        LEFT JOIN pembagian1 b1 ON p.pembagian1_id = b1.pembagian1_id
        LEFT JOIN pembagian2 b2 ON p.pembagian2_id = b2.pembagian2_id
        LEFT JOIN pembagian3 b3 ON p.pembagian3_id = b3.pembagian3_id
        WHERE p.pegawai_id != 1
    """

    params = []

    # kalau admin qaqc, tambah filter supaya hanya QAQC dan TA
    if is_admin_qaqc:
        query += " AND b1.pembagian1_nama IN (%s, %s)"
        params.extend(['QAQC', 'TA'])

    # urutkan berdasarkan nama pegawai (alfabet)
    query += " ORDER BY p.pegawai_nama ASC"

    cursor.execute(query, params)
    pegawai = cursor.fetchall()
    cursor.close()
    conn.close()
    
    # kirim data ke template untuk ditampilkan dalam tabel
    return render_template('pegawai_index.html', pegawai=pegawai)





@app.route('/pegawai/create', methods=['GET', 'POST'])
def create_pegawai():
    # halaman tambah pegawai baru – hanya admin biasa
    # GET: tampilkan form kosong + dropdown divisi/jabatan/tim
    # POST: simpan data baru ke tabel pegawai dengan password default

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        # ambil semua input dari form
        pegawai_pin = request.form['pegawai_pin']
        pegawai_nip = request.form['pegawai_nip']
        pegawai_nama = request.form['pegawai_nama']
        pegawai_alias = request.form.get('pegawai_alias') 
        pegawai_nik_lintas = request.form.get('pegawai_nik_lintas')  
        pembagian1_id = request.form['pembagian1_id']
        pembagian2_id = request.form['pembagian2_id']
        # pembagian3 boleh kosong (null)
        pembagian3_id = request.form.get('pembagian3_id') or None
        sisa_cuti = request.form.get('sisa_cuti', 0)
        # password awal selalu default ini
        pegawai_pwd = os.environ.get('INITIAL_EMPLOYEE_PASSWORD', 'DemoUser123!')

        conn = get_db_connection()
        cursor = conn.cursor()
        # simpan pegawai baru ke database
        cursor.execute("""
            INSERT INTO pegawai (pegawai_pin, pegawai_nip, pegawai_nama, pegawai_alias, pegawai_nik_lintas, 
                               pembagian1_id, pembagian2_id, pembagian3_id, sisa_cuti, pegawai_pwd)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (pegawai_pin, pegawai_nip, pegawai_nama, pegawai_alias, pegawai_nik_lintas, 
              pembagian1_id, pembagian2_id, pembagian3_id, sisa_cuti, pegawai_pwd))
        conn.commit()
        cursor.close()
        conn.close()

        # setelah simpan, balik ke daftar pegawai
        return redirect(url_for('index_pegawai'))

    # kalau GET: ambil daftar untuk dropdown
    pembagian1 = get_pembagian_list() 
    pembagian2 = get_pembagian2_list()
    pembagian3 = get_pembagian3_list()
    # tampilkan form create
    return render_template('pegawai_create.html', pembagian1=pembagian1, pembagian2=pembagian2, pembagian3=pembagian3)


@app.route('/pegawai/edit/<int:pegawai_id>', methods=['GET', 'POST'])
def edit_pegawai(pegawai_id):
    # halaman edit data pegawai tertentu
    # GET: tampilkan form dengan data pegawai yang sudah ada + dropdown
    # POST: simpan perubahan (password tidak diubah di sini)

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # ambil data pegawai berdasarkan id
        cursor.execute("SELECT * FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
        pegawai = cursor.fetchone()

        # kalau tidak ketemu, balik ke daftar
        if not pegawai:
            return redirect(url_for('index_pegawai'))

        if request.method == 'POST':
            # ambil data baru dari form
            pegawai_pin = request.form['pegawai_pin']
            pegawai_nip = request.form['pegawai_nip']
            pegawai_nama = request.form['pegawai_nama']
            pegawai_alias = request.form.get('pegawai_alias') or None
            pegawai_nik_lintas = request.form.get('pegawai_nik_lintas') or None
            pembagian1_id = request.form['pembagian1_id']
            pembagian2_id = request.form['pembagian2_id']
            pembagian3_id = request.form.get('pembagian3_id') or None
            pegawai_status = int(request.form['pegawai_status'])
            sisa_cuti = request.form.get('sisa_cuti', 0)

            # update data pegawai (password tidak disentuh)
            cursor.execute("""
                UPDATE pegawai 
                SET pegawai_pin = %s, pegawai_nip = %s, pegawai_nama = %s, 
                    pegawai_alias = %s, pegawai_nik_lintas = %s, pembagian1_id = %s, 
                    pembagian2_id = %s, pembagian3_id = %s, pegawai_status = %s, sisa_cuti = %s
                WHERE pegawai_id = %s
            """, (pegawai_pin, pegawai_nip, pegawai_nama, pegawai_alias, pegawai_nik_lintas, 
                  pembagian1_id, pembagian2_id, pembagian3_id, pegawai_status, sisa_cuti, pegawai_id))
            conn.commit()

            # setelah update berhasil, balik ke daftar
            return redirect(url_for('index_pegawai'))
        
        # kalau GET: ambil daftar dropdown
        pembagian1 = get_pembagian_list()
        pembagian2 = get_pembagian2_list()
        pembagian3 = get_pembagian3_list()
        return render_template('pegawai_edit.html', pegawai=pegawai, pembagian1=pembagian1, pembagian2=pembagian2, pembagian3=pembagian3)

    except Exception as e:
        # kalau error, tetap tampilkan form dengan data existing
        return render_template('pegawai_edit.html', pegawai=pegawai, pembagian1=get_pembagian_list(), pembagian2=get_pembagian2_list(), pembagian3=get_pembagian3_list())
    finally:
        # pastikan koneksi ditutup
        cursor.close()
        conn.close()

@app.route('/pegawai/delete/<int:pegawai_id>', methods=['POST'])
def delete_pegawai(pegawai_id):
    # route hapus pegawai – dipanggil via POST (biasanya tombol konfirmasi)
    # hard delete langsung dari tabel pegawai

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    # hapus pegawai berdasarkan id
    cursor.execute("DELETE FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
    conn.commit()
    cursor.close()
    conn.close()

    # setelah hapus, balik ke daftar pegawai
    return redirect(url_for('index_pegawai'))

def get_pembagian3_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian3_id, pembagian3_nama FROM pembagian3 ORDER BY pembagian3_nama")
    pembagian3 = cursor.fetchall()
    cursor.close()
    conn.close()
    return pembagian3


@app.route('/fungsi', methods=['GET'])
def index_pembagian2():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian2_id, pembagian2_nama, pembagian2_ket FROM pembagian2")
    pembagian2_data = cursor.fetchall()
    cursor.close()
    conn.close()

    return render_template('pembagian_index.html', pembagian2_data=pembagian2_data)


@app.route('/fungsi/create', methods=['GET', 'POST'])
def create_pembagian2():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    if request.method == 'POST':
        pembagian2_nama = request.form['pembagian2_nama']
        pembagian2_ket = request.form['pembagian2_ket']

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO pembagian2 (pembagian2_nama, pembagian2_ket) VALUES (%s, %s)",
                       (pembagian2_nama, pembagian2_ket))
        conn.commit()
        cursor.close()
        conn.close()

        return redirect(url_for('index_pembagian2'))

    return render_template('pembagian_create.html')

@app.route('/fungsi/update/<int:id>', methods=['GET', 'POST'])
def update_pembagian2(id):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM pembagian2 WHERE pembagian2_id = %s", (id,))
    pembagian2 = cursor.fetchone()

    if request.method == 'POST':
        pembagian2_nama = request.form['pembagian2_nama']
        pembagian2_ket = request.form['pembagian2_ket']

        cursor.execute("""
            UPDATE pembagian2 SET pembagian2_nama = %s, pembagian2_ket = %s WHERE pembagian2_id = %s
        """, (pembagian2_nama, pembagian2_ket, id))
        conn.commit()
        cursor.close()
        conn.close()

        return redirect(url_for('index_pembagian2'))

    cursor.close()
    conn.close()
    return render_template('pembagian_update.html', pembagian2=pembagian2)


@app.route('/fungsi/delete/<int:id>', methods=['POST'])
def delete_pembagian2(id):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM pembagian2 WHERE pembagian2_id = %s", (id,))
    conn.commit()
    cursor.close()
    conn.close()

    return redirect(url_for('index_pembagian2'))


def read_connection_json():
    json_file_path = os.path.join(os.path.dirname(__file__), 'connection.json')
    with open(json_file_path, 'r') as json_file:
        return json.load(json_file)

@app.route('/edit_connection', methods=['GET'])
def edit_connection():
    import socket
    config = read_connection_json()
    server_ip = socket.gethostbyname(socket.gethostname())
    return render_template('edit_connection.html', config=config, server_ip=server_ip)

@app.route('/save_connection', methods=['POST'])
def save_connection():
    config = {
        "user": request.form['user'],
        "password": request.form['password'],
        "host": request.form['host'],
        "database": request.form['database'],
        "port": int(request.form['port']),
        "charset": request.form['charset']
    }
    
    json_file_path = os.path.join(os.path.dirname(__file__), 'connection.json')
    with open(json_file_path, 'w') as json_file:
        json.dump(config, json_file, indent=4)
    
    return redirect(url_for('edit_connection'))


@app.route('/edit-nip-mapping', methods=['GET', 'POST'])
def edit_nip_mapping():

    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    JSON_FILE_PATH = 'mapping_nip_old_absen_to_real_nip.json'
    if request.method == 'POST':
        try:
            new_mapping = {}
            old_nip_list = request.form.getlist('old_nip')
            new_nip_list = request.form.getlist('new_nip')

        
            new_old_nip = request.form.get('new_old_nip')
            new_new_nip = request.form.get('new_new_nip')

            if new_old_nip and new_new_nip:
                old_nip_list.append(new_old_nip)
                new_nip_list.append(new_new_nip)

            
            for old_nip, new_nip in zip(old_nip_list, new_nip_list):
                if old_nip and new_nip:
                    new_mapping[old_nip] = new_nip

           
            with open(JSON_FILE_PATH, 'w') as json_file:
                json.dump(new_mapping, json_file, indent=4)

            return redirect(url_for('edit_nip_mapping'))
        except Exception as e:
            pass

    
    try:
        with open(JSON_FILE_PATH, 'r') as json_file:
            nip_mapping = json.load(json_file)
    except Exception as e:
        nip_mapping = {}

    
    pegawai_names = {}
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        for new_nip in nip_mapping.values():
            cursor.execute("SELECT pegawai_nama FROM pegawai WHERE pegawai_nip = %s", (new_nip,))
            result = cursor.fetchone()
            if result:
                pegawai_names[new_nip] = result[0]
            else:
                pegawai_names[new_nip] = 'Nama tidak ditemukan'

        cursor.close()
        connection.close()
    except Exception as e:
        pass

    return render_template('edit_nip_mapping.html', nip_mapping=nip_mapping, pegawai_names=pegawai_names)



@app.route('/search_month', methods=['GET', 'POST'])
def search_month():
    if request.method == 'POST':
        search_name = request.form.get('search_name')
        selected_month = request.form.get('month')


        return redirect(url_for('search_month_result', search_name=search_name, month=selected_month))

    return render_template('search_month.html')


@app.route('/search_month_result') 
def search_month_result():

    from datetime import datetime, timedelta, time
    from dateutil.relativedelta import relativedelta

    from datetime import datetime, timedelta, time

    from datetime import datetime, timedelta

    search_name = request.args.get('search_name')
    month = request.args.get('month')

    if not search_name or not month:
        return redirect(url_for('search_month'))

    year, month = month.split('-')
    start_date = f"{year}-{month}-01"
    end_date = (datetime.strptime(start_date, "%Y-%m-%d") + relativedelta(months=1) - timedelta(days=1)).strftime('%Y-%m-%d')

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, sr.scan_in, sr.scan_out, 
                   jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            WHERE (p.pegawai_nama LIKE %s OR p.pegawai_nip LIKE %s)
            AND sr.tgl_shift BETWEEN %s AND %s
        """
        
        search_string = f"%{search_name}%"
        cursor.execute(query, (search_string, search_string, start_date, end_date))
        records = cursor.fetchall()

        if records:
            pegawai_nip = records[0]['pegawai_nip']
            pegawai_nama = records[0]['pegawai_nama']
        else:
            pegawai_nip = None
            pegawai_nama = None


        month_name = datetime.strptime(month, "%m").strftime('%B')

        generate_time = datetime.now().strftime('%d/%m/%Y %H:%M')


        total_hadir = 0
        total_ct = 0
        total_d = 0
        total_tl = 0
        total_tp = 0
        total_a = 0

        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            tgl_shift = record['tgl_shift']
            record['tgl_shift'] = tgl_shift.strftime('%d')

            jk_bcin_time = None
            jk_ecout_time = None

            if scan_in and scan_out and scan_in != "0000-00-00 00:00:00" and scan_out != "0000-00-00 00:00:00":
                duration = scan_out - scan_in
                hours, remainder = divmod(duration.total_seconds(), 3600)
                minutes = remainder // 60
                record['work_duration'] = f"{int(hours)}:{int(minutes):02d}"
            else:
                record['work_duration'] = "-"

            if jk_bcin and jk_ecout:
                base_date = scan_in.date() if scan_in else tgl_shift

                if isinstance(jk_bcin, timedelta):
                    jk_bcin_time = (datetime.min + jk_bcin).time()
                else:
                    jk_bcin_time = jk_bcin
                
                if isinstance(jk_ecout, timedelta):
                    jk_ecout_time = (datetime.min + jk_ecout).time()
                else:
                    jk_ecout_time = jk_ecout

                if scan_in:
                    jk_bcin_time = datetime.combine(scan_in.date(), jk_bcin_time) if jk_bcin_time else None
                    jk_ecout_time = datetime.combine(scan_in.date(), jk_ecout_time) if jk_ecout_time else None

            is_weekend = tgl_shift.weekday() in [5, 6]

            record['tp'] = '-'
            record['tl'] = '-'

            if record['pegawai_id'] == 180:
                if is_weekend:
                    record['keterangan'] = "LB"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
                else:
                    record['keterangan'] = "H"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
            elif jk_id in [5, 6, 7, 10, 9, 8, 11, 29, 30, 31]:
                keterangan_map = {
                    5: "LB", 6: "LB", 7: "LB",
                    10: "LB",
                    9: "D",
                    8: "CT",
                    11: "S",
                    29: "LB",
                    30: "LDL",
                    31: "LD"
                }
                record['keterangan'] = keterangan_map.get(jk_id, "A")
            else:
                if scan_in and (scan_out is None or scan_out == "0000-00-00 00:00:00"):
                    record['tp'] = "TP"
                    record['keterangan'] = "H"
                elif scan_out and (scan_in is None or scan_in == "0000-00-00 00:00:00"):
                    record['tl'] = "TL" 
                    record['keterangan'] = "H" 
                elif scan_in and scan_out:
                    if jk_bcin_time and jk_ecout_time:
                        if scan_in.time() >= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() >= jk_ecout_time.time():
                            record['tl'] = "TL"
                            record['keterangan'] = "H"  
                            total_tl += 1
                            print(f"TL condition met for {record['pegawai_id']} on {tgl_shift}")
                        elif scan_in.time() <= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() >= jk_ecout_time.time():
                            record['keterangan'] = "H"
                        elif scan_in.time() >= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() < jk_ecout_time.time():
                            record['tp'] = "TP"
                            record['keterangan'] = "H" 
                            total_tp += 1
                            print(f"TP condition met for {record['pegawai_id']} on {tgl_shift}")
                        elif scan_in.time() <= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() < jk_ecout_time.time():
                            record['tp'] = "TP"
                            record['keterangan'] = "H"
                            total_tp += 1
                        else:
                            record['keterangan'] = "A"
                    else:
                        record['keterangan'] = "A"
                else:
                    record['keterangan'] = "A"

            if record['keterangan'] in ["LB", "CT", "LDL", "LD", "S"]:
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
            else:
                if scan_in:
                    record['scan_in'] = scan_in.strftime('%H:%M')
                if scan_out:
                    record['scan_out'] = scan_out.strftime('%H:%M')
                if jk_bcin_time:
                    record['jk_bcin'] = jk_bcin_time.strftime('%H:%M')
                if jk_ecout_time:
                    record['jk_ecout'] = jk_ecout_time.strftime('%H:%M')


            if record['keterangan'] == 'H':
                total_hadir += 1
            elif record['keterangan'] == 'CT':
                total_ct += 1
            elif record['keterangan'] == 'D':
                total_d += 1
            elif record['keterangan'] == 'A':
                total_a += 1

        return render_template(
            'search_month_result.html',
            records=records, 
            search_name=pegawai_nama,
            nip=pegawai_nip,
            month=month_name,
            year=year,
            generate_time=generate_time,
            total_hadir=total_hadir,
            total_ct=total_ct,
            total_d=total_d,
            total_tl=total_tl,
            total_tp=total_tp,
            total_a=total_a
        )
    except Error as e:
        return redirect(url_for('search_month'))
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/save_html') 
def save_html():
    from dateutil.relativedelta import relativedelta
    from datetime import datetime, timedelta
    from flask import make_response
    import calendar
    from xhtml2pdf import pisa
    import os
    import requests

    search_name = request.args.get('search_name')
    month = request.args.get('month')  
    
    if not search_name or not month:
        return redirect(url_for('search_month'))

    try:

        month_name, year = month.split('-')  

       
        month_number = list(calendar.month_name).index(month_name)


        start_date = f"{year}-{month_number:02d}-01" 
        end_date = (datetime.strptime(start_date, "%Y-%m-%d") + relativedelta(months=1) - timedelta(days=1)).strftime('%Y-%m-%d')

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, sr.scan_in, sr.scan_out, 
                   jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            WHERE (p.pegawai_nama LIKE %s OR p.pegawai_nip LIKE %s)
            AND sr.tgl_shift BETWEEN %s AND %s
        """

        search_string = f"%{search_name}%"
        cursor.execute(query, (search_string, search_string, start_date, end_date))
        records = cursor.fetchall()

        if records:
            pegawai_nip = records[0]['pegawai_nip']
            pegawai_nama = records[0]['pegawai_nama']
        else:
            pegawai_nip = None
            pegawai_nama = None

        month_name = datetime.strptime(str(month_number), "%m").strftime('%B')
        generate_time = datetime.now().strftime('%d/%m/%Y %H:%M')


        total_hadir = total_ct = total_d = total_tl = total_tp = total_a = 0
        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            tgl_shift = record['tgl_shift']
            record['tgl_shift'] = tgl_shift.strftime('%d')

            jk_bcin_time = None
            jk_ecout_time = None

            print(f"Record Pegawai: {record['pegawai_id']}, Tgl Shift: {tgl_shift}, Scan In: {scan_in}, Scan Out: {scan_out}")


            if scan_in and scan_out and scan_in != "0000-00-00 00:00:00" and scan_out != "0000-00-00 00:00:00":
                duration = scan_out - scan_in
                hours, remainder = divmod(duration.total_seconds(), 3600)
                minutes = remainder // 60
                record['work_duration'] = f"{int(hours)}:{int(minutes):02d}"
            else:
                record['work_duration'] = "-"

            if jk_bcin and jk_ecout:
                base_date = scan_in.date() if scan_in else tgl_shift

                if isinstance(jk_bcin, timedelta):
                    jk_bcin_time = (datetime.min + jk_bcin).time()
                else:
                    jk_bcin_time = jk_bcin
                
                if isinstance(jk_ecout, timedelta):
                    jk_ecout_time = (datetime.min + jk_ecout).time()
                else:
                    jk_ecout_time = jk_ecout

                if scan_in:
                    jk_bcin_time = datetime.combine(scan_in.date(), jk_bcin_time) if jk_bcin_time else None
                    jk_ecout_time = datetime.combine(scan_in.date(), jk_ecout_time) if jk_ecout_time else None

            is_weekend = tgl_shift.weekday() in [5, 6]


            record['tp'] = '-'
            record['tl'] = '-'

            if record['pegawai_id'] == 180:
                if is_weekend:
                    record['keterangan'] = "LB"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
                else:
                    record['keterangan'] = "H"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
            elif jk_id in [5, 6, 7, 10, 9, 8, 11, 29, 30, 31]:
                keterangan_map = {
                    5: "LB", 6: "LB", 7: "LB",
                    10: "LB",
                    9: "D",
                    8: "CT",
                    11: "S",
                    29: "LB",
                    30: "LDL",
                    31: "LD"
                }
                record['keterangan'] = keterangan_map.get(jk_id, "A")
            else:

                if scan_in and (scan_out is None or scan_out == "0000-00-00 00:00:00"):
                    record['tp'] = "TP"
                    record['keterangan'] = "H" 
                    print(f"TP condition met for {record['pegawai_id']} on {tgl_shift}")
                elif scan_out and (scan_in is None or scan_in == "0000-00-00 00:00:00"):
                    record['tl'] = "TL" 
                    record['keterangan'] = "H" 
                    print(f"TL condition met for {record['pegawai_id']} on {tgl_shift}")
                elif scan_in and scan_out:
                    if jk_bcin_time and jk_ecout_time:
                        if scan_in.time() >= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() >= jk_ecout_time.time():
                            record['tl'] = "TL"
                            record['keterangan'] = "H"
                            total_tl += 1
                        elif scan_in.time() <= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() >= jk_ecout_time.time():
                            record['keterangan'] = "H"
                        elif scan_in.time() >= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() < jk_ecout_time.time():
                            record['tp'] = "TP"
                            record['keterangan'] = "H"  
                            total_tp += 1
                        elif scan_in.time() <= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() < jk_ecout_time.time():
                            record['tp'] = "TP"
                            record['keterangan'] = "H"
                            total_tp += 1
                        else:
                            record['keterangan'] = "A"
                    else:
                        record['keterangan'] = "A"
                else:
                    record['keterangan'] = "A"

            if record['keterangan'] in ["LB", "CT", "LDL", "LD", "S"]:
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
            else:
                if scan_in:
                    record['scan_in'] = scan_in.strftime('%H:%M')
                if scan_out:
                    record['scan_out'] = scan_out.strftime('%H:%M')
                if jk_bcin_time:
                    record['jk_bcin'] = jk_bcin_time.strftime('%H:%M')
                if jk_ecout_time:
                    record['jk_ecout'] = jk_ecout_time.strftime('%H:%M')


            if record['keterangan'] == 'H':
                total_hadir += 1
            elif record['keterangan'] == 'CT':
                total_ct += 1
            elif record['keterangan'] == 'D':
                total_d += 1
            elif record['keterangan'] == 'A':
                total_a += 1

        html_output = render_template(
            'search_month_result.html',
            records=records,
            search_name=search_name,
            nip=pegawai_nip,
            month=month_name,
            year=year,
            generate_time=generate_time,
            total_hadir=total_hadir,
            total_ct=total_ct,
            total_d=total_d,
            total_tl=total_tl,
            total_tp=total_tp,
            total_a=total_a
        )

        pdf_file_path = f'uploads/{search_name}_report.pdf'
        

        with open(pdf_file_path, 'wb') as pdf_file:
            pisa_status = pisa.CreatePDF(html_output, dest=pdf_file)

        if pisa_status.err:
            return redirect(url_for('search_month'))

        return send_file(pdf_file_path, as_attachment=True)

    except Exception as e:
        return redirect(url_for('search_month'))

    finally:

        if connection:
            connection.close()

@app.route('/export_pdf', methods=['GET']) 
def export_pdf():
    import pdfkit
    from flask import make_response
    from datetime import datetime
    from dateutil.relativedelta import relativedelta
    import calendar

    config = pdfkit.configuration(wkhtmltopdf=os.environ.get('WKHTMLTOPDF_PATH') or shutil.which('wkhtmltopdf') or r'C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe')

    search_name = request.args.get('search_name')
    month = request.args.get('month')  
    
    if not search_name or not month:
        return redirect(url_for('search_month'))

    try:
        
        month_name, year = month.split('-') 

        month_number = list(calendar.month_name).index(month_name)  

        start_date = f"{year}-{month_number:02d}-01"  
        end_date = (datetime.strptime(start_date, "%Y-%m-%d") + relativedelta(months=1) - timedelta(days=1)).strftime('%Y-%m-%d')

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, sr.scan_in, sr.scan_out, 
                   jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            WHERE (p.pegawai_nama LIKE %s OR p.pegawai_nip LIKE %s)
            AND sr.tgl_shift BETWEEN %s AND %s
        """

        search_string = f"%{search_name}%"
        cursor.execute(query, (search_string, search_string, start_date, end_date))
        records = cursor.fetchall()

        if records:
            pegawai_nip = records[0]['pegawai_nip']
            pegawai_nama = records[0]['pegawai_nama']
        else:
            pegawai_nip = None
            pegawai_nama = None

        month_name = datetime.strptime(str(month_number), "%m").strftime('%B')
        generate_time = datetime.now().strftime('%d/%m/%Y %H:%M')

        total_hadir = total_ct = total_d = total_tl = total_tp = total_a = 0
        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            tgl_shift = record['tgl_shift']
            record['tgl_shift'] = tgl_shift.strftime('%d')

            jk_bcin_time = None
            jk_ecout_time = None

            if scan_in and scan_out and scan_in != "0000-00-00 00:00:00" and scan_out != "0000-00-00 00:00:00":
                duration = scan_out - scan_in
                hours, remainder = divmod(duration.total_seconds(), 3600)
                minutes = remainder // 60
                record['work_duration'] = f"{int(hours)}:{int(minutes):02d}"
            else:
                record['work_duration'] = "-"

            if jk_bcin and jk_ecout:
                base_date = scan_in.date() if scan_in else tgl_shift

                if isinstance(jk_bcin, timedelta):
                    jk_bcin_time = (datetime.min + jk_bcin).time()
                else:
                    jk_bcin_time = jk_bcin
                
                if isinstance(jk_ecout, timedelta):
                    jk_ecout_time = (datetime.min + jk_ecout).time()
                else:
                    jk_ecout_time = jk_ecout

                if scan_in:
                    jk_bcin_time = datetime.combine(scan_in.date(), jk_bcin_time) if jk_bcin_time else None
                    jk_ecout_time = datetime.combine(scan_in.date(), jk_ecout_time) if jk_ecout_time else None

            is_weekend = tgl_shift.weekday() in [5, 6]

            record['tp'] = '-'
            record['tl'] = '-'

            if record['pegawai_id'] == 180:
                if is_weekend:
                    record['keterangan'] = "LB"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
                else:
                    record['keterangan'] = "H"
                    record['scan_in'] = "-"
                    record['scan_out'] = "-"
                    record['jk_bcin'] = "-"
                    record['jk_ecout'] = "-"
            elif jk_id in [5, 6, 7, 10, 9, 8, 11, 29, 30, 31]:
                keterangan_map = {
                    5: "LB", 6: "LB", 7: "LB",
                    10: "LB",
                    9: "D",
                    8: "CT",
                    11: "S",
                    29: "LB",
                    30: "LDL",
                    31: "LD"
                }
                record['keterangan'] = keterangan_map.get(jk_id, "A")
            else:

                if scan_in and (scan_out is None or scan_out == "0000-00-00 00:00:00"):
                    record['tp'] = "TP"  
                    record['keterangan'] = "H" 
                elif scan_out and (scan_in is None or scan_in == "0000-00-00 00:00:00"):
                    record['tl'] = "TL" 
                    record['keterangan'] = "H" 
                elif scan_in and scan_out:
                    if jk_bcin_time and jk_ecout_time:
                        if scan_in.time() >= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() >= jk_ecout_time.time():
                            record['tl'] = "TL"
                            record['keterangan'] = "H" 
                            total_tl += 1
                        elif scan_in.time() <= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() >= jk_ecout_time.time():
                            record['keterangan'] = "H"
                        elif scan_in.time() >= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() < jk_ecout_time.time():
                            record['tp'] = "TP"
                            record['keterangan'] = "H"
                            total_tp += 1
                        elif scan_in.time() <= (jk_bcin_time + timedelta(minutes=1)).time() and scan_out.time() < jk_ecout_time.time():
                            record['tp'] = "TP"
                            record['keterangan'] = "H"
                            total_tp += 1
                        else:
                            record['keterangan'] = "A"
                    else:
                        record['keterangan'] = "A"
                else:
                    record['keterangan'] = "A"

            if record['keterangan'] in ["LB", "CT", "LDL", "LD", "S"]:
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
            else:
                if scan_in:
                    record['scan_in'] = scan_in.strftime('%H:%M')
                if scan_out:
                    record['scan_out'] = scan_out.strftime('%H:%M')
                if jk_bcin_time:
                    record['jk_bcin'] = jk_bcin_time.strftime('%H:%M')
                if jk_ecout_time:
                    record['jk_ecout'] = jk_ecout_time.strftime('%H:%M')

            if record['keterangan'] == 'H':
                total_hadir += 1
            elif record['keterangan'] == 'CT':
                total_ct += 1
            elif record['keterangan'] == 'D':
                total_d += 1
            elif record['keterangan'] == 'A':
                total_a += 1

        
        rendered_html = render_template(
            'search_month_result.html', 
            records=records, 
            search_name=pegawai_nama,
            nip=pegawai_nip,
            month=month_name,
            year=year,
            generate_time=generate_time,
            total_hadir=total_hadir,
            total_ct=total_ct,
            total_d=total_d,
            total_tl=total_tl,
            total_tp=total_tp,
            total_a=total_a
        )

        pdf = pdfkit.from_string(rendered_html, False, configuration=config)

        response = make_response(pdf)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename={search_name}_{month_name}_{year}.pdf'

        return response

    except Exception as e:
        return redirect(url_for('search_month'))
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/search_name_autocomplete')
def search_name_autocomplete():
    search_term = request.args.get('term', '')

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = "SELECT pegawai_nama FROM pegawai WHERE pegawai_nama LIKE %s LIMIT 10"
        search_term = f"%{search_term}%"
        cursor.execute(query, (search_term,))
        results = cursor.fetchall()

        name_list = [row['pegawai_nama'] for row in results]

        return jsonify(name_list)
    except Error as e:
        return jsonify([])
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/lembur', methods=['GET', 'POST'])
def lembur():
    try:
        file_path = session.get('uploaded_file_path', 'lembur.xlsx')
        df = pd.read_excel(file_path, header=1, skiprows=0)

        expected_columns = ['NO', 'TIM', 'Hari dan Tanggal', 'Pekerjaan', 'Nama Personil',
                            'Jumlah Jam Lembur', 'Jam Mulai', 'Jam Akhir']
        if not all(col in df.columns for col in expected_columns):
            return "Struktur Excel tidak sesuai dengan format yang diharapkan. Pastikan header kolom adalah: " + ", ".join(expected_columns)


        df['Nama Personil'] = df['Nama Personil'].fillna('').str.strip()
        df['Hari dan Tanggal'] = pd.to_datetime(df['Hari dan Tanggal'], format='%A, %d %B %Y', errors='coerce').dt.strftime('%Y-%m-%d')
        df['Jam Mulai'] = df['Jam Mulai'].astype(str).str.strip()
        df['Jam Akhir'] = df['Jam Akhir'].astype(str).str.strip()
        df['Jumlah Jam Lembur'] = df['Jumlah Jam Lembur'].fillna(0).astype(int)

        def calculate_overtime(row):
            try:
                start_str = str(row['Jam Mulai']).replace('.', ':').strip()
                end_str = str(row['Jam Akhir']).replace('.', ':').strip()

             
                if len(start_str.split(':')) == 3:
                    start_str = ':'.join(start_str.split(':')[:2])
                if len(end_str.split(':')) == 3:
                    end_str = ':'.join(end_str.split(':')[:2])

                start = pd.to_datetime(start_str, format='%H:%M', errors='coerce')
                end = pd.to_datetime(end_str, format='%H:%M', errors='coerce')

                if pd.isna(start) or pd.isna(end):
                    return 0.0, 0

                total = end - start
                total_hours_decimal = total.total_seconds() / 3600

                
                break_start = start.replace(hour=12, minute=0)
                break_end = start.replace(hour=13, minute=0)

                overlap_start = max(start, break_start)
                overlap_end = min(end, break_end)
                overlap_minutes = (overlap_end - overlap_start).total_seconds() / 60

                if overlap_minutes >= 60:
                    total_hours_decimal -= 1

                total_hours_rounded = int(total_hours_decimal) if total_hours_decimal > 0 else 0
                return total_hours_decimal, total_hours_rounded

            except Exception as e:
          
                return 0.0, 0

        df[['Hitungan Aplikasi (berkoma)', 'Hitungan Aplikasi (dibulatkan)']] = df.apply(
            lambda row: pd.Series(calculate_overtime(row)), axis=1
        )

        display_mode = request.form.get('display_mode', 'all')
        if display_mode == 'highlighted':
            df = df[df['Hitungan Aplikasi (dibulatkan)'] != df['Jumlah Jam Lembur']]

        selected_columns = [
            'NO', 'TIM', 'Hari dan Tanggal', 'Pekerjaan', 'Nama Personil',
            'Jumlah Jam Lembur', 'Jam Mulai', 'Jam Akhir',
            'Hitungan Aplikasi (berkoma)', 'Hitungan Aplikasi (dibulatkan)'
        ]
        df_selected = df[selected_columns]
        records = df_selected.to_dict(orient='records')

        session['filtered_lembur_data'] = records

        return render_template('lembur.html', records=records, display_mode=display_mode)

    except Exception as e:
        return f"Terjadi kesalahan saat memuat data: {e}"


@app.route('/lembur/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return "Tidak ada file yang diunggah"

        file = request.files['file']
        if file.filename == '':
            return "Nama file kosong"

        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        file.save(file_path)

      
        session['uploaded_file_path'] = file_path

        return redirect(url_for('lembur'))

    except Exception as e:
        return f"Terjadi kesalahan saat memuat file: {e}"

@app.route('/lembur/export', methods=['POST'])
def export_lembur_to_excel():


    if 'loggedin' not in session or session.get('role') != 'admin':
        flash('Please login to access this page.', 'warning')
        return redirect(url_for('login'))


    try:
  
        file_path = session.get('uploaded_file_path')

        if not file_path or not os.path.exists(file_path):
            return "Tidak ada data untuk di-export."

 
        df = pd.read_excel(file_path)

      
        if "Nama" not in df.columns:
            df["Nama"] = df.get("Nama Personil HDTI", "").fillna('') + df.get("Nama Personil HDBS", "").fillna('')
            df["Nama"] = df["Nama"].str.strip()

      
        def calculate_overtime(start, end):
            try:
                if pd.notnull(start) and pd.notnull(end):
                    start_time = pd.to_datetime(start, errors='coerce')
                    end_time = pd.to_datetime(end, errors='coerce')
                    if pd.notnull(start_time) and pd.notnull(end_time):
                        total = end_time - start_time
                        if total.total_seconds() > 0:
                            total_hours = total.total_seconds() // 3600
                            noon_start = start_time.replace(hour=12, minute=0, second=0)
                            noon_end = start_time.replace(hour=13, minute=0, second=0)
                            if start_time <= noon_end and end_time >= noon_start:
                                total_hours -= 1
                            return max(int(total_hours), 0)
                return 0
            except Exception:
                return 0

       
        df["Total Lembur"] = df.apply(
            lambda row: calculate_overtime(row.get("Waktu Mulai Lembur :", None), row.get("Waktu Selesai Lembur :", None)),
            axis=1
        )

      
        df['Tanggal Lembur :'] = pd.to_datetime(df['Tanggal Lembur :'], errors='coerce')
        df['Tanggal Lembur :'] = df['Tanggal Lembur :'].dt.strftime('%A, %d %B %Y')

   
        selected_columns = [
            "Nama", "Tanggal Lembur :", 
            "Deskripsi Pekerjaan Lembur (Max: 4 Baris)", "Waktu Mulai Lembur :", "Waktu Selesai Lembur :", "Total Lembur"
        ]
        

        df_selected = df[[col for col in selected_columns if col in df.columns]]

 
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df_selected.to_excel(writer, index=False, sheet_name='Lembur')
            worksheet = writer.sheets['Lembur']
            worksheet.set_column('A:A', 30)
            worksheet.set_column('B:B', 30)
            worksheet.set_column('C:C', 50)
            worksheet.set_column('D:E', 20)
            worksheet.set_column('F:F', 15)

        output.seek(0)

     
        return send_file(
            output, 
            as_attachment=True, 
            download_name='Lembur_Report.xlsx', 
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )

    except Exception as e:
        return f"Terjadi kesalahan saat export data: {e}"


@app.route('/upload-lembur', methods=['GET'])
def upload_page():


    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    return render_template('upload-lembur.html')

import pickle

@app.route('/ito_load', methods=['GET', 'POST'])
def ito_load():
    # route ini untuk halaman import data absensi dari web ITO (ito-attendance.lintasarta.co.id)
    # admin upload file excel export dari ITO, lalu preview data sebelum simpan
    # data difilter berdasarkan divisi, dan hanya status 'Late' atau 'Leave Early' yang ditampilkan

    # cek dulu user sudah login sebagai admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    # ambil daftar semua divisi untuk dropdown filter
    divisions = []
    try:
        conn_div_list = get_db_connection()
        cursor_div_list = conn_div_list.cursor(dictionary=True)
        cursor_div_list.execute("SELECT DISTINCT pembagian1_nama FROM pembagian1 WHERE pembagian1_nama IS NOT NULL AND pembagian1_nama != '' ORDER BY pembagian1_nama")
        divisions = [row['pembagian1_nama'] for row in cursor_div_list.fetchall()]
        cursor_div_list.close()
        conn_div_list.close()
    except Exception:
        # kalau gagal ambil divisi, lanjut saja dengan list kosong
        pass
    
    if request.method == 'POST':
        # cek ada file yang diupload
        if 'file' not in request.files:
            return redirect(request.url)
        
        file = request.files['file']
        # kalau tidak pilih file, kasih pesan dan balik
        if file.filename == '':
            flash('No file selected.', 'warning')
            return redirect(request.url)

        # pastikan file excel
        if file and file.filename.endswith('.xlsx'):
            # simpan file ke folder uploads
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filepath)

            try:
                # baca excel mulai baris ke-7 (header=6), paksa NIK jadi string supaya leading zero aman
                df = pd.read_excel(filepath, header=6, dtype={'NIK': str})
                
                # ambil mapping nik → divisi dari database
                # ini untuk tambah kolom Divisi di dataframe
                try:
                    conn_div = get_db_connection()
                    cursor_div = conn_div.cursor(dictionary=True)
                    cursor_div.execute("SELECT pegawai_nik_lintas, pembagian1_nama FROM pegawai JOIN pembagian1 ON pegawai.pembagian1_id = pembagian1.pembagian1_id")
                    division_mapping = {row['pegawai_nik_lintas']: row['pembagian1_nama'] for row in cursor_div.fetchall()}
                    cursor_div.close()
                    conn_div.close()
                except Exception:
                    # kalau gagal, pakai mapping kosong
                    division_mapping = {}
                
                # bersihkan nama kolom dari spasi ekstra
                df.columns = df.columns.str.strip()

                # kolom yang diharapkan dari export ITO
                expected_columns = [
                    "Name", "NIK", "Date", "Time In", "Time Out", 
                    "Schedule In", "Schedule Out", "Total Time", 
                    "Longitude In", "Latitude In", "Longitude Out", "Latitude Out", "Status"
                ]

                # kalau kolom kurang, balik ke halaman
                if len(df.columns) < len(expected_columns):
                    return redirect(request.url)

                # pakai nama kolom standar (potong kalau lebih panjang)
                df.columns = expected_columns[:len(df.columns)]

                # buang baris yang benar-benar kosong
                df = df.dropna(how='all')
                
                # isi nilai kosong dengan tanda '-'
                for col in df.columns:
                    df[col] = df[col].fillna('-')
                
                # fungsi khusus untuk konversi NIK jadi 8 digit dengan leading zero
                def convert_nik_debug(x):
                    original = x
                    try:
                        # kalau kosong atau invalid, default '0000'
                        if pd.isna(x) or str(x).strip() in ['', '-', 'nan', 'NaN']:
                            return '0000'
                        
                        x_str = str(x).strip()
                        
                        # kalau ada notasi ilmiah (e+ atau e-)
                        if 'e+' in x_str.lower() or 'e-' in x_str.lower():
                            x_float = float(x_str)
                            return f"{int(x_float):08d}"
                        
                        # kalau ada desimal, ambil integer
                        if '.' in x_str:
                            x_int = int(float(x_str))
                            return f"{x_int:08d}"
                        
                        # pad dengan nol di depan sampai 8 digit
                        return x_str.zfill(8)
                        
                    except:
                        # kalau error, pakai original dengan pad nol
                        return str(original).zfill(8) if original is not None else '0000'

                # terapkan konversi NIK
                df['NIK'] = df['NIK'].apply(convert_nik_debug)
                
                # filter hanya status yang relevan: Late atau Leave Early
                valid_statuses = ['Late', 'Leave Early']
                df = df[df['Status'].str.strip().isin(valid_statuses)]

                # tambah kolom Divisi dari mapping nik
                df['Divisi'] = df['NIK'].map(division_mapping).fillna('-')
                
                # bersihkan spasi di nama sebelum urut
                df['Name'] = df['Name'].str.strip()
                
                # urutkan berdasarkan nama pegawai
                df.sort_values(by='Name', inplace=True)
                
                # kalau setelah filter kosong, tampilkan halaman tanpa data
                if df.empty:
                    return render_template('ito.html', records=None)
                
                # simpan dataframe ke file pickle supaya bisa dipakai di update_ito
                pkl_filename = f"uploaded_{datetime.now().strftime('%Y%m%d%H%M%S')}.pkl"
                pkl_filepath = os.path.join(app.config['UPLOAD_FOLDER'], pkl_filename)
                with open(pkl_filepath, 'wb') as f:
                    pickle.dump(df, f)

                # simpan path pickle ke session
                session['uploaded_file_path'] = pkl_filepath
                
                # ubah ke list dict untuk template
                records = df.to_dict(orient='records')
                
                # tampilkan preview data
                return render_template('ito.html', records=records, divisions=divisions)

            except Exception as e:
                # kalau error saat proses excel, balik ke halaman
                return redirect(request.url)
        else:
            return redirect(request.url)

    # kalau GET, tampilkan halaman upload dengan dropdown divisi
    return render_template('ito.html', divisions=divisions)


from datetime import datetime

@app.route('/update_ito', methods=['POST'])
def update_ito():
    # route ini dipanggil setelah admin pilih record dari preview ITO
    # tugasnya: simpan scan in/out dari data ITO ke tabel shift_result_new
    # hanya overwrite kalau nilai lama '0000-00-00 00:00:00'

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    # ambil list record yang dipilih (format: nik|date)
    selected_records = request.form.getlist('selected_records')

    # kalau tidak ada yang dipilih, balik ke halaman ITO
    if not selected_records:
        return redirect(url_for('ito_load'))

    try:
        # cek ada file pickle dari preview sebelumnya
        if 'uploaded_file_path' not in session:
            return redirect(url_for('ito_load'))

        pkl_filepath = session['uploaded_file_path']

        # baca dataframe dari pickle
        with open(pkl_filepath, 'rb') as f:
            df = pickle.load(f)

        # buka koneksi db
        connection = get_db_connection()
        cursor = connection.cursor()

        # proses tiap record yang dipilih
        for record in selected_records:
            nik, date = record.split('|')

            # cari baris yang cocok di dataframe
            row = df[(df['NIK'] == nik) & (df['Date'] == date)]
            if row.empty:
                continue

            # ambil waktu in dan out
            time_in = row['Time In'].values[0]
            time_out = row['Time Out'].values[0]

            # kalau '-' ganti jadi 00:00:00
            time_in = '00:00:00' if time_in == '-' else time_in
            time_out = '00:00:00' if time_out == '-' else time_out

            try:
                # ubah date dari dd/mm/yyyy jadi yyyy-mm-dd
                tgl_shift = datetime.strptime(date, '%d/%m/%Y').strftime('%Y-%m-%d')

                # gabung tanggal + waktu jadi string datetime
                scan_in = f"{tgl_shift} {time_in}" if time_in != '00:00:00' else '0000-00-00 00:00:00'
                scan_out = f"{tgl_shift} {time_out}" if time_out != '00:00:00' else '0000-00-00 00:00:00'

                # ubah jadi objek datetime kalau bukan default nol
                scan_in = datetime.strptime(scan_in, '%Y-%m-%d %H:%M:%S') if scan_in != '0000-00-00 00:00:00' else scan_in
                scan_out = datetime.strptime(scan_out, '%Y-%m-%d %H:%M:%S') if scan_out != '0000-00-00 00:00:00' else scan_out

            except Exception as e:
                # kalau error format tanggal/waktu, skip record ini
                continue

            # cari pegawai_id berdasarkan 4 digit akhir nik (pegawai_nip)
            pegawai_id_query = "SELECT pegawai_id FROM pegawai WHERE RIGHT(pegawai_nip, 4) = %s"
            nik_suffix = nik[-4:]
            cursor.execute(pegawai_id_query, (nik_suffix,))
            pegawai_id = cursor.fetchone()

            if not pegawai_id:
                # nip tidak ketemu, skip
                continue

            # query upsert: insert kalau belum ada
            # update scan_in/out hanya kalau nilai lama adalah default nol
            upsert_query = """
                INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out) 
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    scan_in = CASE 
                        WHEN scan_in = '0000-00-00 00:00:00' THEN VALUES(scan_in) 
                        ELSE scan_in 
                    END,
                    scan_out = CASE 
                        WHEN scan_out = '0000-00-00 00:00:00' THEN VALUES(scan_out) 
                        ELSE scan_out 
                    END
            """

            cursor.execute(upsert_query, (pegawai_id[0], tgl_shift, scan_in, scan_out))

        # commit semua perubahan
        connection.commit()

    except Exception as e:
        # kalau error, skip saja (tidak crash)
        pass

    finally:
        # tutup koneksi db
        if connection.is_connected():
            cursor.close()
            connection.close()

        # hapus file pickle sementara setelah proses selesai
        if 'uploaded_file_path' in session:
            if os.path.exists(session['uploaded_file_path']):
                os.remove(session['uploaded_file_path'])
            session.pop('uploaded_file_path', None)

    # kembali ke halaman ITO
    return redirect(url_for('ito_load'))


@app.route('/cek-absen', methods=['GET', 'POST'])
def cek_absen():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        
       
        cursor.execute("SELECT DISTINCT nama_pegawai FROM absensi ORDER BY nama_pegawai ASC")
        nama_pegawai_list = [row['nama_pegawai'] for row in cursor.fetchall()]
        
        data = None
        
        if request.method == 'POST':
            tanggal_awal = request.form.get('tanggal_awal')
            tanggal_akhir = request.form.get('tanggal_akhir')
            nama_pegawai = request.form.get('nama_pegawai')
            
            query = "SELECT * FROM absensi WHERE 1=1"
            params = []
            
            if tanggal_awal:
                query += " AND tanggal_absen >= %s"
                params.append(tanggal_awal)
            
            if tanggal_akhir:
                query += " AND tanggal_absen <= %s"
                params.append(tanggal_akhir)
            
            if nama_pegawai:
                query += " AND nama_pegawai = %s"
                params.append(nama_pegawai)
            
            query += " ORDER BY tanggal_absen DESC"
            cursor.execute(query, params)
            data = cursor.fetchall()
        
        cursor.close()
        conn.close()
        
        return render_template("cek-absen.html", data=data, nama_pegawai_list=nama_pegawai_list)
    except Exception as e:
        return render_template("cek-absen.html", data=None, nama_pegawai_list=[])



@app.route('/upload-cek-absen', methods=['GET', 'POST'])
def upload_cek_absen():
    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)

        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)

        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        file.save(filepath)


        try:
          
            excel_file = pd.ExcelFile(filepath)
            conn = get_db_connection()
            cursor = conn.cursor()

            for sheet_name in excel_file.sheet_names:
                try:
                    try:
                        tanggal_absen = datetime.strptime(sheet_name, "%Y-%m-%d").date()
                    except ValueError:
                        tanggal_absen = datetime.strptime(sheet_name, "%d %B %Y").date()
                except ValueError:
                    continue

       
                try:
                    df = pd.read_excel(filepath, sheet_name=sheet_name, skiprows=3)
                except Exception as e:
                    continue

                df = df.iloc[:, :10]
                df.columns = [
                    "no", "nama_pegawai", "masuk", "pulang",
                    "masuk_real", "pulang_real", "tl", "tp",
                    "jumlah_jam_kerja", "keterangan"
                ]
                df = df.drop(columns=["no"], errors="ignore")
                df["tanggal_absen"] = tanggal_absen
                df = df.infer_objects(copy=False).fillna('-')

                for _, row in df.iterrows():
                    sql = """
                    INSERT INTO absensi (nama_pegawai, masuk, pulang, masuk_real, pulang_real, tl, tp, jumlah_jam_kerja, keterangan, tanggal_absen)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """
                    cursor.execute(sql, (
                        row["nama_pegawai"], row["masuk"], row["pulang"], row["masuk_real"], row["pulang_real"],
                        row["tl"], row["tp"], row["jumlah_jam_kerja"], row["keterangan"], row["tanggal_absen"]
                    ))


            conn.commit()
            cursor.close()
            conn.close()

            return redirect(url_for('upload_cek_absen'))

        except Exception as e:
            return redirect(request.url)

    return render_template('upload-cek-absen.html')

@app.route('/beranda')
def beranda():
    # halaman ini adalah dashboard utama buat pegawai
    # tugasnya: tampilkan rekap absensi pribadi dalam rentang tanggal tertentu
    # defaultnya minggu berjalan (senin-minggu), tapi minimum tanggal 6 Mei 2025
    # pegawai hanya bisa lihat data miliknya sendiri (berdasarkan nip dari session)

    # cek dulu user sudah login dan role-nya pegawai
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))

    # ambil nip dari session (harus ada, kalau enggak balik ke login)
    nip = session.get('nip')
    if not nip:
        return redirect(url_for('login'))

    # ambil parameter filter dari url
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    search = request.args.get('search_name')
    pembagian1_nama = request.args.get('pembagian1_nama')
    keterangan = request.args.get('keterangan')

    # batas minimum tanggal yang boleh dilihat (6 Mei 2025)
    MIN_DATE = date(2025, 5, 6)

    # kalau tidak ada filter tanggal, pakai minggu berjalan
    if not start_date or not end_date:
        today = date.today()
        # hitung senin minggu ini
        start_of_week = today - timedelta(days=today.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        # pastikan tidak lebih kecil dari MIN_DATE
        start_date = max(start_of_week, MIN_DATE).strftime('%Y-%m-%d')
        end_date = end_of_week.strftime('%Y-%m-%d')
    else:
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            # kalau tanggal mulai lebih kecil dari MIN_DATE, paksa ke MIN_DATE
            if start_date_obj < MIN_DATE:
                start_date = MIN_DATE.strftime('%Y-%m-%d')
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
            # kalau end lebih kecil dari start, paksa sama dengan start
            if end_date_obj < start_date_obj:
                end_date = start_date
        except ValueError:
            # kalau format tanggal salah, fallback ke MIN_DATE sampai hari ini
            today = date.today()
            start_date = MIN_DATE.strftime('%Y-%m-%d')
            end_date = today.strftime('%Y-%m-%d')

    try:
        # buka koneksi db
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # ambil info pegawai yang login (nama, divisi, jabatan)
        cursor.execute("""
            SELECT 
                p.pegawai_nama,
                pb1.pembagian1_nama AS divisi,
                pb2.pembagian2_nama AS jabatan
            FROM pegawai p
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            LEFT JOIN pembagian2 pb2 ON p.pembagian2_id = pb2.pembagian2_id
            WHERE p.pegawai_nip = %s
        """, (nip,))
        pegawai = cursor.fetchone()

        # query utama: ambil semua data shift pegawai ini dalam rentang tanggal
        # batasi minimum ke 2025-05-06 supaya tidak ambil data lama
        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, 
                   sr.scan_in, sr.scan_out, jk.jk_id, jk.jk_bcin, jk.jk_ecout, 
                   jk.jk_name, pb1.pembagian1_nama
            FROM shift_result_new sr
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE sr.tgl_shift BETWEEN GREATEST(%s, '2025-05-06') AND %s
              AND p.pegawai_status != 0
              AND p.pegawai_id != 1
              AND p.pegawai_nip = %s
        """
        params = [start_date, end_date, nip]

        # kalau ada pencarian nama atau nip (meski jarang dipakai di beranda pegawai)
        if search:
            search_string = f'%{search}%'
            query += ' AND (p.pegawai_nama LIKE %s OR p.pegawai_nip LIKE %s)'
            params += [search_string, search_string]

        # kalau ada filter divisi (meski biasanya kosong di beranda pegawai)
        if pembagian1_nama:
            pembagian1_nama_string = f'%{pembagian1_nama}%'
            query += ' AND pb1.pembagian1_nama LIKE %s'
            params.append(pembagian1_nama_string)

        cursor.execute(query, params)
        records = cursor.fetchall()

        # proses tiap record untuk tentukan keterangan dan format tampilan
        for record in records:
            scan_in = record['scan_in']
            scan_out = record['scan_out']
            jk_bcin = record['jk_bcin']
            jk_ecout = record['jk_ecout']
            jk_id = record['jk_id']
            tgl_shift = record['tgl_shift']

            # format tanggal jadi Senin, 06 Mei 2025 (title case)
            record['tgl_formatted'] = tgl_shift.strftime('%a, %d %b %Y').title()

            jk_bcin_time = None
            jk_ecout_time = None

            if jk_bcin and jk_ecout:
                # ubah durasi timedelta ke time object
                jk_bcin_time = (datetime.min + jk_bcin).time()
                jk_ecout_time = (datetime.min + jk_ecout).time()

            # kalau shift cross-day (5,6,7), pakai tanggal scan_in untuk base
            if jk_id in [5, 6, 7]:
                if scan_in:
                    jk_bcin_time = datetime.combine(scan_in.date(), jk_bcin_time)
                if scan_in and jk_ecout:
                    jk_ecout_time = datetime.combine(scan_in.date(), jk_ecout_time)
            else:
                if jk_bcin and jk_ecout:
                    jk_bcin_time = datetime.min + jk_bcin
                    jk_ecout_time = datetime.min + jk_ecout

            is_weekend = tgl_shift.weekday() in [5, 6]

            # penanganan khusus pegawai PM (id 180) – meski di beranda pegawai biasanya tidak muncul
            if record['pegawai_id'] == 180:
                record['keterangan'] = "Libur" if is_weekend else "H"
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
                record['jk_name'] = "-"
            # shift khusus langsung kasih keterangan tanpa cek scan
            elif jk_id == 10:
                if scan_in and scan_out:
                    record['keterangan'] = "OT"
                else:
                    record['keterangan'] = "Libur"
            elif jk_id == 9:
                record['keterangan'] = "D"
            elif jk_id == 8:
                record['keterangan'] = "CT"
            elif jk_id == 11:
                record['keterangan'] = "S"
            elif jk_id == 29:
                record['keterangan'] = "LB"
            elif jk_id == 30:
                record['keterangan'] = "LDL"
            elif jk_id == 31:
                record['keterangan'] = "LD"
            else:
                # logika normal: cek scan masuk/pulang vs jadwal
                if scan_in and not scan_out:
                    # masuk ada, pulang tidak → tidak penuh
                    record['keterangan'] = "TP"
                elif not scan_in and scan_out:
                    # masuk tidak, pulang ada → terlambat
                    record['keterangan'] = "TL"
                elif scan_in and scan_out and jk_bcin_time and jk_ecout_time:
                    scan_in_time = scan_in.time()
                    scan_out_time = scan_out.time()
                    bc_time_limit = (jk_bcin_time + timedelta(minutes=1)).time()
                    # kalau bulan Maret, pulang maju 30 menit
                    ec_time_limit = (jk_ecout_time - timedelta(minutes=30)).time() if tgl_shift.month == 3 else jk_ecout_time.time()

                    if scan_in_time >= bc_time_limit and scan_out_time >= ec_time_limit:
                        record['keterangan'] = "TL"
                    elif scan_in_time <= bc_time_limit and scan_out_time >= ec_time_limit:
                        record['keterangan'] = "H"
                    elif scan_in_time >= bc_time_limit and scan_out_time < ec_time_limit:
                        record['keterangan'] = "TP"
                    elif scan_in_time <= bc_time_limit and scan_out_time < ec_time_limit:
                        record['keterangan'] = "TP"
                    else:
                        record['keterangan'] = "Belum Diketahui"
                else:
                    record['keterangan'] = "Belum Diketahui"

            # kalau status libur/cuti/sakit, kosongkan kolom waktu
            if record['keterangan'] in ["LB", "CT", "LDL", "LD", "S", "Libur"]:
                record['scan_in'] = "-"
                record['scan_out'] = "-"
                record['jk_bcin'] = "-"
                record['jk_ecout'] = "-"
            else:
                # format waktu jadi HH:MM
                record['scan_in'] = scan_in.strftime('%H:%M') if scan_in else "-"
                record['scan_out'] = scan_out.strftime('%H:%M') if scan_out else "-"
                record['jk_bcin'] = jk_bcin_time.strftime('%H:%M') if jk_bcin_time else "-"
                record['jk_ecout'] = jk_ecout_time.strftime('%H:%M') if jk_ecout_time else "-"

        # kalau ada filter keterangan, hapus record yang tidak cocok
        if keterangan:
            records = [record for record in records if record['keterangan'] == keterangan]

        # urutkan berdasarkan tanggal shift
        records.sort(key=lambda x: x['tgl_shift'])

        # siapkan pesan kalau tidak ada data
        message = None
        if not records:
            if start_date or end_date or keterangan or search or pembagian1_nama:
                message = "Enggak ada data sesuai filter"
            else:
                message = "Plih filter buat nampilin data"

        # cek apakah mode preview (untuk testing atau template baru)
        preview = request.args.get('preview') == '1'
        template_name = 'beranda_new.html' if preview else 'beranda.html'

        # tampilkan halaman beranda dengan semua data
        return render_template(
            template_name,
            records=records,
            start_date=start_date,
            end_date=end_date,
            pegawai=pegawai,
            keterangan=keterangan,
            message=message
        )

    except Error as e:
        # kalau error db, tampilkan halaman dengan pesan error
        return render_template('beranda.html', records=[], pegawai=None, error="Failed to fetch data")

    finally:
        # pastikan tutup koneksi db
        if connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/akun-saya', methods=['GET', 'POST'])
def akun_saya():

    import re

    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))

    nip = session.get('nip')
    if not nip:
        return redirect(url_for('login'))

    error = None
    success = None

    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not old_password or not new_password or not confirm_password:
            error = "Semua kolom password harus diisi"
        elif new_password != confirm_password:
            error = "Password baru sama konfirmasi password enggak cocok"
        else:
          
            if len(new_password) < 6:
                error = "Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus"
            elif not re.search(r"[A-Z]", new_password):
                error = "Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus"
            elif not re.search(r"[0-9]", new_password):
                error = "Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus"
            elif not re.search(r"[!@#$%^&*()_+\-=\[\]{};:'\"\\|,.<>/?]", new_password):
                error = "Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus"
            else:
                try:
                    connection = get_db_connection()
                    cursor = connection.cursor(dictionary=True)

                
                    cursor.execute("SELECT pegawai_pwd FROM pegawai WHERE pegawai_nip = %s", (nip,))
                    user = cursor.fetchone()

                    if not user or old_password != user['pegawai_pwd']:
                        error = "Password lama salah"
                    else:
                     
                        cursor.execute("UPDATE pegawai SET pegawai_pwd = %s WHERE pegawai_nip = %s", (new_password, nip))
                        connection.commit()
                        success = "Password berhasil diubah"

                    cursor.close()
                    connection.close()

                except Error as e:
                    error = f"Gagal mengubah password: {e}"

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        
        cursor.execute("""
            SELECT 
                p.pegawai_nip,
                p.pegawai_nama,
                p.pegawai_nik_lintas,
                pb1.pembagian1_nama AS divisi,
                pb2.pembagian2_nama AS jabatan
            FROM pegawai p
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            LEFT JOIN pembagian2 pb2 ON p.pembagian2_id = pb2.pembagian2_id
            WHERE p.pegawai_nip = %s
        """, (nip,))
        pegawai = cursor.fetchone()

        if not pegawai:
            return render_template('akun.html', pegawai=None, error="Data pengguna enggak ditemukan", success=None)

        return render_template('akun.html', pegawai=pegawai, error=error, success=success)

    except Error as e:
        return render_template('akun.html', pegawai=None, error="Gagal ambil data profil", success=None)

    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()



def int_to_hhmm(seconds):
    """Convert integer seconds to HH:MM string."""
    if seconds is None or not isinstance(seconds, int):
        return '-'
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f'{hours:02d}:{minutes:02d}'

def get_absensi_data(cursor, nip, tanggal_lembur):
    """Get and process attendance data for a specific date."""
    cursor.execute("""
        SELECT sr.scan_in, sr.scan_out, jk.jk_bcin, jk.jk_ecout, jk.jk_name, 
               jk.jk_id, sr.pegawai_id, sr.tgl_shift
        FROM shift_result_new sr
        LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
        WHERE sr.pegawai_id = (SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s)
        AND sr.tgl_shift = %s
    """, (nip, tanggal_lembur))
    
    absensi = cursor.fetchone()
    if not absensi:
        return {
            'jk_name': '-', 'jk_bcin': '-', 'jk_ecout': '-',
            'scan_in': '-', 'scan_out': '-', 'keterangan': 'Belum Ada Data'
        }
    
    
    scan_in = absensi['scan_in']
    scan_out = absensi['scan_out']
    jk_bcin = absensi['jk_bcin']
    jk_ecout = absensi['jk_ecout']
    jk_id = absensi['jk_id']
    tgl_shift = absensi['tgl_shift']
    pegawai_id = absensi['pegawai_id']
    
    
    jk_bcin_seconds = int(jk_bcin.total_seconds()) if jk_bcin else None
    jk_ecout_seconds = int(jk_ecout.total_seconds()) if jk_ecout else None
    
    
    absensi['scan_in'] = scan_in.strftime('%H:%M') if scan_in else '-'
    absensi['scan_out'] = scan_out.strftime('%H:%M') if scan_out else '-'
    absensi['jk_bcin'] = int_to_hhmm(jk_bcin_seconds) if jk_bcin_seconds is not None else '-'
    absensi['jk_ecout'] = int_to_hhmm(jk_ecout_seconds) if jk_ecout_seconds is not None else '-'
    
    
    absensi['keterangan'] = calculate_keterangan(
        pegawai_id, jk_id, scan_in, scan_out, 
        jk_bcin_seconds, jk_ecout_seconds, tgl_shift
    )
    
    
    if absensi['keterangan'] in ['LB', 'CT', 'LDL', 'LD', 'S', 'Libur'] or pegawai_id == 180:
        if pegawai_id == 180:
            is_weekend = tgl_shift.weekday() in [5, 6]
            if is_weekend:
                absensi['scan_in'] = "-"
                absensi['scan_out'] = "-"
                absensi['jk_bcin'] = "-"
                absensi['jk_ecout'] = "-"
                absensi['jk_name'] = "-"
    
    return absensi

def calculate_keterangan(pegawai_id, jk_id, scan_in, scan_out, jk_bcin_seconds, jk_ecout_seconds, tgl_shift):
    """Calculate attendance status based on various conditions."""
    is_weekend = tgl_shift.weekday() in [5, 6]
    
    
    if pegawai_id == 180:
        return "Libur" if is_weekend else "H"
    
    
    status_map = {
        10: "OT" if scan_in and scan_out else "Libur",
        9: "D", 8: "CT", 11: "S",
        29: "LB", 30: "LDL", 31: "LD"
    }
    
    if jk_id in status_map:
        return status_map[jk_id]
    
    
    if scan_in and not scan_out:
        return "TP"
    elif not scan_in and scan_out:
        return "TL"
    elif not scan_in and not scan_out:
        return "A"
    elif scan_in and scan_out:
        if jk_bcin_seconds is None or jk_ecout_seconds is None:
            return "A"
        
        
        scan_in_seconds = (scan_in.hour * 3600 + scan_in.minute * 60 + scan_in.second)
        scan_out_seconds = (scan_out.hour * 3600 + scan_out.minute * 60 + scan_out.second)
        
        
        bc_time_limit_seconds = jk_bcin_seconds + 60
        
        ec_time_limit_seconds = (jk_ecout_seconds - 1800 
                               if tgl_shift.month == 3 
                               else jk_ecout_seconds)
        
        
        late_in = scan_in_seconds >= bc_time_limit_seconds
        early_out = scan_out_seconds < ec_time_limit_seconds
        
        if late_in and scan_out_seconds >= jk_ecout_seconds:
            return "TL"
        elif not late_in and scan_out_seconds >= ec_time_limit_seconds:
            return "H"
        elif early_out:
            return "TP"
        else:
            return "H"
    
    return "A"

def validate_lembur_time(absensi, jam_lembur_awal_time, jam_lembur_akhir_time):
    """Validate overtime hours against attendance data."""

    invalid_statuses = ['TL', 'TP', 'S', 'D', 'CT']
    if absensi['keterangan'] in invalid_statuses:
        return False, f'Pengajuan lembur tidak diizinkan untuk status: {absensi["keterangan"]}'
    
    
    if (absensi['keterangan'] in ['L', 'LB', 'LD', 'LDL', 'Libur', 'Belum Ada Data'] or 
        absensi['scan_in'] == '-' or absensi['scan_out'] == '-'):
        return True, None
    
    try:
        
        scan_in_time = datetime.strptime(absensi['scan_in'], '%H:%M').time()
        scan_out_time = datetime.strptime(absensi['scan_out'], '%H:%M').time()
        
        
        overtime_overlaps = (
            (jam_lembur_awal_time >= scan_in_time and jam_lembur_awal_time <= scan_out_time) or
            (jam_lembur_akhir_time >= scan_in_time and jam_lembur_akhir_time <= scan_out_time) or
            jam_lembur_awal_time == scan_in_time or jam_lembur_akhir_time == scan_out_time
        )
        
        if overtime_overlaps:
            return False, (f'Jam lembur harus sepenuhnya di luar rentang jam masuk sebenarnya '
                          f'({absensi["scan_in"]}) hingga jam pulang sebenarnya ({absensi["scan_out"]}).')
        
        
        is_valid_early_overtime = jam_lembur_akhir_time < scan_in_time
        is_valid_late_overtime = jam_lembur_awal_time > scan_out_time
        
        if not (is_valid_early_overtime or is_valid_late_overtime):
            return False, (f'Lembur harus berupa lembur awal (berakhir sebelum {absensi["scan_in"]}) '
                          f'atau lembur akhir (dimulai setelah {absensi["scan_out"]}).')
        
        return True, None
        
    except ValueError:
        return False, 'Format waktu scan tidak valid.'

@app.route('/pengajuan-lembur', methods=['GET', 'POST'])
def pengajuan_lembur():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))

    nip = session.get('nip')
    if not nip:
        return redirect(url_for('login'))

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        
        cursor.execute("""
            SELECT pegawai_id, pegawai_nama, pembagian1_nama AS bagian
            FROM pegawai p
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE p.pegawai_nip = %s
        """, (nip,))
        pegawai = cursor.fetchone()
        
        if not pegawai:
            return redirect(url_for('login'))

        
        cursor.execute("SELECT nama FROM ref_nama_lembur")
        nama_options = [row['nama'] for row in cursor.fetchall()]
        
        cursor.execute("SELECT lokasi FROM ref_lokasi_kerja")
        lokasi_options = [row['lokasi'] for row in cursor.fetchall()]

        if request.method == 'POST':
            
            form_data = {
                'tanggal_lembur': request.form.get('tanggal_lembur'),
                'lokasi_kerja': request.form.get('lokasi_kerja'),
                'jam_lembur_awal': request.form.get('jam_lembur_awal'),
                'jam_lembur_akhir': request.form.get('jam_lembur_akhir'),
                'keterangan_lembur': request.form.get('keterangan_lembur', '')[:512],
                'nama_mengetahui': request.form.get('nama_mengetahui'),
                'nama_menyetujui': request.form.get('nama_menyetujui'),
                'pemberi_tugas': request.form.get('pemberi_tugas')
            }
            
            
            if not form_data['tanggal_lembur']:
                return redirect(url_for('pengajuan_lembur'))
            
            
            required_names = [form_data['nama_mengetahui'], form_data['nama_menyetujui'], form_data['pemberi_tugas']]
            if not all(name in nama_options for name in required_names):
                return redirect(url_for('pengajuan_lembur'))
            
            if form_data['lokasi_kerja'] not in lokasi_options:
                return redirect(url_for('pengajuan_lembur'))
            
            
            try:
                tanggal_lembur_obj = datetime.strptime(form_data['tanggal_lembur'], '%Y-%m-%d').date()
                if tanggal_lembur_obj > date.today():
                    return redirect(url_for('pengajuan_lembur'))
                nomor_surat = f"{nip[:4]}/{tanggal_lembur_obj.strftime('%d/%m/%Y')}"
            except ValueError:
                return redirect(url_for('pengajuan_lembur'))
            
            
            try:
                jam_lembur_awal_time = datetime.strptime(form_data['jam_lembur_awal'], '%H:%M').time()
                jam_lembur_akhir_time = datetime.strptime(form_data['jam_lembur_akhir'], '%H:%M').time()
                if jam_lembur_akhir_time <= jam_lembur_awal_time:
                    return redirect(url_for('pengajuan_lembur'))
            except ValueError:
                return redirect(url_for('pengajuan_lembur'))
            
            
            absensi = get_absensi_data(cursor, nip, form_data['tanggal_lembur'])
            is_valid, error_message = validate_lembur_time(absensi, jam_lembur_awal_time, jam_lembur_akhir_time)
            
            if not is_valid:
                return redirect(url_for('pengajuan_lembur'))
            
            
            cursor.execute("""
                INSERT INTO pengajuan_lembur (
                    pegawai_id, pegawai_nip, penerima_tugas, nomor_surat, tanggal_lembur, lokasi_kerja, 
                    jam_lembur_awal, jam_lembur_akhir, keterangan_lembur, 
                    nama_mengetahui, nama_menyetujui, pemberi_tugas, status, tanggal_pengajuan
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                pegawai['pegawai_id'], nip, pegawai['pegawai_nama'], nomor_surat, 
                form_data['tanggal_lembur'], form_data['lokasi_kerja'],
                form_data['jam_lembur_awal'], form_data['jam_lembur_akhir'], 
                form_data['keterangan_lembur'], form_data['nama_mengetahui'], 
                form_data['nama_menyetujui'], form_data['pemberi_tugas'], 
                'Menunggu', datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
            
            connection.commit()
            
            session['pengajuan_berhasil'] = True
            session['id_lembur_baru'] = cursor.lastrowid
            
            return redirect(url_for('pengajuan_lembur'))

        
        absensi = None
        tanggal_lembur = request.args.get('tanggal_lembur')
        
        if tanggal_lembur:
            try:
                tanggal_lembur_obj = datetime.strptime(tanggal_lembur, '%Y-%m-%d').date()
                if tanggal_lembur_obj <= date.today():
                    absensi = get_absensi_data(cursor, nip, tanggal_lembur)
            except ValueError:
                pass

        
        pengajuan_berhasil = session.pop('pengajuan_berhasil', False)
        id_lembur_baru = session.pop('id_lembur_baru', None)

        return render_template('pengajuan_lembur.html', 
                             pegawai=pegawai, 
                             absensi=absensi, 
                             tanggal_lembur=tanggal_lembur, 
                             today=date.today(),
                             nama_options=nama_options,
                             lokasi_options=lokasi_options,
                             pengajuan_berhasil=pengajuan_berhasil,
                             id_lembur_baru=id_lembur_baru)

    except Error as e:
        return render_template('pengajuan_lembur.html', 
                             pegawai=None, 
                             absensi=None, 
                             today=date.today(),
                             nama_options=[],
                             lokasi_options=[],
                             pengajuan_berhasil=False,
                             id_lembur_baru=None)
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()




@app.route('/lemburan-saya')
def lemburan_saya():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))
    
    pegawai_id = session.get('id')
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT pl.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM pengajuan_lembur pl
            JOIN pegawai p ON pl.pegawai_id = p.pegawai_id
            WHERE pl.pegawai_id = %s
            ORDER BY pl.tanggal_pengajuan DESC
        """
        
        cursor.execute(query, (pegawai_id,))
        lembur_records = cursor.fetchall()
        
        for record in lembur_records:
            if record['tanggal_lembur']:
                record['tanggal_lembur'] = record['tanggal_lembur'].strftime('%d/%m/%Y')
            if record['tanggal_pengajuan']:
                
                if isinstance(record['tanggal_pengajuan'], str):
                    try:
                        tanggal_pengajuan = datetime.strptime(record['tanggal_pengajuan'], '%Y-%m-%d %H:%M:%S')
                        record['tanggal_pengajuan'] = tanggal_pengajuan.strftime('%d/%m/%Y %H:%M')
                    except ValueError:
                        record['tanggal_pengajuan'] = record['tanggal_pengajuan']
                else:
                    record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')
            
            
            if record['jam_lembur_awal'] and record['jam_lembur_akhir']:
                try:
                    
                    if isinstance(record['jam_lembur_awal'], str):
                        jam_awal = datetime.strptime(record['jam_lembur_awal'], '%H:%M:%S').time()
                    else:
                        jam_awal = record['jam_lembur_awal']
                    
                    if isinstance(record['jam_lembur_akhir'], str):
                        jam_akhir = datetime.strptime(record['jam_lembur_akhir'], '%H:%M:%S').time()
                    else:
                        jam_akhir = record['jam_lembur_akhir']
                    
                    
                    datetime_awal = datetime.combine(date.today(), jam_awal)
                    datetime_akhir = datetime.combine(date.today(), jam_akhir)
                    
                    
                    if datetime_akhir <= datetime_awal:
                        datetime_akhir = datetime.combine(date.today() + timedelta(days=1), jam_akhir)
                    
                    duration = datetime_akhir - datetime_awal
                    hours = duration.total_seconds() / 3600
                    record['durasi_jam'] = f"{hours:.1f} jam"
                    
                    
                    record['jam_lembur_awal'] = jam_awal.strftime('%H:%M')
                    record['jam_lembur_akhir'] = jam_akhir.strftime('%H:%M')
                    
                except (ValueError, TypeError):
                    record['durasi_jam'] = '-'
                    record['jam_lembur_awal'] = str(record['jam_lembur_awal']) if record['jam_lembur_awal'] else '-'
                    record['jam_lembur_akhir'] = str(record['jam_lembur_akhir']) if record['jam_lembur_akhir'] else '-'
            else:
                record['durasi_jam'] = '-'
                record['jam_lembur_awal'] = '-'
                record['jam_lembur_akhir'] = '-'
        
    except Error as e:
        lembur_records = []
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return render_template('lemburan_saya.html', lembur_records=lembur_records)




@app.route('/kirim-email-lembur/<int:id_lembur>', methods=['POST'])
def kirim_email_lembur(id_lembur):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'message': 'Please login to access this page.'})
    
    pegawai_id = session.get('id')
    email_tujuan = request.json.get('email')
    
    if not email_tujuan:
        return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pl.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM pengajuan_lembur pl
            JOIN pegawai p ON pl.pegawai_id = p.pegawai_id
            WHERE pl.id_lembur = %s AND pl.pegawai_id = %s
        """, (id_lembur, pegawai_id))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({'success': False, 'message': 'Data pengajuan lembur tidak ditemukan.'})
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-lembur.docx')
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': 'Template file tidak ditemukan.'})
        
        doc = Document(template_path)
        
        
        durasi_jam = '-'
        if record['jam_lembur_awal'] and record['jam_lembur_akhir']:
            try:
                
                if isinstance(record['jam_lembur_awal'], str):
                    jam_awal = datetime.strptime(record['jam_lembur_awal'], '%H:%M:%S').time()
                else:
                    jam_awal = record['jam_lembur_awal']
                
                if isinstance(record['jam_lembur_akhir'], str):
                    jam_akhir = datetime.strptime(record['jam_lembur_akhir'], '%H:%M:%S').time()
                else:
                    jam_akhir = record['jam_lembur_akhir']
                
                
                datetime_awal = datetime.combine(date.today(), jam_awal)
                datetime_akhir = datetime.combine(date.today(), jam_akhir)
                
                
                if datetime_akhir <= datetime_awal:
                    datetime_akhir = datetime.combine(date.today() + timedelta(days=1), jam_akhir)
                
                duration = datetime_akhir - datetime_awal
                hours = duration.total_seconds() / 3600
                durasi_jam = f"{hours:.1f} jam"
                
                jam_awal_str = jam_awal.strftime('%H:%M')
                jam_akhir_str = jam_akhir.strftime('%H:%M')
                
            except (ValueError, TypeError):
                jam_awal_str = str(record['jam_lembur_awal']) if record['jam_lembur_awal'] else '-'
                jam_akhir_str = str(record['jam_lembur_akhir']) if record['jam_lembur_akhir'] else '-'
        else:
            jam_awal_str = '-'
            jam_akhir_str = '-'
        
        data_pengganti = {
            'penerima_tugas': format_name(record['pegawai_nama']),
            'pegawai_nik': record['pegawai_nik_lintas'] or '-',
            'pegawai_nip': record['pegawai_nip'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'tanggal_lembur': record['tanggal_lembur'].strftime('%d %B %Y') if record['tanggal_lembur'] else '-',
            'tanggal_pengajuan': record['tanggal_pengajuan'].strftime('%d %B %Y') if record['tanggal_pengajuan'] else '-',
            'jam_lembur_awal': jam_awal_str,
            'jam_lembur_akhir': jam_akhir_str,
            'lokasi_kerja': record['lokasi_kerja'] or '-',
            'keterangan_lembur': record['keterangan_lembur'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'pemberi_tugas': record['pemberi_tugas'] or '-'
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Lembur_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
                
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
        
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_content = pdf_file.read()
        
        EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
        PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
        
        subjek = f"Form Pengajuan Lembur - {record['pegawai_nama']} ({record['nomor_surat']})"
        pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Pengajuan Lembur</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form pengajuan lembur <strong>{record['pegawai_nama']}</strong> :

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tanggal Lembur</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_lembur'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Jam Lembur</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{jam_awal_str} - {jam_akhir_str}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Durasi</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{durasi_jam}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Lokasi</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['lokasi_kerja']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form lemburnya sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
        """
        
        berhasil = kirim_email_pdf(
            pengirim_email=EMAIL_PENGIRIM,
            pengirim_password=PASSWORD_APLIKASI,
            penerima_email=email_tujuan,
            subjek=subjek,
            pesan=pesan,
            pdf_content=pdf_content,
            filename=f"{filename_base}.pdf"
        )
        
        if berhasil:
            return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
        else:
            return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/download-lembur/<int:id_lembur>')
def download_lembur(id_lembur):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('role')
    if user_role not in ['admin', 'pegawai']:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))
    
    if user_role == 'pegawai':
        pegawai_id = session.get('id')
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute("SELECT pegawai_id FROM pengajuan_lembur WHERE id_lembur = %s", (id_lembur,))
            lembur_check = cursor.fetchone()
            
            if not lembur_check or lembur_check['pegawai_id'] != pegawai_id:
                return redirect(url_for('lemburan_saya'))
                
        except Error as e:
            return redirect(url_for('lemburan_saya'))
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
        return redirect(url_for(redirect_url))
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pl.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM pengajuan_lembur pl
            JOIN pegawai p ON pl.pegawai_id = p.pegawai_id
            WHERE pl.id_lembur = %s
        """, (id_lembur,))
        record = cursor.fetchone()
        
        if not record:
            redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
            return redirect(url_for(redirect_url))
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-lembur.docx')
        
        if not os.path.exists(template_path):
            redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
            return redirect(url_for(redirect_url))
        
        doc = Document(template_path)
        
        
        durasi_jam = '-'
        if record['jam_lembur_awal'] and record['jam_lembur_akhir']:
            try:
                
                if isinstance(record['jam_lembur_awal'], str):
                    jam_awal = datetime.strptime(record['jam_lembur_awal'], '%H:%M:%S').time()
                else:
                    jam_awal = record['jam_lembur_awal']
                
                if isinstance(record['jam_lembur_akhir'], str):
                    jam_akhir = datetime.strptime(record['jam_lembur_akhir'], '%H:%M:%S').time()
                else:
                    jam_akhir = record['jam_lembur_akhir']
                
                
                datetime_awal = datetime.combine(date.today(), jam_awal)
                datetime_akhir = datetime.combine(date.today(), jam_akhir)
                
                
                if datetime_akhir <= datetime_awal:
                    datetime_akhir = datetime.combine(date.today() + timedelta(days=1), jam_akhir)
                
                duration = datetime_akhir - datetime_awal
                hours = duration.total_seconds() / 3600
                durasi_jam = f"{hours:.1f} jam"
                
                jam_awal_str = jam_awal.strftime('%H:%M')
                jam_akhir_str = jam_akhir.strftime('%H:%M')
                
            except (ValueError, TypeError):
                jam_awal_str = str(record['jam_lembur_awal']) if record['jam_lembur_awal'] else '-'
                jam_akhir_str = str(record['jam_lembur_akhir']) if record['jam_lembur_akhir'] else '-'
        else:
            jam_awal_str = '-'
            jam_akhir_str = '-'
        
        data_pengganti = {
            'penerima_tugas': format_name(record['pegawai_nama']),
            'pegawai_nik': record['pegawai_nik_lintas'] or '-',
            'pegawai_nip': record['pegawai_nip'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'tanggal_lembur': record['tanggal_lembur'].strftime('%d %B %Y') if record['tanggal_lembur'] else '-',
            'tanggal_pengajuan': record['tanggal_pengajuan'].strftime('%d %B %Y') if record['tanggal_pengajuan'] else '-',
            'jam_lembur_awal': jam_awal_str,
            'jam_lembur_akhir': jam_akhir_str,
            'lokasi_kerja': record['lokasi_kerja'] or '-',
            'keterangan_lembur': record['keterangan_lembur'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'pemberi_tugas': record['pemberi_tugas'] or '-'
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Lembur_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
                return redirect(url_for(redirect_url))
                
        except subprocess.TimeoutExpired:
            redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
            return redirect(url_for(redirect_url))
        except Exception as e:
            redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
            return redirect(url_for(redirect_url))
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
            return redirect(url_for(redirect_url))
        
        filename_pdf = f"{filename_base}.pdf"
        return send_file(temp_pdf_path, download_name=filename_pdf, as_attachment=True, 
                        mimetype='application/pdf')
        
    except Exception as e:
        redirect_url = 'lemburan_saya' if user_role == 'pegawai' else 'daftar_lembur'
        return redirect(url_for(redirect_url))
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                import time
                time.sleep(1)
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()




@app.route('/kirim-email-pengajuan-lembur/<int:id_lembur>', methods=['POST'])
def kirim_email_pengajuan_lembur(id_lembur):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'message': 'Please login to access this page.'})
    
    pegawai_id = session.get('id')
    email_tujuan = request.json.get('email')
    
    if not email_tujuan:
        return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pl.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM pengajuan_lembur pl
            JOIN pegawai p ON pl.pegawai_id = p.pegawai_id
            WHERE pl.id_lembur = %s AND pl.pegawai_id = %s
        """, (id_lembur, pegawai_id))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({'success': False, 'message': 'Data pengajuan lembur tidak ditemukan.'})
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-lembur.docx')
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': 'Template file tidak ditemukan.'})
        
        doc = Document(template_path)
        
        
        durasi_jam = '-'
        if record['jam_lembur_awal'] and record['jam_lembur_akhir']:
            try:
                
                if isinstance(record['jam_lembur_awal'], str):
                    jam_awal = datetime.strptime(record['jam_lembur_awal'], '%H:%M:%S').time()
                else:
                    jam_awal = record['jam_lembur_awal']
                
                if isinstance(record['jam_lembur_akhir'], str):
                    jam_akhir = datetime.strptime(record['jam_lembur_akhir'], '%H:%M:%S').time()
                else:
                    jam_akhir = record['jam_lembur_akhir']
                
                
                datetime_awal = datetime.combine(date.today(), jam_awal)
                datetime_akhir = datetime.combine(date.today(), jam_akhir)
                
                
                if datetime_akhir <= datetime_awal:
                    datetime_akhir = datetime.combine(date.today() + timedelta(days=1), jam_akhir)
                
                duration = datetime_akhir - datetime_awal
                hours = duration.total_seconds() / 3600
                durasi_jam = f"{hours:.1f} jam"
                
                jam_awal_str = jam_awal.strftime('%H:%M')
                jam_akhir_str = jam_akhir.strftime('%H:%M')
                
            except (ValueError, TypeError):
                jam_awal_str = str(record['jam_lembur_awal']) if record['jam_lembur_awal'] else '-'
                jam_akhir_str = str(record['jam_lembur_akhir']) if record['jam_lembur_akhir'] else '-'
        else:
            jam_awal_str = '-'
            jam_akhir_str = '-'
        
        data_pengganti = {
            'penerima_tugas': format_name(record['pegawai_nama']),
            'pegawai_nik': record['pegawai_nik_lintas'] or '-',
            'pegawai_nip': record['pegawai_nip'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'tanggal_lembur': record['tanggal_lembur'].strftime('%d %B %Y') if record['tanggal_lembur'] else '-',
            'tanggal_pengajuan': record['tanggal_pengajuan'].strftime('%d %B %Y') if record['tanggal_pengajuan'] else '-',
            'jam_lembur_awal': jam_awal_str,
            'jam_lembur_akhir': jam_akhir_str,
            'lokasi_kerja': record['lokasi_kerja'] or '-',
            'keterangan_lembur': record['keterangan_lembur'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'pemberi_tugas': record['pemberi_tugas'] or '-'
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Lembur_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
                
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
        
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_content = pdf_file.read()
        
        EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
        PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
        
        subjek = f"Form Pengajuan Lembur - {record['pegawai_nama']} ({record['nomor_surat']})"
        pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Pengajuan Lembur</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form pengajuan lembur <strong>{record['pegawai_nama']}</strong> :

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tanggal Lembur</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_lembur'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Jam Lembur</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{jam_awal_str} - {jam_akhir_str}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Durasi</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{durasi_jam}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Lokasi</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['lokasi_kerja']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form lemburnya sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
        """
        
        berhasil = kirim_email_pdf(
            pengirim_email=EMAIL_PENGIRIM,
            pengirim_password=PASSWORD_APLIKASI,
            penerima_email=email_tujuan,
            subjek=subjek,
            pesan=pesan,
            pdf_content=pdf_content,
            filename=f"{filename_base}.pdf"
        )
        
        if berhasil:
            return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
        else:
            return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()





@app.route('/daftar-lembur')
def daftar_lembur():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status_filter = request.args.get('status_filter')
    search_nama = request.args.get('search_nama')
    divisi_filter = request.args.get('divisi_filter')
    lokasi_filter = request.args.get('lokasi_filter')

    if not start_date:
        start_date = (date.today() - timedelta(days=30)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = date.today().strftime('%Y-%m-%d')

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT pl.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip, pb1.pembagian1_nama
            FROM pengajuan_lembur pl
            JOIN pegawai p ON pl.pegawai_id = p.pegawai_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE pl.tanggal_pengajuan BETWEEN %s AND %s
        """

        params = [start_date + ' 00:00:00', end_date + ' 23:59:59']

        if status_filter:
            query += ' AND pl.status = %s'
            params.append(status_filter)

        if search_nama:
            search_string = f'%{search_nama}%'
            query += ' AND (p.pegawai_nama LIKE %s OR p.pegawai_nik_lintas LIKE %s)'
            params += [search_string, search_string]

        if divisi_filter:
            query += ' AND pb1.pembagian1_nama = %s'
            params.append(divisi_filter)
            
        if lokasi_filter:
            query += ' AND pl.lokasi_kerja LIKE %s'
            params.append(f'%{lokasi_filter}%')

        query += ' ORDER BY pl.tanggal_pengajuan DESC'

        cursor.execute(query, params)
        lembur_records = cursor.fetchall()

        total_overtime_hours = 0

        for record in lembur_records:
            if record['tanggal_lembur']:
                record['tanggal_lembur'] = record['tanggal_lembur'].strftime('%d/%m/%Y')
            if record['tanggal_pengajuan']:
                if isinstance(record['tanggal_pengajuan'], str):
                    try:
                        tanggal_pengajuan = datetime.strptime(record['tanggal_pengajuan'], '%Y-%m-%d %H:%M:%S')
                        record['tanggal_pengajuan'] = tanggal_pengajuan.strftime('%d/%m/%Y %H:%M')
                    except ValueError:
                        record['tanggal_pengajuan'] = record['tanggal_pengajuan']
                else:
                    record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')
            
            if record['jam_lembur_awal'] and record['jam_lembur_akhir']:
                duration_hours, duration_str = calculate_overtime_duration(
                    record['jam_lembur_awal'], 
                    record['jam_lembur_akhir']
                )
                record['durasi_jam'] = duration_str
                
                if record['status'] == 'OK':
                    total_overtime_hours += duration_hours
                
                try:
                    if isinstance(record['jam_lembur_awal'], timedelta):
                        total_seconds_awal = int(record['jam_lembur_awal'].total_seconds())
                        hours_awal = total_seconds_awal // 3600
                        minutes_awal = (total_seconds_awal % 3600) // 60
                        jam_awal = time(hours_awal, minutes_awal)
                    elif isinstance(record['jam_lembur_awal'], str):
                        jam_awal = datetime.strptime(record['jam_lembur_awal'], '%H:%M:%S').time()
                    else:
                        jam_awal = record['jam_lembur_awal']
                    
                    if isinstance(record['jam_lembur_akhir'], timedelta):
                        total_seconds_akhir = int(record['jam_lembur_akhir'].total_seconds())
                        hours_akhir = total_seconds_akhir // 3600
                        minutes_akhir = (total_seconds_akhir % 3600) // 60
                        jam_akhir = time(hours_akhir, minutes_akhir)
                    elif isinstance(record['jam_lembur_akhir'], str):
                        jam_akhir = datetime.strptime(record['jam_lembur_akhir'], '%H:%M:%S').time()
                    else:
                        jam_akhir = record['jam_lembur_akhir']
                    
                    record['jam_lembur_awal'] = jam_awal.strftime('%H:%M')
                    record['jam_lembur_akhir'] = jam_akhir.strftime('%H:%M')
                    
                except (ValueError, TypeError, AttributeError):
                    record['jam_lembur_awal'] = str(record['jam_lembur_awal']) if record['jam_lembur_awal'] else '-'
                    record['jam_lembur_akhir'] = str(record['jam_lembur_akhir']) if record['jam_lembur_akhir'] else '-'
            else:
                record['jam_lembur_awal'] = '-'
                record['jam_lembur_akhir'] = '-'
                record['durasi_jam'] = '0 jam'

        cursor.execute("SELECT DISTINCT pembagian1_nama FROM pembagian1 ORDER BY pembagian1_nama")
        divisi_list = cursor.fetchall()
        
        cursor.execute("SELECT DISTINCT lokasi_kerja FROM pengajuan_lembur ORDER BY lokasi_kerja")
        lokasi_list = cursor.fetchall()

        if total_overtime_hours == int(total_overtime_hours):
            total_overtime_display = f"{int(total_overtime_hours)} jam"
        else:
            total_overtime_display = f"{total_overtime_hours:.1f} jam"

    except Error as e:
        lembur_records = []
        divisi_list = []
        lokasi_list = []
        total_overtime_display = "0 jam"
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

    return render_template('daftar_lembur.html', 
                         lembur_records=lembur_records,
                         divisi_list=divisi_list,
                         lokasi_list=lokasi_list,
                         start_date=start_date,
                         end_date=end_date,
                         status_filter=status_filter,
                         search_nama=search_nama,
                         divisi_filter=divisi_filter,
                         lokasi_filter=lokasi_filter,
                         total_overtime_display=total_overtime_display)

def calculate_overtime_duration(jam_awal, jam_akhir):
    try:
        if not jam_awal or not jam_akhir:
            return 0, "0 jam"
        
        if isinstance(jam_awal, timedelta):
            total_seconds_awal = int(jam_awal.total_seconds())
            hours_awal = total_seconds_awal // 3600
            minutes_awal = (total_seconds_awal % 3600) // 60
            jam_awal_time = time(hours_awal, minutes_awal)
        elif isinstance(jam_awal, str):
            jam_awal_time = datetime.strptime(jam_awal, '%H:%M:%S').time()
        else:
            jam_awal_time = jam_awal
        
        if isinstance(jam_akhir, timedelta):
            total_seconds_akhir = int(jam_akhir.total_seconds())
            hours_akhir = total_seconds_akhir // 3600
            minutes_akhir = (total_seconds_akhir % 3600) // 60
            jam_akhir_time = time(hours_akhir, minutes_akhir)
        elif isinstance(jam_akhir, str):
            jam_akhir_time = datetime.strptime(jam_akhir, '%H:%M:%S').time()
        else:
            jam_akhir_time = jam_akhir
        
        datetime_awal = datetime.combine(date.today(), jam_awal_time)
        datetime_akhir = datetime.combine(date.today(), jam_akhir_time)
        
        if datetime_akhir <= datetime_awal:
            datetime_akhir = datetime.combine(date.today() + timedelta(days=1), jam_akhir_time)
        
        duration = datetime_akhir - datetime_awal
        hours = duration.total_seconds() / 3600
        
        if hours == int(hours):
            duration_str = f"{int(hours)} jam"
        else:
            duration_str = f"{hours:.1f} jam"
        
        return hours, duration_str
    except (ValueError, TypeError, AttributeError):
        return 0, "0 jam"

@app.route('/update-status-lembur/<int:id_lembur>', methods=['POST'])
def update_status_lembur(id_lembur):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'})
    
    new_status = request.json.get('status')
    
    if new_status not in ['OK', 'Menunggu', 'Ditolak']:
        return jsonify({'success': False, 'message': 'Status tidak valid'})
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM pengajuan_lembur WHERE id_lembur = %s", (id_lembur,))
        lembur_record = cursor.fetchone()
        
        if not lembur_record:
            return jsonify({'success': False, 'message': 'Data pengajuan lembur tidak ditemukan'})
        
        cursor.execute("UPDATE pengajuan_lembur SET status = %s WHERE id_lembur = %s", (new_status, id_lembur))
        connection.commit()
        
        return jsonify({'success': True, 'message': 'Status berhasil diupdate'})
        
    except Error as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/hapus-lembur-admin/<int:id_lembur>')
def hapus_lembur_admin(id_lembur):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM pengajuan_lembur WHERE id_lembur = %s", (id_lembur,))
        lembur_record = cursor.fetchone()
        
        if not lembur_record:
            return redirect(url_for('daftar_lembur'))
        
        cursor.execute("DELETE FROM pengajuan_lembur WHERE id_lembur = %s", (id_lembur,))
        connection.commit()
        
    except Error as e:
        pass
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return redirect(url_for('daftar_lembur'))

from docx import Document
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Pt
from io import BytesIO
import math


def time_to_hhmm(time_obj):
    """Convert time or timedelta object to HH:MM string."""
    if time_obj is None:
        return '-'
    if isinstance(time_obj, timedelta):
        total_seconds = int(time_obj.total_seconds())
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        return f'{hours:02d}:{minutes:02d}'
    if isinstance(time_obj, time):
        return time_obj.strftime('%H:%M')
    return '-'

def format_name(name):
    """Format name to title case and abbreviate if 3 or more words."""
    if not name or name == '-':
        return '-'
    words = name.title().split()
    if len(words) < 3:
        return ' '.join(words)
    return ' '.join(words[:2]) + ' ' + ''.join(word[0] for word in words[2:])


def calculate_cuti_days(pegawai_id, pegawai_nip, start_date, end_date):
    """
    Menghitung jumlah hari cuti dengan logika yang sama seperti saat pengajuan
    """
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        special_nips = ['4790', '3338', '3434', '3332']
        is_special_employee = pegawai_nip in special_nips
        
        
        cursor.execute("SELECT tanggal FROM cuti_libur_nasional")
        libur_nasional_result = cursor.fetchall()
        libur_nasional_dates = set()
        for row in libur_nasional_result:
            libur_nasional_dates.add(row['tanggal'])
        
        special_libur_dates = set()
        regular_libur_dates = set()
        
        if is_special_employee:
            cursor.execute("SELECT tgl_shift FROM shift_result_new WHERE pegawai_id = %s AND jk_id IN (30, 10)", (pegawai_id,))
            special_libur_result = cursor.fetchall()
            for row in special_libur_result:
                special_libur_dates.add(row['tgl_shift'])
        else:
            cursor.execute("SELECT tgl_shift FROM shift_result_new WHERE pegawai_id = %s AND jk_id = 10", (pegawai_id,))
            regular_libur_result = cursor.fetchall()
            for row in regular_libur_result:
                regular_libur_dates.add(row['tgl_shift'])
        
        hari_cuti = 0
        current_date = start_date
        
        while current_date <= end_date:
            date_obj = current_date.date() if hasattr(current_date, 'date') else current_date
            
            if is_special_employee:
                
                if date_obj not in special_libur_dates:
                    hari_cuti += 1
            else:
                
                if hasattr(current_date, 'weekday'):
                    weekday = current_date.weekday()
                else:
                    weekday = current_date.weekday()
                    
                if (weekday < 5 and 
                    date_obj not in libur_nasional_dates and 
                    date_obj not in regular_libur_dates):
                    hari_cuti += 1
            
            current_date += timedelta(days=1)
        
        return hari_cuti
        
    except Exception as e:
      
        return 0
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

            
@app.route('/pengajuan-cuti', methods=['GET', 'POST'])
def pengajuan_cuti():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        pegawai_id = session.get('id')
        pegawai_nik_lintas = session.get('nik_lintas')
        pegawai_nip = session.get('nip')
        
        if not pegawai_id or not pegawai_nik_lintas or not pegawai_nip:
            return redirect(url_for('login'))
        
        kategori_cuti = request.form['kategori_cuti']
        tim = request.form['tim']
        personil_backup = request.form['personil_backup']
        alasan = request.form['alasan']
        nama_mengetahui = request.form['nama_mengetahui']
        nama_menyetujui = request.form['nama_menyetujui']
        tanggal_cuti_mulai = request.form['tanggal_cuti_mulai']
        tanggal_cuti_akhir = request.form['tanggal_cuti_akhir']
        
        start_date = datetime.strptime(tanggal_cuti_mulai, '%Y-%m-%d')
        end_date = datetime.strptime(tanggal_cuti_akhir, '%Y-%m-%d')

                
        batas_maksimal = datetime(2026, 7, 6)

        if start_date > batas_maksimal:
            return redirect(url_for('pengajuan_cuti'))

        if end_date > batas_maksimal:
            return redirect(url_for('pengajuan_cuti'))
        
        special_nips = ['4790', '3338', '3434', '3332']
        is_special_employee = pegawai_nip in special_nips
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute("SELECT tanggal FROM cuti_libur_nasional")
            libur_nasional_result = cursor.fetchall()
            libur_nasional_dates = set()
            for row in libur_nasional_result:
                libur_nasional_dates.add(row['tanggal'])
            
            special_libur_dates = set()
            regular_libur_dates = set()
            
            if is_special_employee:
                cursor.execute("SELECT tgl_shift FROM shift_result_new WHERE pegawai_id = %s AND jk_id = 30", (pegawai_id,))
                special_libur_result = cursor.fetchall()
                for row in special_libur_result:
                    special_libur_dates.add(row['tgl_shift'])
            else:
                cursor.execute("SELECT tgl_shift FROM shift_result_new WHERE pegawai_id = %s AND jk_id = 10", (pegawai_id,))
                regular_libur_result = cursor.fetchall()
                for row in regular_libur_result:
                    regular_libur_dates.add(row['tgl_shift'])
            
            hari_cuti = 0
            current_date = start_date
            while current_date <= end_date:
                if is_special_employee:
                    if current_date.date() not in special_libur_dates:
                        hari_cuti += 1
                else:
                    if (current_date.weekday() < 5 and 
                        current_date.date() not in libur_nasional_dates and 
                        current_date.date() not in regular_libur_dates):
                        hari_cuti += 1
                current_date += timedelta(days=1)
            
            if kategori_cuti in ['CT', 'CTD'] and hari_cuti > 2:
                return redirect(url_for('pengajuan_cuti'))
            
            today = datetime.now()
            nomor_surat = f"{pegawai_nik_lintas}/CUTI/{today.strftime('%d%m%Y')}"
            
            cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
            sisa_cuti_result = cursor.fetchone()
            sisa_cuti_saat_ini = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result and sisa_cuti_result['sisa_cuti'] is not None else 0
            
            hari_pengurangan_cuti = 0
            if kategori_cuti in ['CT', 'CTD']:
                hari_pengurangan_cuti = hari_cuti
            
            if hari_pengurangan_cuti > sisa_cuti_saat_ini:
                return redirect(url_for('pengajuan_cuti'))
            
            cursor.execute("SELECT pembagian1_nama FROM pegawai p JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id WHERE p.pegawai_id = %s", (pegawai_id,))
            divisi_result = cursor.fetchone()
            divisi_user = divisi_result['pembagian1_nama'] if divisi_result else ''
            
            current_check_date = start_date
            while current_check_date <= end_date:
                should_check = True
                if is_special_employee:
                    should_check = current_check_date.date() not in special_libur_dates
                else:
                    should_check = (current_check_date.weekday() < 5 and 
                                  current_check_date.date() not in libur_nasional_dates and 
                                  current_check_date.date() not in regular_libur_dates)
                
                if should_check and kategori_cuti == 'CT':
                    cursor.execute("""
                        SELECT COUNT(*) as total_cuti,
                               SUM(CASE WHEN pb1.pembagian1_nama = 'HDTI' THEN 1 ELSE 0 END) as hdti_count,
                               SUM(CASE WHEN pb1.pembagian1_nama = 'HDBS' THEN 1 ELSE 0 END) as hdbs_count
                        FROM pengajuan_cuti pc
                        JOIN pegawai p ON pc.pegawai_id = p.pegawai_id
                        JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
                        WHERE pc.kategori_cuti = 'CT' 
                          AND pc.status = 'OK'
                          AND %s BETWEEN pc.tanggal_cuti_mulai AND pc.tanggal_cuti_akhir
                    """, (current_check_date.strftime('%Y-%m-%d'),))
                    
                    cuti_stats = cursor.fetchone()
                    total_cuti = cuti_stats['total_cuti'] if cuti_stats and cuti_stats['total_cuti'] is not None else 0
                    hdti_count = cuti_stats['hdti_count'] if cuti_stats and cuti_stats['hdti_count'] is not None else 0
                    hdbs_count = cuti_stats['hdbs_count'] if cuti_stats and cuti_stats['hdbs_count'] is not None else 0
                    
                    if total_cuti >= 7:
                        return redirect(url_for('pengajuan_cuti'))
                    
                    if divisi_user == 'HDTI' and hdti_count >= 5:
                        return redirect(url_for('pengajuan_cuti'))
                    
                    if divisi_user == 'HDBS' and hdbs_count >= 2:
                        return redirect(url_for('pengajuan_cuti'))
                
                current_check_date += timedelta(days=1)
            
            insert_query = """
                INSERT INTO pengajuan_cuti 
                (pegawai_id, pegawai_nip, nomor_surat, kategori_cuti, tim, personil_backup, 
                 alasan, nama_mengetahui, nama_menyetujui, tanggal_cuti_mulai, tanggal_cuti_akhir, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            cursor = connection.cursor()
            cursor.execute(insert_query, (
                pegawai_id, pegawai_nik_lintas, nomor_surat, kategori_cuti, tim, personil_backup,
                alasan, nama_mengetahui, nama_menyetujui, tanggal_cuti_mulai, tanggal_cuti_akhir, 'OK'
            ))
            
            id_cuti_baru = cursor.lastrowid
            
            sisa_cuti_baru = sisa_cuti_saat_ini - hari_pengurangan_cuti
            update_query = "UPDATE pegawai SET sisa_cuti = %s WHERE pegawai_id = %s"
            cursor.execute(update_query, (sisa_cuti_baru, pegawai_id))
            
            connection.commit()
            
            session['pengajuan_berhasil'] = True
            session['id_cuti_baru'] = id_cuti_baru
            session['sisa_cuti_baru'] = sisa_cuti_baru
            
            return redirect(url_for('pengajuan_cuti'))
            
        except Error as e:
            pass
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        pegawai_id = session.get('id')
        pegawai_nip = session.get('nip')
        
        cursor.execute("SELECT pembagian1_nama FROM pegawai p JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id WHERE p.pegawai_id = %s", (pegawai_id,))
        divisi_result = cursor.fetchone()
        divisi_user = divisi_result['pembagian1_nama'] if divisi_result else ''
        
        cursor.execute("SELECT * FROM tim WHERE pembagian1_nama = %s ORDER BY tim_nama", (divisi_user,))
        tim_list = cursor.fetchall()
        
        cursor.execute("SELECT pegawai_id, pegawai_nama FROM pegawai WHERE pegawai_id != 1 AND pegawai_nama != 'admin' AND pegawai_status = 1 ORDER BY pegawai_nama")
        pegawai_list = cursor.fetchall()
        
        cursor.execute("SELECT * FROM ref_nama_lembur ORDER BY nama")
        nama_mengetahui_list = cursor.fetchall()
        
        cursor.execute("SELECT * FROM ref_nama_lembur WHERE nama = 'Demo Supervisor' ORDER BY nama")
        nama_menyetujui_list = cursor.fetchall()
        
        cursor.execute("SELECT tanggal, keterangan FROM cuti_libur_nasional")
        libur_nasional_result = cursor.fetchall()
        libur_nasional_data = {}
        for row in libur_nasional_result:
            libur_nasional_data[row['tanggal'].strftime('%Y-%m-%d')] = row['keterangan']
        
        cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
        sisa_cuti_result = cursor.fetchone()
        sisa_cuti = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result and sisa_cuti_result['sisa_cuti'] is not None else 0
        
        special_nips = ['4790', '3338', '3434', '3332']
        is_special_employee = pegawai_nip in special_nips
        
        special_libur_data = {}
        regular_libur_data = {}
        
        if is_special_employee:
            cursor.execute("SELECT tgl_shift FROM shift_result_new WHERE pegawai_id = %s AND jk_id = 30", (pegawai_id,))
            special_libur_result = cursor.fetchall()
            for row in special_libur_result:
                special_libur_data[row['tgl_shift'].strftime('%Y-%m-%d')] = 'Libur Khusus'
        else:
            cursor.execute("SELECT tgl_shift FROM shift_result_new WHERE pegawai_id = %s AND jk_id = 10", (pegawai_id,))
            regular_libur_result = cursor.fetchall()
            for row in regular_libur_result:
                regular_libur_data[row['tgl_shift'].strftime('%Y-%m-%d')] = 'Libur'
        
    except Error as e:
        tim_list = []
        pegawai_list = []
        nama_mengetahui_list = []
        nama_menyetujui_list = []
        sisa_cuti = 0
        libur_nasional_data = {}
        is_special_employee = False
        special_libur_data = {}
        regular_libur_data = {}
        divisi_user = ''
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    pengajuan_berhasil = session.pop('pengajuan_berhasil', False)
    id_cuti_baru = session.pop('id_cuti_baru', None)
    sisa_cuti_baru = session.pop('sisa_cuti_baru', None)
    
    pegawai_nik_lintas = session.get('nik_lintas', '')
    today = datetime.now()
    nomor_surat_preview = f"{pegawai_nik_lintas}/CUTI/{today.strftime('%d%m%Y')}"
    
    return render_template('pengajuan_cuti.html', 
                         nomor_surat_preview=nomor_surat_preview,
                         tim_list=tim_list,
                         pegawai_list=pegawai_list,
                         nama_mengetahui_list=nama_mengetahui_list,
                         nama_menyetujui_list=nama_menyetujui_list,
                         sisa_cuti=sisa_cuti,
                         libur_nasional_data=libur_nasional_data,
                         is_special_employee=is_special_employee,
                         special_libur_data=special_libur_data,
                         regular_libur_data=regular_libur_data,
                         pengajuan_berhasil=pengajuan_berhasil,
                         id_cuti_baru=id_cuti_baru,
                         sisa_cuti_baru=sisa_cuti_baru,
                         divisi_user=divisi_user)

def get_jabatan_mengetahui(nama_mengetahui):
    if nama_mengetahui in ['Mukhlis Nasution', 'Dedy Rachmawan']:
        return 'Supervisor'
    else:
        return 'Pegawai BI'


@app.route('/kirim-email-pengajuan-cuti/<int:id_cuti>', methods=['POST'])
def kirim_email_pengajuan_cuti(id_cuti):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'message': 'Please login to access this page.'})
    
    pegawai_id = session.get('id')
    email_tujuan = request.json.get('email')
    
    if not email_tujuan:
        return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pc.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM pengajuan_cuti pc
            JOIN pegawai p ON pc.pegawai_id = p.pegawai_id
            WHERE pc.id_cuti = %s AND pc.pegawai_id = %s
        """, (id_cuti, pegawai_id))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({'success': False, 'message': 'Data pengajuan cuti tidak ditemukan.'})
        
        cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
        sisa_cuti_result = cursor.fetchone()
        sisa_cuti_terakhir = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result else 0
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-cuti.docx')
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': 'Template file tidak ditemukan.'})
        
        doc = Document(template_path)
        
        start_date = record['tanggal_cuti_mulai']
        end_date = record['tanggal_cuti_akhir']
        
        
        hari_cuti = calculate_cuti_days(
            record['pegawai_id'], 
            record['pegawai_nip'], 
            start_date, 
            end_date
        )
        
        kategori_nama = {
            'CT': 'Cuti Tahunan',
            'CTD': 'Cuti Mendadak', 
            'CTX': 'Cuti Extra'
        }
        
        data_pengganti = {
            'nama_personil': format_name(record['pegawai_nama']),
            'tgl_mulai': record['tanggal_cuti_mulai'].strftime('%d/%m/%Y') if record['tanggal_cuti_mulai'] else '-',
            'tgl_berakhir': record['tanggal_cuti_akhir'].strftime('%d/%m/%Y') if record['tanggal_cuti_akhir'] else '-',
            'tgl_pembuatan': record['tanggal_pengajuan'].strftime('%d/%m/%Y') if record['tanggal_pengajuan'] else '-',
            'tim_pesonil': record['tim'] or '-',
            'jumlah_cuti': f"{hari_cuti} hari",
            'kategori_cuti': kategori_nama.get(record['kategori_cuti'], record['kategori_cuti']) or '-',
            'alasan_cuti': record['alasan'] or '-',
            'personil_backup': format_name(record['personil_backup']),
            'sisa_cuti_terakhir': f"{sisa_cuti_terakhir} hari",
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'jabatan_mengetahui': get_jabatan_mengetahui(record['nama_mengetahui'])
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Cuti_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
                
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
        
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_content = pdf_file.read()
        
        EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
        PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
        
        subjek = f"Form Pengajuan Cuti - {record['pegawai_nama']} ({record['nomor_surat']})"
        pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Pengajuan Cuti</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form cuti <strong>{record['pegawai_nama']}</strong> :

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Kategori</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{kategori_nama.get(record['kategori_cuti'], record['kategori_cuti'])}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tanggal Cuti</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_cuti_mulai'].strftime('%d/%m/%Y')} s/d {record['tanggal_cuti_akhir'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Jumlah Hari</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{hari_cuti} hari</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tim</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tim']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form cutinya sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
        """
        
        berhasil = kirim_email_pdf(
            pengirim_email=EMAIL_PENGIRIM,
            pengirim_password=PASSWORD_APLIKASI,
            penerima_email=email_tujuan,
            subjek=subjek,
            pesan=pesan,
            pdf_content=pdf_content,
            filename=f"{filename_base}.pdf"
        )
        
        if berhasil:
            return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
        else:
            return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/cuti-saya')
def cuti_saya():
   if 'loggedin' not in session or session.get('role') != 'pegawai':
       return redirect(url_for('login'))
   
   pegawai_id = session.get('id')
   
   try:
       connection = get_db_connection()
       cursor = connection.cursor(dictionary=True)
       
       query = """
           SELECT pc.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
           FROM pengajuan_cuti pc
           JOIN pegawai p ON pc.pegawai_id = p.pegawai_id
           WHERE pc.pegawai_id = %s
           ORDER BY pc.tanggal_pengajuan DESC
       """
       
       cursor.execute(query, (pegawai_id,))
       cuti_records = cursor.fetchall()
       
       for record in cuti_records:
           start_date = record['tanggal_cuti_mulai']
           end_date = record['tanggal_cuti_akhir']
           
           
           hari_cuti = calculate_cuti_days(
               record['pegawai_id'], 
               record['pegawai_nip'], 
               start_date, 
               end_date
           )
           
           record['jumlah_hari'] = hari_cuti
           
           if record['tanggal_cuti_mulai']:
               record['tanggal_cuti_mulai'] = record['tanggal_cuti_mulai'].strftime('%d/%m/%Y')
           if record['tanggal_cuti_akhir']:
               record['tanggal_cuti_akhir'] = record['tanggal_cuti_akhir'].strftime('%d/%m/%Y')
           if record['tanggal_pengajuan']:
               record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')
       
   except Error as e:
       cuti_records = []
   finally:
       if connection.is_connected():
           cursor.close()
           connection.close()
   
   return render_template('cuti_saya.html', cuti_records=cuti_records)

@app.route('/hapus-cuti/<int:id_cuti>')
def hapus_cuti(id_cuti):
   if 'loggedin' not in session or session.get('role') != 'pegawai':
       return redirect(url_for('login'))
   
   pegawai_id = session.get('id')
   
   try:
       connection = get_db_connection()
       cursor = connection.cursor(dictionary=True)
       
       cursor.execute("SELECT * FROM pengajuan_cuti WHERE id_cuti = %s AND pegawai_id = %s", (id_cuti, pegawai_id))
       cuti_record = cursor.fetchone()
       
       if not cuti_record:
           return redirect(url_for('cuti_saya'))
       
       if cuti_record['kategori_cuti'] in ['CT', 'CTD']:
           start_date = datetime.strptime(str(cuti_record['tanggal_cuti_mulai']), '%Y-%m-%d')
           end_date = datetime.strptime(str(cuti_record['tanggal_cuti_akhir']), '%Y-%m-%d')
           
           hari_cuti = 0
           current_date = start_date
           while current_date <= end_date:
               if current_date.weekday() < 5:
                   hari_cuti += 1
               current_date += timedelta(days=1)
           
           cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
           sisa_cuti_result = cursor.fetchone()
           sisa_cuti_saat_ini = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result else 0
           
           sisa_cuti_baru = sisa_cuti_saat_ini + hari_cuti
           cursor.execute("UPDATE pegawai SET sisa_cuti = %s WHERE pegawai_id = %s", (sisa_cuti_baru, pegawai_id))
       
       cursor.execute("DELETE FROM pengajuan_cuti WHERE id_cuti = %s", (id_cuti,))
       connection.commit()
       
       
   except Error as e:
       pass
   finally:
       if connection.is_connected():
           cursor.close()
           connection.close()
   
   return redirect(url_for('cuti_saya'))

def format_name(name):
   """Format nama sesuai aturan: 2 kata pertama lengkap, sisanya inisial"""
   if not name:
       return '-'
   
   words = name.strip().split()
   if len(words) <= 2:
       return ' '.join([word.capitalize() for word in words])
   
   
   formatted_words = [word.capitalize() for word in words[:2]]
   
   
   initials = ''.join([word[0].upper() for word in words[2:]])
   
   return ' '.join(formatted_words) + ' ' + initials

@app.route('/download-cuti/<int:id_cuti>')
def download_cuti(id_cuti):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('role')
    if user_role not in ['admin', 'pegawai']:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))
    
    if user_role == 'pegawai':
        pegawai_id = session.get('id')
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute("SELECT pegawai_id FROM pengajuan_cuti WHERE id_cuti = %s", (id_cuti,))
            cuti_check = cursor.fetchone()
            
            if not cuti_check or cuti_check['pegawai_id'] != pegawai_id:
                return redirect(url_for('cuti_saya'))
                
        except Error as e:
            return redirect(url_for('cuti_saya'))
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
        return redirect(url_for(redirect_url))
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pc.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM pengajuan_cuti pc
            JOIN pegawai p ON pc.pegawai_id = p.pegawai_id
            WHERE pc.id_cuti = %s
        """, (id_cuti,))
        record = cursor.fetchone()
        
        if not record:
            redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
            return redirect(url_for(redirect_url))
        
        cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (record['pegawai_id'],))
        sisa_cuti_result = cursor.fetchone()
        sisa_cuti_terakhir = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result else 0
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-cuti.docx')
        
        if not os.path.exists(template_path):
            redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
            return redirect(url_for(redirect_url))
        
        doc = Document(template_path)
        
        start_date = record['tanggal_cuti_mulai']
        end_date = record['tanggal_cuti_akhir']
        
        hari_cuti = calculate_cuti_days(
            record['pegawai_id'], 
            record['pegawai_nip'], 
            start_date, 
            end_date
        )
        
        kategori_nama = {
            'CT': 'Cuti Tahunan',
            'CTD': 'Cuti Mendadak', 
            'CTX': 'Cuti Extra'
        }
        
        data_pengganti = {
            'nama_personil': format_name(record['pegawai_nama']),
            'tgl_mulai': record['tanggal_cuti_mulai'].strftime('%d/%m/%Y') if record['tanggal_cuti_mulai'] else '-',
            'tgl_berakhir': record['tanggal_cuti_akhir'].strftime('%d/%m/%Y') if record['tanggal_cuti_akhir'] else '-',
            'tgl_pembuatan': record['tanggal_pengajuan'].strftime('%d/%m/%Y') if record['tanggal_pengajuan'] else '-',
            'tim_pesonil': record['tim'] or '-',
            'jumlah_cuti': f"{hari_cuti} hari",
            'kategori_cuti': kategori_nama.get(record['kategori_cuti'], record['kategori_cuti']) or '-',
            'alasan_cuti': record['alasan'] or '-',
            'personil_backup': format_name(record['personil_backup']),
            'sisa_cuti_terakhir': f"{sisa_cuti_terakhir} hari",
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'jabatan_mengetahui': get_jabatan_mengetahui(record['nama_mengetahui'])
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Cuti_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
                return redirect(url_for(redirect_url))
                
        except subprocess.TimeoutExpired:
            redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
            return redirect(url_for(redirect_url))
        except Exception as e:
            redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
            return redirect(url_for(redirect_url))
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
            return redirect(url_for(redirect_url))
        
        filename_pdf = f"{filename_base}.pdf"
        return send_file(temp_pdf_path, download_name=filename_pdf, as_attachment=True, 
                        mimetype='application/pdf')
        
    except Exception as e:
        redirect_url = 'cuti_saya' if user_role == 'pegawai' else 'daftar_cuti'
        return redirect(url_for(redirect_url))
    finally:
       
        def clean_temp_folder(temp_dir):
            try:
                if os.path.exists(temp_dir):
                    import time
                    time.sleep(1)  
                    
                    for filename in os.listdir(temp_dir):
                        file_path = os.path.join(temp_dir, filename)
                        try:
                            if os.path.isfile(file_path):
                                os.remove(file_path)
                             
                            elif os.path.isdir(file_path):
                                import shutil
                                shutil.rmtree(file_path)
                                print(f"Deleted directory: {file_path}")
                        except Exception as e:
                            print(f"Failed to delete {file_path}: {e}")
            except Exception as e:
                print(f"Error cleaning temp folder: {e}")
        
     
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        clean_temp_folder(temp_dir)
        
  
        try:
            if 'connection' in locals() and connection.is_connected():
                cursor.close()
                connection.close()
        except:
            pass
        if connection.is_connected():
            cursor.close()
            connection.close()



import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

import subprocess
import os
import shutil

def check_libreoffice():
   """Check if LibreOffice is available and return the correct command"""
   commands_to_try = [
       "soffice",
       "libreoffice", 
       r"C:\Program Files\LibreOffice\program\soffice.exe",
       r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"
   ]
   
   for cmd in commands_to_try:
       try:
           if shutil.which(cmd) or os.path.exists(cmd):
               return cmd
       except:
           continue
   
   return None

@app.route('/kirim-email-cuti/<int:id_cuti>', methods=['POST'])
def kirim_email_cuti(id_cuti):
   if 'loggedin' not in session or session.get('role') != 'pegawai':
       return jsonify({'success': False, 'message': 'Please login to access this page.'})
   
   pegawai_id = session.get('id')
   email_tujuan = request.json.get('email')
   
   if not email_tujuan:
       return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
   
   libreoffice_cmd = check_libreoffice()
   if not libreoffice_cmd:
       return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
   
   temp_docx_path = None
   temp_pdf_path = None
   
   try:
       connection = get_db_connection()
       cursor = connection.cursor(dictionary=True)
       
       cursor.execute("""
           SELECT pc.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
           FROM pengajuan_cuti pc
           JOIN pegawai p ON pc.pegawai_id = p.pegawai_id
           WHERE pc.id_cuti = %s AND pc.pegawai_id = %s
       """, (id_cuti, pegawai_id))
       record = cursor.fetchone()
       
       if not record:
           return jsonify({'success': False, 'message': 'Data pengajuan cuti tidak ditemukan.'})
       
       cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
       sisa_cuti_result = cursor.fetchone()
       sisa_cuti_terakhir = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result else 0
       
       template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-cuti.docx')
       
       if not os.path.exists(template_path):
           return jsonify({'success': False, 'message': 'Template file tidak ditemukan.'})
       
       doc = Document(template_path)
       
       start_date = record['tanggal_cuti_mulai']
       end_date = record['tanggal_cuti_akhir']
       
       
       hari_cuti = calculate_cuti_days(
           record['pegawai_id'], 
           record['pegawai_nip'], 
           start_date, 
           end_date
       )
       
       kategori_nama = {
           'CT': 'Cuti Tahunan',
           'CTD': 'Cuti Mendadak', 
           'CTX': 'Cuti Extra'
       }
       
       data_pengganti = {
           'nama_personil': format_name(record['pegawai_nama']),
           'tgl_mulai': record['tanggal_cuti_mulai'].strftime('%d/%m/%Y') if record['tanggal_cuti_mulai'] else '-',
           'tgl_berakhir': record['tanggal_cuti_akhir'].strftime('%d/%m/%Y') if record['tanggal_cuti_akhir'] else '-',
           'tgl_pembuatan': record['tanggal_pengajuan'].strftime('%d/%m/%Y') if record['tanggal_pengajuan'] else '-',
           'tim_pesonil': record['tim'] or '-',
           'jumlah_cuti': f"{hari_cuti} hari",
           'kategori_cuti': kategori_nama.get(record['kategori_cuti'], record['kategori_cuti']) or '-',
           'alasan_cuti': record['alasan'] or '-',
           'personil_backup': format_name(record['personil_backup']),
           'sisa_cuti_terakhir': f"{sisa_cuti_terakhir} hari",
           'nama_mengetahui': record['nama_mengetahui'] or '-',
           'nama_menyetujui': record['nama_menyetujui'] or '-',
           'nomor_surat': record['nomor_surat'] or '-',
           'jabatan_mengetahui': get_jabatan_mengetahui(record['nama_mengetahui'])
       }
       
       def replace_text_in_paragraph(paragraph, replacements):
           if paragraph.text:
               full_text = paragraph.text
               for key, value in replacements.items():
                   if key in full_text:
                       full_text = full_text.replace(key, value)
               
               if full_text != paragraph.text:
                   for run in paragraph.runs:
                       run.clear()
                   
                   new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                   new_run.text = full_text
                   new_run.font.name = 'Times New Roman'
                   new_run.font.size = Pt(12)
       
       for paragraph in doc.paragraphs:
           replace_text_in_paragraph(paragraph, data_pengganti)
       
       for table in doc.tables:
           for row in table.rows:
               for cell in row.cells:
                   for paragraph in cell.paragraphs:
                       replace_text_in_paragraph(paragraph, data_pengganti)
       
       temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
       os.makedirs(temp_dir, exist_ok=True)
       
       filename_base = f"Form_Pengajuan_Cuti_{record['nomor_surat'].replace('/', '_')}"
       temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
       temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
       
       doc.save(temp_docx_path)
       
       try:
           abs_docx_path = os.path.abspath(temp_docx_path)
           abs_temp_dir = os.path.abspath(temp_dir)
           
           result = subprocess.run([
               libreoffice_cmd,
               "--headless",
               "--convert-to", "pdf",
               "--outdir", abs_temp_dir,
               abs_docx_path
           ], capture_output=True, text=True, timeout=60)
           
           if result.returncode != 0:
               return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
               
       except subprocess.TimeoutExpired:
           return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
       except Exception as e:
           return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
       
       import time
       time.sleep(2)
       
       if not os.path.exists(temp_pdf_path):
           return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
       
       with open(temp_pdf_path, 'rb') as pdf_file:
           pdf_content = pdf_file.read()
       
       EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
       PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
       
       subjek = f"Form Pengajuan Cuti - {record['pegawai_nama']} ({record['nomor_surat']})"
       pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Pengajuan Cuti</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form cuti <strong>{record['pegawai_nama']}</strong> :

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Kategori</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{kategori_nama.get(record['kategori_cuti'], record['kategori_cuti'])}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tanggal Cuti</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_cuti_mulai'].strftime('%d/%m/%Y')} s/d {record['tanggal_cuti_akhir'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Jumlah Hari</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{hari_cuti} hari</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tim</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tim']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form cutinya sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
       """
       
       berhasil = kirim_email_pdf(
           pengirim_email=EMAIL_PENGIRIM,
           pengirim_password=PASSWORD_APLIKASI,
           penerima_email=email_tujuan,
           subjek=subjek,
           pesan=pesan,
           pdf_content=pdf_content,
           filename=f"{filename_base}.pdf"
       )
       
       if berhasil:
           return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
       else:
           return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
       
   except Exception as e:
       return jsonify({'success': False, 'message': f'Error: {str(e)}'})
   finally:
       try:
           if temp_docx_path and os.path.exists(temp_docx_path):
               os.remove(temp_docx_path)
           if temp_pdf_path and os.path.exists(temp_pdf_path):
               os.remove(temp_pdf_path)
       except:
           pass
       
       if connection.is_connected():
           cursor.close()
           connection.close()

def kirim_email_pdf(pengirim_email, pengirim_password, penerima_email, subjek, pesan, pdf_content, filename):
   try:
       if not pengirim_email or not pengirim_password:
           raise ValueError('Configure SMTP_EMAIL and SMTP_PASSWORD before sending email')
       msg = MIMEMultipart()
       msg['From'] = pengirim_email
       msg['To'] = penerima_email
       msg['Subject'] = subjek
       
       msg.attach(MIMEText(pesan, 'html'))
       
       
       part = MIMEBase('application', 'pdf')
       part.set_payload(pdf_content)
       
       encoders.encode_base64(part)
       part.add_header(
           'Content-Disposition',
           f'attachment; filename= {filename}'
       )
       msg.attach(part)
       
       server = smtplib.SMTP('smtp.gmail.com', 587)
       server.starttls()
       server.login(pengirim_email, pengirim_password)
       
       text = msg.as_string()
       server.sendmail(pengirim_email, penerima_email, text)
       server.quit()
       
       return True
       
   except Exception as e:
       print(f"Error sending email: {str(e)}")
       return False





@app.route('/daftar-cuti')
def daftar_cuti():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    kategori_cuti = request.args.get('kategori_cuti')
    status_filter = request.args.get('status_filter')
    search_nama = request.args.get('search_nama')
    divisi_filter = request.args.get('divisi_filter')

    if not start_date:
        start_date = (date.today() - timedelta(days=30)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = date.today().strftime('%Y-%m-%d')

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT pc.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip, pb1.pembagian1_nama
            FROM pengajuan_cuti pc
            JOIN pegawai p ON pc.pegawai_id = p.pegawai_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE pc.tanggal_pengajuan BETWEEN %s AND %s
        """

        params = [start_date + ' 00:00:00', end_date + ' 23:59:59']

        if kategori_cuti:
            query += ' AND pc.kategori_cuti = %s'
            params.append(kategori_cuti)

        if status_filter:
            query += ' AND pc.status = %s'
            params.append(status_filter)

        if search_nama:
            search_string = f'%{search_nama}%'
            query += ' AND (p.pegawai_nama LIKE %s OR p.pegawai_nik_lintas LIKE %s)'
            params += [search_string, search_string]

        if divisi_filter:
            query += ' AND pb1.pembagian1_nama = %s'
            params.append(divisi_filter)

        query += ' ORDER BY pc.tanggal_pengajuan DESC'

        cursor.execute(query, params)
        cuti_records = cursor.fetchall()

        for record in cuti_records:
            start_date_obj = record['tanggal_cuti_mulai']
            end_date_obj = record['tanggal_cuti_akhir']
            
            
            hari_cuti = calculate_cuti_days(
                record['pegawai_id'], 
                record['pegawai_nip'], 
                start_date_obj, 
                end_date_obj
            )
            
            record['jumlah_hari'] = hari_cuti
            
            if record['tanggal_cuti_mulai']:
                record['tanggal_cuti_mulai'] = record['tanggal_cuti_mulai'].strftime('%d/%m/%Y')
            if record['tanggal_cuti_akhir']:
                record['tanggal_cuti_akhir'] = record['tanggal_cuti_akhir'].strftime('%d/%m/%Y')
            if record['tanggal_pengajuan']:
                record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')

        cursor.execute("SELECT DISTINCT pembagian1_nama FROM pembagian1 ORDER BY pembagian1_nama")
        divisi_list = cursor.fetchall()

    except Error as e:
        cuti_records = []
        divisi_list = []
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

    return render_template('daftar_cuti.html', 
                         cuti_records=cuti_records,
                         divisi_list=divisi_list,
                         start_date=start_date,
                         end_date=end_date,
                         kategori_cuti=kategori_cuti,
                         status_filter=status_filter,
                         search_nama=search_nama,
                         divisi_filter=divisi_filter)



@app.route('/update-status-cuti/<int:id_cuti>', methods=['POST'])
def update_status_cuti(id_cuti):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'})
    
    new_status = request.json.get('status')
    
    if new_status not in ['OK', 'Menunggu', 'Ditolak']:
        return jsonify({'success': False, 'message': 'Status tidak valid'})
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT pc.*, p.pegawai_nip FROM pengajuan_cuti pc JOIN pegawai p ON pc.pegawai_id = p.pegawai_id WHERE pc.id_cuti = %s", (id_cuti,))
        cuti_record = cursor.fetchone()
        
        if not cuti_record:
            return jsonify({'success': False, 'message': 'Data cuti tidak ditemukan'})
        
        old_status = cuti_record['status']
        
        
        cursor.execute("UPDATE pengajuan_cuti SET status = %s WHERE id_cuti = %s", (new_status, id_cuti))
        
        
        if old_status != new_status and cuti_record['kategori_cuti'] in ['CT', 'CTD']:
            hari_cuti = calculate_cuti_days(
                cuti_record['pegawai_id'], 
                cuti_record['pegawai_nip'], 
                cuti_record['tanggal_cuti_mulai'], 
                cuti_record['tanggal_cuti_akhir']
            )
            
            cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (cuti_record['pegawai_id'],))
            sisa_cuti_result = cursor.fetchone()
            sisa_cuti_saat_ini = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result else 0
            
            
            if old_status == 'OK' and new_status == 'Ditolak':
                sisa_cuti_baru = sisa_cuti_saat_ini + hari_cuti
                cursor.execute("UPDATE pegawai SET sisa_cuti = %s WHERE pegawai_id = %s", (sisa_cuti_baru, cuti_record['pegawai_id']))
            
            
            elif old_status == 'Ditolak' and new_status == 'OK':
                if sisa_cuti_saat_ini >= hari_cuti:
                    sisa_cuti_baru = sisa_cuti_saat_ini - hari_cuti
                    cursor.execute("UPDATE pegawai SET sisa_cuti = %s WHERE pegawai_id = %s", (sisa_cuti_baru, cuti_record['pegawai_id']))
                else:
                    return jsonify({'success': False, 'message': f'Sisa cuti tidak mencukupi. Dibutuhkan {hari_cuti} hari, tersedia {sisa_cuti_saat_ini} hari'})
            
            
            elif old_status == 'Menunggu' and new_status == 'OK':
                if sisa_cuti_saat_ini >= hari_cuti:
                    sisa_cuti_baru = sisa_cuti_saat_ini - hari_cuti
                    cursor.execute("UPDATE pegawai SET sisa_cuti = %s WHERE pegawai_id = %s", (sisa_cuti_baru, cuti_record['pegawai_id']))
                else:
                    return jsonify({'success': False, 'message': f'Sisa cuti tidak mencukupi. Dibutuhkan {hari_cuti} hari, tersedia {sisa_cuti_saat_ini} hari'})
            
            
            elif old_status == 'OK' and new_status == 'Menunggu':
                sisa_cuti_baru = sisa_cuti_saat_ini + hari_cuti
                cursor.execute("UPDATE pegawai SET sisa_cuti = %s WHERE pegawai_id = %s", (sisa_cuti_baru, cuti_record['pegawai_id']))
        
        connection.commit()
        return jsonify({'success': True, 'message': 'Status berhasil diupdate'})
        
    except Error as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/hapus-cuti-admin/<int:id_cuti>')
def hapus_cuti_admin(id_cuti):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT pc.*, p.pegawai_nip FROM pengajuan_cuti pc JOIN pegawai p ON pc.pegawai_id = p.pegawai_id WHERE pc.id_cuti = %s", (id_cuti,))
        cuti_record = cursor.fetchone()
        
        if not cuti_record:
            return redirect(url_for('daftar_cuti'))
        
        
        if cuti_record['kategori_cuti'] in ['CT', 'CTD'] and cuti_record['status'] == 'OK':
            hari_cuti = calculate_cuti_days(
                cuti_record['pegawai_id'], 
                cuti_record['pegawai_nip'], 
                cuti_record['tanggal_cuti_mulai'], 
                cuti_record['tanggal_cuti_akhir']
            )
            
            cursor.execute("SELECT sisa_cuti FROM pegawai WHERE pegawai_id = %s", (cuti_record['pegawai_id'],))
            sisa_cuti_result = cursor.fetchone()
            sisa_cuti_saat_ini = sisa_cuti_result['sisa_cuti'] if sisa_cuti_result else 0
            
            sisa_cuti_baru = sisa_cuti_saat_ini + hari_cuti
            cursor.execute("UPDATE pegawai SET sisa_cuti = %s WHERE pegawai_id = %s", (sisa_cuti_baru, cuti_record['pegawai_id']))
            
        else:
            pass
        
        cursor.execute("DELETE FROM pengajuan_cuti WHERE id_cuti = %s", (id_cuti,))
        connection.commit()
        
    except Error as e:
        pass
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return redirect(url_for('daftar_cuti'))




@app.route('/status-kehadiran', methods=['GET', 'POST'])
def status_kehadiran():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        pegawai_id = session.get('id')
        pegawai_nik_lintas = session.get('nik_lintas')
        pegawai_nip = session.get('nip')
        
        if not pegawai_id or not pegawai_nik_lintas or not pegawai_nip:
            return redirect(url_for('login'))
        
        nomor_id_bi = request.form['nomor_id_bi']
        kategori_alasan = request.form['kategori_alasan']
        tim = request.form['tim']
        alasan_detail = request.form['alasan_detail']
        nama_mengetahui = request.form['nama_mengetahui']
        nama_menyetujui = request.form['nama_menyetujui']
        tanggal_ketidakhadiran = request.form['tanggal_ketidakhadiran']
        
        
        if not nomor_id_bi.isdigit() or len(nomor_id_bi) > 6:
            return redirect(url_for('status_kehadiran'))
        
        ketidakhadiran_date = datetime.strptime(tanggal_ketidakhadiran, '%Y-%m-%d')

        
        batas_maksimal = datetime(2026, 7, 6)

        if ketidakhadiran_date > batas_maksimal:
            return redirect(url_for('status_kehadiran'))
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            today = datetime.now()
            nomor_surat = f"{pegawai_nik_lintas}/STATUS/{today.strftime('%d%m%Y')}"
            
            
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM status_kehadiran 
                WHERE pegawai_id = %s AND tanggal_ketidakhadiran = %s
            """, (pegawai_id, tanggal_ketidakhadiran))
            
            existing_count = cursor.fetchone()['count']
            if existing_count > 0:
                return redirect(url_for('status_kehadiran'))
            
            insert_query = """
                INSERT INTO status_kehadiran 
                (pegawai_id, pegawai_nip, nomor_surat, nomor_id_bi, kategori_alasan, tim, alasan_detail, 
                 nama_mengetahui, nama_menyetujui, tanggal_ketidakhadiran, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            cursor = connection.cursor()
            cursor.execute(insert_query, (
                pegawai_id, pegawai_nik_lintas, nomor_surat, nomor_id_bi, kategori_alasan, tim, alasan_detail,
                nama_mengetahui, nama_menyetujui, tanggal_ketidakhadiran, 'OK'
            ))
            
            id_status_baru = cursor.lastrowid
            connection.commit()
            
            session['pengajuan_berhasil'] = True
            session['id_status_baru'] = id_status_baru
            
            return redirect(url_for('status_kehadiran'))
            
        except Error as e:
            pass
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        pegawai_id = session.get('id')
        pegawai_nip = session.get('nip')
        
        cursor.execute("SELECT pembagian1_nama FROM pegawai p JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id WHERE p.pegawai_id = %s", (pegawai_id,))
        divisi_result = cursor.fetchone()
        divisi_user = divisi_result['pembagian1_nama'] if divisi_result else ''
        
        cursor.execute("SELECT * FROM tim WHERE pembagian1_nama = %s ORDER BY tim_nama", (divisi_user,))
        tim_list = cursor.fetchall()
        
        cursor.execute("SELECT * FROM ref_nama_lembur ORDER BY nama")
        nama_mengetahui_list = cursor.fetchall()
        
        cursor.execute("SELECT * FROM ref_nama_lembur WHERE nama = 'Demo Supervisor' ORDER BY nama")
        nama_menyetujui_list = cursor.fetchall()
        
    except Error as e:
        tim_list = []
        nama_mengetahui_list = []
        nama_menyetujui_list = []
        divisi_user = ''
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    pengajuan_berhasil = session.pop('pengajuan_berhasil', False)
    id_status_baru = session.pop('id_status_baru', None)
    
    pegawai_nik_lintas = session.get('nik_lintas', '')
    today = datetime.now()
    nomor_surat_preview = f"{pegawai_nik_lintas}/STATUS/{today.strftime('%d%m%Y')}"
    
    return render_template('status_kehadiran.html', 
                         nomor_surat_preview=nomor_surat_preview,
                         tim_list=tim_list,
                         nama_mengetahui_list=nama_mengetahui_list,
                         nama_menyetujui_list=nama_menyetujui_list,
                         pengajuan_berhasil=pengajuan_berhasil,
                         id_status_baru=id_status_baru,
                         divisi_user=divisi_user)





@app.route('/kirim-email-status-kehadiran/<int:id_status>', methods=['POST'])
def kirim_email_status_kehadiran(id_status):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'message': 'Please login to access this page.'})
    
    pegawai_id = session.get('id')
    email_tujuan = request.json.get('email')
    
    if not email_tujuan:
        return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT sk.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM status_kehadiran sk
            JOIN pegawai p ON sk.pegawai_id = p.pegawai_id
            WHERE sk.id_status_kehadiran = %s AND sk.pegawai_id = %s
        """, (id_status, pegawai_id))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({'success': False, 'message': 'Data status kehadiran tidak ditemukan.'})
        
        
        template_mapping = {
            'Sakit': 'template-status-kehadiran-sakit.docx',
            'Dinas': 'template-status-kehadiran-dinas.docx',
            'Bekerja Tidak Penuh (TP)': 'template-status-kehadiran-tp.docx',
            'Telambat (TL)': 'template-status-kehadiran-tl.docx',
            'Lain - Lain': 'template-status-kehadiran-lainnya.docx'
        }
        
        template_file = template_mapping.get(record['kategori_alasan'], 'template-status-kehadiran-lainnya.docx')
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), template_file)
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': f'Template file {template_file} tidak ditemukan.'})
        
        doc = Document(template_path)
        
        data_pengganti = {
            'nama_personil': format_name(record['pegawai_nama']),
            'no_id_bi': record['nomor_id_bi'] or '-',
            'tanggal_ketidakhadiran': record['tanggal_ketidakhadiran'].strftime('%d %B %Y') if record['tanggal_ketidakhadiran'] else '-',
            'alasan_detail': record['alasan_detail'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'jabatan_mengetahui': get_jabatan_mengetahui(record['nama_mengetahui'])
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Status_Kehadiran_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
                
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
        
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_content = pdf_file.read()
        
        EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
        PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
        
        subjek = f"Form Status Kehadiran - {record['pegawai_nama']} ({record['nomor_surat']})"
        pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Status Kehadiran</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form status kehadiran <strong>{record['pegawai_nama']}</strong> :

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor ID BI</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_id_bi']}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Kategori</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['kategori_alasan']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tanggal Ketidakhadiran</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_ketidakhadiran'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tim</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tim']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Detail Alasan</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['alasan_detail']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form status kehadirannya sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
        """
        
        berhasil = kirim_email_pdf(
            pengirim_email=EMAIL_PENGIRIM,
            pengirim_password=PASSWORD_APLIKASI,
            penerima_email=email_tujuan,
            subjek=subjek,
            pesan=pesan,
            pdf_content=pdf_content,
            filename=f"{filename_base}.pdf"
        )
        
        if berhasil:
            return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
        else:
            return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()





@app.route('/download-status-kehadiran/<int:id_status>')
def download_status_kehadiran(id_status):
    if 'loggedin' not in session:
        return redirect(url_for('login'))
    
    user_role = session.get('role')
    if user_role not in ['admin', 'pegawai']:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))
    
    if user_role == 'pegawai':
        pegawai_id = session.get('id')
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute("SELECT pegawai_id FROM status_kehadiran WHERE id_status_kehadiran = %s", (id_status,))
            status_check = cursor.fetchone()
            
            if not status_check or status_check['pegawai_id'] != pegawai_id:
                return redirect(url_for('status_kehadiran_saya'))
                
        except Error as e:
            return redirect(url_for('status_kehadiran_saya'))
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
        return redirect(url_for(redirect_url))
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT sk.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM status_kehadiran sk
            JOIN pegawai p ON sk.pegawai_id = p.pegawai_id
            WHERE sk.id_status_kehadiran = %s
        """, (id_status,))
        record = cursor.fetchone()
        
        if not record:
            redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
            return redirect(url_for(redirect_url))
        
        
        template_mapping = {
            'Sakit': 'template-status-kehadiran-sakit.docx',
            'Dinas': 'template-status-kehadiran-dinas.docx',
            'Bekerja Tidak Penuh (TP)': 'template-status-kehadiran-tp.docx',
            'Telambat (TL)': 'template-status-kehadiran-tl.docx',
            'Lain - Lain': 'template-status-kehadiran-lainnya.docx'
        }
        
        template_file = template_mapping.get(record['kategori_alasan'], 'template-status-kehadiran-lainnya.docx')
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), template_file)
        
        if not os.path.exists(template_path):
            redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
            return redirect(url_for(redirect_url))
        
        doc = Document(template_path)
        
        data_pengganti = {
            'nama_personil': format_name(record['pegawai_nama']),
            'no_id_bi': record['nomor_id_bi'] or '-',
            'tanggal_ketidakhadiran': record['tanggal_ketidakhadiran'].strftime('%d %B %Y') if record['tanggal_ketidakhadiran'] else '-',
            'alasan_detail': record['alasan_detail'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'jabatan_mengetahui': get_jabatan_mengetahui(record['nama_mengetahui'])
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Status_Kehadiran_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
                return redirect(url_for(redirect_url))
                
        except subprocess.TimeoutExpired:
            redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
            return redirect(url_for(redirect_url))
        except Exception as e:
            redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
            return redirect(url_for(redirect_url))
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
            return redirect(url_for(redirect_url))
        
        filename_pdf = f"{filename_base}.pdf"
        return send_file(temp_pdf_path, download_name=filename_pdf, as_attachment=True, 
                        mimetype='application/pdf')
        
    except Exception as e:
        redirect_url = 'status_kehadiran_saya' if user_role == 'pegawai' else 'daftar_status_kehadiran'
        return redirect(url_for(redirect_url))
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                import time
                time.sleep(1)
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()






@app.route('/kirim-email-status/<int:id_status>', methods=['POST'])
def kirim_email_status(id_status):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'message': 'Please login to access this page.'})
    
    pegawai_id = session.get('id')
    email_tujuan = request.json.get('email')
    
    if not email_tujuan:
        return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT sk.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM status_kehadiran sk
            JOIN pegawai p ON sk.pegawai_id = p.pegawai_id
            WHERE sk.id_status_kehadiran = %s AND sk.pegawai_id = %s
        """, (id_status, pegawai_id))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({'success': False, 'message': 'Data status kehadiran tidak ditemukan.'})
        
        
        template_mapping = {
            'Sakit': 'template-status-kehadiran-sakit.docx',
            'Dinas': 'template-status-kehadiran-dinas.docx',
            'Bekerja Tidak Penuh (TP)': 'template-status-kehadiran-tp.docx',
            'Telambat (TL)': 'template-status-kehadiran-tl.docx',
            'Lain - Lain': 'template-status-kehadiran-lainnya.docx'
        }
        
        template_file = template_mapping.get(record['kategori_alasan'], 'template-status-kehadiran-lainnya.docx')
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), template_file)
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': f'Template file {template_file} tidak ditemukan.'})
        
        doc = Document(template_path)
        
        data_pengganti = {
            'nama_personil': format_name(record['pegawai_nama']),
            'no_id_bi': record['nomor_id_bi'] or '-',
            'tanggal_ketidakhadiran': record['tanggal_ketidakhadiran'].strftime('%d %B %Y') if record['tanggal_ketidakhadiran'] else '-',
            'alasan_detail': record['alasan_detail'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'jabatan_mengetahui': get_jabatan_mengetahui(record['nama_mengetahui'])
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Status_Kehadiran_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
                
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
        
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_content = pdf_file.read()
        
        EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
        PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
        
        subjek = f"Form Status Kehadiran - {record['pegawai_nama']} ({record['nomor_surat']})"
        pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Status Kehadiran</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form status kehadiran <strong>{record['pegawai_nama']}</strong> :

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor ID BI</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_id_bi']}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Kategori</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['kategori_alasan']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tanggal Ketidakhadiran</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_ketidakhadiran'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tim</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tim']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Detail Alasan</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['alasan_detail']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form status kehadirannya sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
        """
        
        berhasil = kirim_email_pdf(
            pengirim_email=EMAIL_PENGIRIM,
            pengirim_password=PASSWORD_APLIKASI,
            penerima_email=email_tujuan,
            subjek=subjek,
            pesan=pesan,
            pdf_content=pdf_content,
            filename=f"{filename_base}.pdf"
        )
        
        if berhasil:
            return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
        else:
            return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/daftar-status-kehadiran')
def daftar_status_kehadiran():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    kategori_alasan = request.args.get('kategori_alasan')
    status_filter = request.args.get('status_filter')
    search_nama = request.args.get('search_nama')
    divisi_filter = request.args.get('divisi_filter')

    if not start_date:
        start_date = (date.today() - timedelta(days=30)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = date.today().strftime('%Y-%m-%d')

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT sk.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip, pb1.pembagian1_nama
            FROM status_kehadiran sk
            JOIN pegawai p ON sk.pegawai_id = p.pegawai_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE sk.tanggal_pengajuan BETWEEN %s AND %s
        """

        params = [start_date + ' 00:00:00', end_date + ' 23:59:59']

        if kategori_alasan:
            query += ' AND sk.kategori_alasan = %s'
            params.append(kategori_alasan)

        if status_filter:
            query += ' AND sk.status = %s'
            params.append(status_filter)

        if search_nama:
            search_string = f'%{search_nama}%'
            query += ' AND (p.pegawai_nama LIKE %s OR p.pegawai_nik_lintas LIKE %s)'
            params += [search_string, search_string]

        if divisi_filter:
            query += ' AND pb1.pembagian1_nama = %s'
            params.append(divisi_filter)

        query += ' ORDER BY sk.tanggal_pengajuan DESC'

        cursor.execute(query, params)
        status_records = cursor.fetchall()

        for record in status_records:
            if record['tanggal_ketidakhadiran']:
                record['tanggal_ketidakhadiran'] = record['tanggal_ketidakhadiran'].strftime('%d/%m/%Y')
            if record['tanggal_pengajuan']:
                record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')

        cursor.execute("SELECT DISTINCT pembagian1_nama FROM pembagian1 ORDER BY pembagian1_nama")
        divisi_list = cursor.fetchall()

    except Error as e:
        status_records = []
        divisi_list = []
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

    return render_template('daftar_status_kehadiran.html', 
                         status_records=status_records,
                         divisi_list=divisi_list,
                         start_date=start_date,
                         end_date=end_date,
                         kategori_alasan=kategori_alasan,
                         status_filter=status_filter,
                         search_nama=search_nama,
                         divisi_filter=divisi_filter)

@app.route('/update-status-kehadiran/<int:id_status>', methods=['POST'])
def update_status_kehadiran(id_status):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'})
    
    new_status = request.json.get('status')
    
    if new_status not in ['OK', 'Menunggu', 'Ditolak']:
        return jsonify({'success': False, 'message': 'Status tidak valid'})
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM status_kehadiran WHERE id_status_kehadiran = %s", (id_status,))
        status_record = cursor.fetchone()
        
        if not status_record:
            return jsonify({'success': False, 'message': 'Data status kehadiran tidak ditemukan'})
        
        cursor.execute("UPDATE status_kehadiran SET status = %s WHERE id_status_kehadiran = %s", (new_status, id_status))
        connection.commit()
        
        return jsonify({'success': True, 'message': 'Status berhasil diupdate'})
        
    except Error as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/hapus-status-kehadiran-admin/<int:id_status>')
def hapus_status_kehadiran_admin(id_status):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM status_kehadiran WHERE id_status_kehadiran = %s", (id_status,))
        status_record = cursor.fetchone()
        
        if not status_record:
            return redirect(url_for('daftar_status_kehadiran'))
        
        cursor.execute("DELETE FROM status_kehadiran WHERE id_status_kehadiran = %s", (id_status,))
        connection.commit()
        
    except Error as e:
        pass
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return redirect(url_for('daftar_status_kehadiran'))


@app.route('/status-kehadiran-saya')
def status_kehadiran_saya():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))
    
    pegawai_id = session.get('id')
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT sk.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip
            FROM status_kehadiran sk
            JOIN pegawai p ON sk.pegawai_id = p.pegawai_id
            WHERE sk.pegawai_id = %s
            ORDER BY sk.tanggal_pengajuan DESC
        """
        
        cursor.execute(query, (pegawai_id,))
        status_records = cursor.fetchall()
        
        for record in status_records:
            if record['tanggal_ketidakhadiran']:
                record['tanggal_ketidakhadiran'] = record['tanggal_ketidakhadiran'].strftime('%d/%m/%Y')
            if record['tanggal_pengajuan']:
                record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')
        
    except Error as e:
        status_records = []
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return render_template('status_kehadiran_saya.html', status_records=status_records)


@app.route('/tukar-shift', methods=['GET', 'POST'])
def tukar_shift():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        pegawai_id = session.get('id')
        pegawai_nik_lintas = session.get('nik_lintas')
        pegawai_nip = session.get('nip')
        
        if not pegawai_id or not pegawai_nik_lintas or not pegawai_nip:
            return redirect(url_for('login'))
        
        tim = request.form['tim']
        tanggal_mulai = request.form['tanggal_mulai']
        tanggal_akhir = request.form['tanggal_akhir']
        shift_awal_id = request.form['shift_awal_id']
        shift_baru_id = request.form['shift_baru_id']
        pegawai_tukar_id = request.form['pegawai_tukar_id']
        alasan_tukar_shift = request.form['alasan_tukar_shift']
        nama_mengetahui = request.form['nama_mengetahui']
        nama_menyetujui = request.form['nama_menyetujui']
        
        
        tanggal_mulai_date = datetime.strptime(tanggal_mulai, '%Y-%m-%d')
        tanggal_akhir_date = datetime.strptime(tanggal_akhir, '%Y-%m-%d')
        
        if tanggal_akhir_date < tanggal_mulai_date:
            return redirect(url_for('tukar_shift'))
        
        
        batas_maksimal = datetime(2026, 7, 6)
        
        if tanggal_mulai_date > batas_maksimal or tanggal_akhir_date > batas_maksimal:
            return redirect(url_for('tukar_shift'))
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            today = datetime.now()
            nomor_surat = f"{pegawai_nik_lintas}/TUKAR/{today.strftime('%d%m%Y')}"
            
            
            cursor.execute("SELECT jk_name FROM jam_kerja WHERE jk_id = %s", (shift_awal_id,))
            shift_awal_result = cursor.fetchone()
            shift_awal_nama = shift_awal_result['jk_name'] if shift_awal_result else ''
            
            cursor.execute("SELECT jk_name FROM jam_kerja WHERE jk_id = %s", (shift_baru_id,))
            shift_baru_result = cursor.fetchone()
            shift_baru_nama = shift_baru_result['jk_name'] if shift_baru_result else ''
            
            
            cursor.execute("SELECT pegawai_nip, pegawai_nama FROM pegawai WHERE pegawai_id = %s", (pegawai_tukar_id,))
            pegawai_tukar_result = cursor.fetchone()
            pegawai_tukar_nip = pegawai_tukar_result['pegawai_nip'] if pegawai_tukar_result else ''
            pegawai_tukar_nama = pegawai_tukar_result['pegawai_nama'] if pegawai_tukar_result else ''
            
            
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM pengajuan_tukar_shift 
                WHERE pegawai_id = %s 
                AND ((tanggal_mulai BETWEEN %s AND %s) OR (tanggal_akhir BETWEEN %s AND %s))
            """, (pegawai_id, tanggal_mulai, tanggal_akhir, tanggal_mulai, tanggal_akhir))
            
            existing_count = cursor.fetchone()['count']
            if existing_count > 0:
                return redirect(url_for('tukar_shift'))
            
            insert_query = """
                INSERT INTO pengajuan_tukar_shift 
                (pegawai_id, pegawai_nip, pegawai_nik_lintas, nomor_surat, tim, tanggal_mulai, tanggal_akhir,
                 shift_awal_id, shift_awal_nama, shift_baru_id, shift_baru_nama, pegawai_tukar_id, 
                 pegawai_tukar_nip, pegawai_tukar_nama, alasan_tukar_shift, nama_mengetahui, nama_menyetujui, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            
            cursor = connection.cursor()
            cursor.execute(insert_query, (
                pegawai_id, pegawai_nip, pegawai_nik_lintas, nomor_surat, tim, tanggal_mulai, tanggal_akhir,
                shift_awal_id, shift_awal_nama, shift_baru_id, shift_baru_nama, pegawai_tukar_id,
                pegawai_tukar_nip, pegawai_tukar_nama, alasan_tukar_shift, nama_mengetahui, nama_menyetujui, 'OK'
            ))
            
            id_tukar_baru = cursor.lastrowid
            connection.commit()
            
            session['pengajuan_berhasil'] = True
            session['id_tukar_baru'] = id_tukar_baru
            
            return redirect(url_for('tukar_shift'))
            
        except Error as e:
            pass
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        pegawai_id = session.get('id')
        pegawai_nip = session.get('nip')
        
        cursor.execute("SELECT pembagian1_nama FROM pegawai p JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id WHERE p.pegawai_id = %s", (pegawai_id,))
        divisi_result = cursor.fetchone()
        divisi_user = divisi_result['pembagian1_nama'] if divisi_result else ''
        
        cursor.execute("SELECT * FROM tim WHERE pembagian1_nama = %s ORDER BY tim_nama", (divisi_user,))
        tim_list = cursor.fetchall()
        
        cursor.execute("SELECT * FROM ref_nama_lembur ORDER BY nama")
        nama_mengetahui_list = cursor.fetchall()
        
        cursor.execute("SELECT * FROM ref_nama_lembur WHERE nama = 'Demo Supervisor' ORDER BY nama")
        nama_menyetujui_list = cursor.fetchall()
        
        cursor.execute("SELECT jk_id, jk_name FROM jam_kerja ORDER BY jk_name")
        shift_list = cursor.fetchall()
        
        cursor.execute("SELECT pegawai_id, pegawai_nama, pegawai_nip FROM pegawai WHERE pegawai_status = 1 AND pegawai_id != %s AND pegawai_id != 1 ORDER BY pegawai_nama", (pegawai_id,))
        pegawai_list = cursor.fetchall()
        
    except Error as e:
        tim_list = []
        nama_mengetahui_list = []
        nama_menyetujui_list = []
        shift_list = []
        pegawai_list = []
        divisi_user = ''
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    pengajuan_berhasil = session.pop('pengajuan_berhasil', False)
    id_tukar_baru = session.pop('id_tukar_baru', None)
    
    pegawai_nik_lintas = session.get('nik_lintas', '')
    today = datetime.now()
    nomor_surat_preview = f"{pegawai_nik_lintas}/TUKAR/{today.strftime('%d%m%Y')}"
    
    return render_template('tukar_shift.html', 
                         nomor_surat_preview=nomor_surat_preview,
                         tim_list=tim_list,
                         nama_mengetahui_list=nama_mengetahui_list,
                         nama_menyetujui_list=nama_menyetujui_list,
                         shift_list=shift_list,
                         pegawai_list=pegawai_list,
                         pengajuan_berhasil=pengajuan_berhasil,
                         id_tukar_baru=id_tukar_baru,
                         divisi_user=divisi_user)





@app.route('/kirim-email-tukar-shift/<int:id_tukar_shift>', methods=['POST'])
def kirim_email_tukar_shift(id_tukar_shift):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'message': 'Please login to access this page.'})
    
    pegawai_id = session.get('id')
    email_tujuan = request.json.get('email')
    
    if not email_tujuan:
        return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pts.*, p.pegawai_nama
            FROM pengajuan_tukar_shift pts
            JOIN pegawai p ON pts.pegawai_id = p.pegawai_id
            WHERE pts.id_tukar_shift = %s AND pts.pegawai_id = %s
        """, (id_tukar_shift, pegawai_id))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({'success': False, 'message': 'Data pengajuan tukar shift tidak ditemukan.'})
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-tukar-shift.docx')
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': 'Template file tidak ditemukan.'})
        
        doc = Document(template_path)
        
        data_pengganti = {
            'nama_pegawai': format_name(record['pegawai_nama']),
            'nik_pegawai': record['pegawai_nik_lintas'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'tim': record['tim'] or '-',
            'tanggal_mulai': record['tanggal_mulai'].strftime('%d/%m/%Y') if record['tanggal_mulai'] else '-',
            'tanggal_akhir': record['tanggal_akhir'].strftime('%d/%m/%Y') if record['tanggal_akhir'] else '-',
            'shift_awal_nama': record['shift_awal_nama'] or '-',
            'shift_baru_nama': record['shift_baru_nama'] or '-',
            'pegawai_tukar_nama': format_name(record['pegawai_tukar_nama']),
            'pegawai_tukar_nip': record['pegawai_tukar_nip'] or '-',
            'alasan_tukar_shift': record['alasan_tukar_shift'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'tanggal_pengajuan': record['tanggal_pengajuan'].strftime('%d/%m/%Y') if record['tanggal_pengajuan'] else '-'
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Tukar_Shift_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
                
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
        
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_content = pdf_file.read()
        
        EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
        PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
        
        subjek = f"Form Pengajuan Tukar Shift - {record['pegawai_nama']} ({record['nomor_surat']})"
        pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Pengajuan Tukar Shift</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form tukar shift <strong>{record['pegawai_nama']}</strong> :
       </p>

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Periode Tukar</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_mulai'].strftime('%d/%m/%Y')} s/d {record['tanggal_akhir'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Shift Awal</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['shift_awal_nama']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Shift Baru</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['shift_baru_nama']}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Pegawai Tukar</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['pegawai_tukar_nama']} ({record['pegawai_tukar_nip']})</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tim</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tim']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form tukar shift sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
        """
        
        berhasil = kirim_email_pdf(
            pengirim_email=EMAIL_PENGIRIM,
            pengirim_password=PASSWORD_APLIKASI,
            penerima_email=email_tujuan,
            subjek=subjek,
            pesan=pesan,
            pdf_content=pdf_content,
            filename=f"{filename_base}.pdf"
        )
        
        if berhasil:
            return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
        else:
            return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()



@app.route('/kirim-email-tukar-shift-simple/<int:id_tukar_shift>', methods=['POST'])
def kirim_email_tukar_shift_simple(id_tukar_shift):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'message': 'Please login to access this page.'})
    
    pegawai_id = session.get('id')
    email_tujuan = request.json.get('email')
    
    if not email_tujuan:
        return jsonify({'success': False, 'message': 'Email tujuan harus diisi.'})
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        return jsonify({'success': False, 'message': 'LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.'})
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pts.*, p.pegawai_nama
            FROM pengajuan_tukar_shift pts
            JOIN pegawai p ON pts.pegawai_id = p.pegawai_id
            WHERE pts.id_tukar_shift = %s AND pts.pegawai_id = %s
        """, (id_tukar_shift, pegawai_id))
        record = cursor.fetchone()
        
        if not record:
            return jsonify({'success': False, 'message': 'Data pengajuan tukar shift tidak ditemukan.'})
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-tukar-shift.docx')
        
        if not os.path.exists(template_path):
            return jsonify({'success': False, 'message': 'Template file tidak ditemukan.'})
        
        doc = Document(template_path)
        
        data_pengganti = {
            'nama_pegawai': format_name(record['pegawai_nama']),
            'nik_pegawai': record['pegawai_nik_lintas'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'tim': record['tim'] or '-',
            'tanggal_mulai': record['tanggal_mulai'].strftime('%d/%m/%Y') if record['tanggal_mulai'] else '-',
            'tanggal_akhir': record['tanggal_akhir'].strftime('%d/%m/%Y') if record['tanggal_akhir'] else '-',
            'shift_awal_nama': record['shift_awal_nama'] or '-',
            'shift_baru_nama': record['shift_baru_nama'] or '-',
            'pegawai_tukar_nama': format_name(record['pegawai_tukar_nama']),
            'pegawai_tukar_nip': record['pegawai_tukar_nip'] or '-',
            'alasan_tukar_shift': record['alasan_tukar_shift'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'tanggal_pengajuan': record['tanggal_pengajuan'].strftime('%d/%m/%Y') if record['tanggal_pengajuan'] else '-'
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Tukar_Shift_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                return jsonify({'success': False, 'message': f'Gagal konversi PDF: {result.stderr}'})
                
        except subprocess.TimeoutExpired:
            return jsonify({'success': False, 'message': 'Timeout saat konversi PDF. Silakan coba lagi.'})
        except Exception as e:
            return jsonify({'success': False, 'message': f'Error konversi PDF: {str(e)}'})
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            return jsonify({'success': False, 'message': 'File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.'})
        
        with open(temp_pdf_path, 'rb') as pdf_file:
            pdf_content = pdf_file.read()
        
        EMAIL_PENGIRIM = os.environ.get('SMTP_EMAIL', '')
        PASSWORD_APLIKASI = os.environ.get('SMTP_PASSWORD', '')
        
        subjek = f"Form Pengajuan Tukar Shift - {record['pegawai_nama']} ({record['nomor_surat']})"
        pesan = f"""
<html>
 <body style="font-family: Arial, sans-serif; background-color: #f5f5f5; margin: 0; padding: 20px;">
   <div style="max-width: 600px; margin: 0 auto; background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;">
     
     <div style="background-color: #4a90e2; padding: 20px; text-align: center;">
       <h2 style="color: #ffffff; margin: 0;">Form Pengajuan Tukar Shift</h2>
     </div>
     
     <div style="padding: 25px;">
       <p style="font-size: 14px; color: #333; margin-bottom: 15px;">
         Salam,
       </p>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         Berikut form tukar shift <strong>{record['pegawai_nama']}</strong> :
       </p>

       <table style="width: 100%; border: 1px solid #ddd; border-collapse: collapse; margin-bottom: 20px;">
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Nomor Surat</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['nomor_surat']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Periode Tukar</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tanggal_mulai'].strftime('%d/%m/%Y')} s/d {record['tanggal_akhir'].strftime('%d/%m/%Y')}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Shift Awal</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['shift_awal_nama']}</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Shift Baru</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['shift_baru_nama']}</td>
         </tr>
         <tr style="background-color: #f9f9f9;">
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Pegawai Tukar</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['pegawai_tukar_nama']} ({record['pegawai_tukar_nip']})</td>
         </tr>
         <tr>
           <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Tim</td>
           <td style="padding: 8px; border: 1px solid #ddd;">{record['tim']}</td>
         </tr>
       </table>

       <p style="font-size: 14px; color: #333; margin-bottom: 20px;">
         File form tukar shift sudah dilampirkan dalam bentuk PDF
       </p>

       <p style="font-size: 14px; color: #333; margin-top: 30px;">
         Thanks,<br>
         <strong>Tim APBI</strong>
       </p>
     </div>
     
     <div style="background-color: #f0f0f0; padding: 15px; text-align: center; border-top: 1px solid #ddd;">
       <p style="font-size: 12px; color: #666; margin: 0;">
         Email ini dikirim otomatis dari sistem
       </p>
     </div>
     
   </div>
 </body>
</html>
        """
        
        berhasil = kirim_email_pdf(
            pengirim_email=EMAIL_PENGIRIM,
            pengirim_password=PASSWORD_APLIKASI,
            penerima_email=email_tujuan,
            subjek=subjek,
            pesan=pesan,
            pdf_content=pdf_content,
            filename=f"{filename_base}.pdf"
        )
        
        if berhasil:
            return jsonify({'success': True, 'message': f'Email berhasil dikirim ke {email_tujuan}'})
        else:
            return jsonify({'success': False, 'message': 'Gagal mengirim email. Silakan coba lagi.'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/download-tukar-shift/<int:id_tukar_shift>')
def download_tukar_shift(id_tukar_shift):
    if 'loggedin' not in session:
        flash('Please login to access this page.', 'warning')
        return redirect(url_for('login'))
    
    user_role = session.get('role')
    if user_role not in ['admin', 'pegawai']:
        flash('Unauthorized access.', 'error')
        return redirect(url_for('login'))
    
    if user_role == 'pegawai':
        pegawai_id = session.get('id')
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            cursor.execute("SELECT pegawai_id FROM pengajuan_tukar_shift WHERE id_tukar_shift = %s", (id_tukar_shift,))
            tukar_check = cursor.fetchone()
            
            if not tukar_check or tukar_check['pegawai_id'] != pegawai_id:
                flash('Data pengajuan tukar shift tidak ditemukan atau bukan milik Anda.', 'error')
                return redirect(url_for('tukar_shift_saya'))
                
        except Error as e:
            flash(f'Error: {e}', 'error')
            return redirect(url_for('tukar_shift_saya'))
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
    
    libreoffice_cmd = check_libreoffice()
    if not libreoffice_cmd:
        flash('LibreOffice tidak terinstall. Silakan install LibreOffice terlebih dahulu.', 'error')
        redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
        return redirect(url_for(redirect_url))
    
    temp_docx_path = None
    temp_pdf_path = None
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT pts.*, p.pegawai_nama
            FROM pengajuan_tukar_shift pts
            JOIN pegawai p ON pts.pegawai_id = p.pegawai_id
            WHERE pts.id_tukar_shift = %s
        """, (id_tukar_shift,))
        record = cursor.fetchone()
        
        if not record:
            flash('Data pengajuan tukar shift tidak ditemukan.', 'error')
            redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
            return redirect(url_for(redirect_url))
        
        template_path = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'template-tukar-shift.docx')
        
        if not os.path.exists(template_path):
            flash('Template file template-tukar-shift.docx tidak ditemukan.', 'error')
            redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
            return redirect(url_for(redirect_url))
        
        doc = Document(template_path)
        
        data_pengganti = {
            'nama_pegawai': format_name(record['pegawai_nama']),
            'nik_pegawai': record['pegawai_nik_lintas'] or '-',
            'nomor_surat': record['nomor_surat'] or '-',
            'tim': record['tim'] or '-',
            'tanggal_mulai': record['tanggal_mulai'].strftime('%d/%m/%Y') if record['tanggal_mulai'] else '-',
            'tanggal_akhir': record['tanggal_akhir'].strftime('%d/%m/%Y') if record['tanggal_akhir'] else '-',
            'shift_awal_nama': record['shift_awal_nama'] or '-',
            'shift_baru_nama': record['shift_baru_nama'] or '-',
            'pegawai_tukar_nama': format_name(record['pegawai_tukar_nama']),
            'pegawai_tukar_nip': record['pegawai_tukar_nip'] or '-',
            'alasan_tukar_shift': record['alasan_tukar_shift'] or '-',
            'nama_mengetahui': record['nama_mengetahui'] or '-',
            'nama_menyetujui': record['nama_menyetujui'] or '-',
            'tanggal_pengajuan': record['tanggal_pengajuan'].strftime('%d %B %Y') if record['tanggal_pengajuan'] else '-'
        }
        
        def replace_text_in_paragraph(paragraph, replacements):
            if paragraph.text:
                full_text = paragraph.text
                for key, value in replacements.items():
                    if key in full_text:
                        full_text = full_text.replace(key, value)
                
                if full_text != paragraph.text:
                    for run in paragraph.runs:
                        run.clear()
                    
                    new_run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
                    new_run.text = full_text
                    new_run.font.name = 'Times New Roman'
                    new_run.font.size = Pt(12)
        
        for paragraph in doc.paragraphs:
            replace_text_in_paragraph(paragraph, data_pengganti)
        
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        replace_text_in_paragraph(paragraph, data_pengganti)
        
        temp_dir = os.path.join(app.config.get('FILES_FOLDER', 'files'), 'temp')
        os.makedirs(temp_dir, exist_ok=True)
        
        filename_base = f"Form_Pengajuan_Tukar_Shift_{record['nomor_surat'].replace('/', '_')}"
        temp_docx_path = os.path.join(temp_dir, f"{filename_base}.docx")
        temp_pdf_path = os.path.join(temp_dir, f"{filename_base}.pdf")
        
        doc.save(temp_docx_path)
        
        try:
            abs_docx_path = os.path.abspath(temp_docx_path)
            abs_temp_dir = os.path.abspath(temp_dir)
            
            result = subprocess.run([
                libreoffice_cmd,
                "--headless",
                "--convert-to", "pdf",
                "--outdir", abs_temp_dir,
                abs_docx_path
            ], capture_output=True, text=True, timeout=60)
            
            if result.returncode != 0:
                flash(f'Gagal konversi PDF: {result.stderr}', 'error')
                redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
                return redirect(url_for(redirect_url))
                
        except subprocess.TimeoutExpired:
            flash('Timeout saat konversi PDF. Silakan coba lagi.', 'error')
            redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
            return redirect(url_for(redirect_url))
        except Exception as e:
            flash(f'Error konversi PDF: {str(e)}', 'error')
            redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
            return redirect(url_for(redirect_url))
        
        import time
        time.sleep(2)
        
        if not os.path.exists(temp_pdf_path):
            flash('File PDF tidak berhasil dibuat. Periksa instalasi LibreOffice.', 'error')
            redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
            return redirect(url_for(redirect_url))
        
        filename_pdf = f"{filename_base}.pdf"
        return send_file(temp_pdf_path, download_name=filename_pdf, as_attachment=True, 
                        mimetype='application/pdf')
        
    except Exception as e:
        flash(f'Error generating PDF: {e}', 'error')
        redirect_url = 'tukar_shift_saya' if user_role == 'pegawai' else 'daftar_tukar_shift'
        return redirect(url_for(redirect_url))
    finally:
        try:
            if temp_docx_path and os.path.exists(temp_docx_path):
                os.remove(temp_docx_path)
            if temp_pdf_path and os.path.exists(temp_pdf_path):
                import time
                time.sleep(1)
                os.remove(temp_pdf_path)
        except:
            pass
        
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/tukar-shift-saya')
def tukar_shift_saya():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        flash('Coba refresh page, atau login ulang', 'warning')
        return redirect(url_for('login'))
    
    pegawai_id = session.get('id')
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        query = """
            SELECT pts.*, p.pegawai_nama
            FROM pengajuan_tukar_shift pts
            JOIN pegawai p ON pts.pegawai_id = p.pegawai_id
            WHERE pts.pegawai_id = %s
            ORDER BY pts.tanggal_pengajuan DESC
        """
        
        cursor.execute(query, (pegawai_id,))
        tukar_shift_records = cursor.fetchall()
        
        for record in tukar_shift_records:
            if record['tanggal_mulai']:
                record['tanggal_mulai'] = record['tanggal_mulai'].strftime('%d/%m/%Y')
            if record['tanggal_akhir']:
                record['tanggal_akhir'] = record['tanggal_akhir'].strftime('%d/%m/%Y')
            if record['tanggal_pengajuan']:
                record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')
        
    except Error as e:
        flash(f'Error loading data: {e}', 'error')
        tukar_shift_records = []
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return render_template('tukar_shift_saya.html', tukar_shift_records=tukar_shift_records)

from difflib import SequenceMatcher

def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

@app.route('/upload_sc', methods=['GET', 'POST'])
def upload_sc():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        file_nik = request.files.get('file_nik')
        file_nama = request.files.get('file_nama')
        
        if not file_nik or not file_nama:
            return redirect(url_for('upload_sc'))
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            
            cursor.execute("UPDATE pegawai SET sisa_cuti = 0 WHERE pegawai_status != 0")
            
            
            df_nik = pd.read_excel(file_nik)
            updated_nik = 0
            not_found_nik = []
            
            for _, row in df_nik.iterrows():
                nik = str(row['NIK']).strip()
                sisa_cuti = int(row['Sisa Cuti']) if pd.notna(row['Sisa Cuti']) else 0
                
                cursor.execute("""
                    UPDATE pegawai 
                    SET sisa_cuti = %s 
                    WHERE pegawai_nik_lintas = %s
                """, (sisa_cuti, nik))
                
                if cursor.rowcount > 0:
                    updated_nik += 1
                else:
                    not_found_nik.append(nik)
            
            
            df_nama = pd.read_excel(file_nama)
            
            
            cursor.execute("SELECT pegawai_nama FROM pegawai WHERE pegawai_status != 0")
            db_names = [row['pegawai_nama'] for row in cursor.fetchall()]
            
            updated_nama = 0
            not_found_nama = []
            similarity_matches = []
            
            for _, row in df_nama.iterrows():
                excel_nama = str(row['Nama']).strip()
                sisa_cuti = int(row['Sisa Cuti']) if pd.notna(row['Sisa Cuti']) else 0
                
                
                exact_match = False
                for db_nama in db_names:
                    if excel_nama.lower() == db_nama.lower():
                        cursor.execute("""
                            UPDATE pegawai 
                            SET sisa_cuti = %s 
                            WHERE LOWER(pegawai_nama) = %s
                        """, (sisa_cuti, excel_nama.lower()))
                        updated_nama += 1
                        exact_match = True
                        break
                
                
                if not exact_match:
                    best_match = None
                    best_score = 0
                    
                    for db_nama in db_names:
                        score = similarity(excel_nama, db_nama)
                        if score > best_score and score >= 0.8:  
                            best_score = score
                            best_match = db_nama
                    
                    if best_match:
                        cursor.execute("""
                            UPDATE pegawai 
                            SET sisa_cuti = %s 
                            WHERE pegawai_nama = %s
                        """, (sisa_cuti, best_match))
                        updated_nama += 1
                        similarity_matches.append(f"{excel_nama} → {best_match} ({best_score:.2%})")
                    else:
                        not_found_nama.append(excel_nama)
            
            connection.commit()
            
            
            
            not_found_nik_details = []
            for nik in not_found_nik:
                not_found_nik_details.append({
                    'nik': nik,
                    'status': 'NIK tidak ditemukan di database'
                })
            
            
            not_found_nama_details = []
            for nama in not_found_nama:
                not_found_nama_details.append({
                    'nama': nama,
                    'status': 'Nama tidak cocok (similarity < 80%)'
                })
            
            
            cursor.execute("""
                SELECT p.pegawai_id, p.pegawai_nip, p.pegawai_nama, p.pegawai_nik_lintas,
                       pb1.pembagian1_nama, p.sisa_cuti
                FROM pegawai p
                LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
                WHERE p.pegawai_status != 0 AND p.sisa_cuti = 0
                ORDER BY pb1.pembagian1_nama, p.pegawai_nama
            """)
            pegawai_belum_update = cursor.fetchall()
            
            
            cursor.execute("SELECT COUNT(*) as total FROM pegawai WHERE pegawai_status != 0")
            total_pegawai = cursor.fetchone()['total']
            
            cursor.execute("SELECT COUNT(*) as updated FROM pegawai WHERE sisa_cuti > 0 AND pegawai_status != 0")
            total_updated = cursor.fetchone()['updated']
            
            
            session['upload_result'] = {
                'updated_nik': updated_nik,
                'updated_nama': updated_nama,
                'total_pegawai': total_pegawai,
                'total_updated': total_updated,
                'not_found_nik': not_found_nik_details,
                'not_found_nama': not_found_nama_details,
                'similarity_matches': similarity_matches,
                'pegawai_belum_update': pegawai_belum_update
            }
            
        except Exception as e:
            connection.rollback()
            pass
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
        
        return redirect(url_for('upload_sc'))
    
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_pegawai,
                COUNT(CASE WHEN sisa_cuti > 0 THEN 1 END) as pegawai_with_cuti,
                SUM(CASE WHEN sisa_cuti > 0 THEN sisa_cuti ELSE 0 END) as total_sisa_cuti
            FROM pegawai 
            WHERE pegawai_status != 0
        """)
        stats = cursor.fetchone()
        
        
        upload_result = session.pop('upload_result', None)
        
        return render_template('upload_sc.html', stats=stats, upload_result=upload_result)
        
    except Error as e:
        return render_template('upload_sc.html', stats=None)
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()




@app.route('/daftar-tukar-shift')
def daftar_tukar_shift():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    shift_awal_filter = request.args.get('shift_awal_filter')
    shift_baru_filter = request.args.get('shift_baru_filter')
    status_filter = request.args.get('status_filter')
    search_nama = request.args.get('search_nama')
    divisi_filter = request.args.get('divisi_filter')

    if not start_date:
        start_date = (date.today() - timedelta(days=30)).strftime('%Y-%m-%d')
    if not end_date:
        end_date = date.today().strftime('%Y-%m-%d')

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        query = """
            SELECT pts.*, p.pegawai_nama, p.pegawai_nik_lintas, p.pegawai_nip, pb1.pembagian1_nama
            FROM pengajuan_tukar_shift pts
            JOIN pegawai p ON pts.pegawai_id = p.pegawai_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE pts.tanggal_pengajuan BETWEEN %s AND %s
        """

        params = [start_date + ' 00:00:00', end_date + ' 23:59:59']

        if shift_awal_filter:
            query += ' AND pts.shift_awal_id = %s'
            params.append(shift_awal_filter)

        if shift_baru_filter:
            query += ' AND pts.shift_baru_id = %s'
            params.append(shift_baru_filter)

        if status_filter:
            query += ' AND pts.status = %s'
            params.append(status_filter)

        if search_nama:
            search_string = f'%{search_nama}%'
            query += ' AND (p.pegawai_nama LIKE %s OR p.pegawai_nik_lintas LIKE %s OR pts.pegawai_tukar_nama LIKE %s)'
            params += [search_string, search_string, search_string]

        if divisi_filter:
            query += ' AND pb1.pembagian1_nama = %s'
            params.append(divisi_filter)

        query += ' ORDER BY pts.tanggal_pengajuan DESC'

        cursor.execute(query, params)
        tukar_shift_records = cursor.fetchall()

        for record in tukar_shift_records:
            if record['tanggal_mulai']:
                record['tanggal_mulai'] = record['tanggal_mulai'].strftime('%d/%m/%Y')
            if record['tanggal_akhir']:
                record['tanggal_akhir'] = record['tanggal_akhir'].strftime('%d/%m/%Y')
            if record['tanggal_pengajuan']:
                record['tanggal_pengajuan'] = record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M')

        cursor.execute("SELECT DISTINCT pembagian1_nama FROM pembagian1 ORDER BY pembagian1_nama")
        divisi_list = cursor.fetchall()

        cursor.execute("SELECT jk_id, jk_name FROM jam_kerja ORDER BY jk_name")
        shift_list = cursor.fetchall()

    except Error as e:
        tukar_shift_records = []
        divisi_list = []
        shift_list = []
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

    return render_template('daftar_tukar_shift.html', 
                         tukar_shift_records=tukar_shift_records,
                         divisi_list=divisi_list,
                         shift_list=shift_list,
                         start_date=start_date,
                         end_date=end_date,
                         shift_awal_filter=shift_awal_filter,
                         shift_baru_filter=shift_baru_filter,
                         status_filter=status_filter,
                         search_nama=search_nama,
                         divisi_filter=divisi_filter)

@app.route('/update-tukar-shift/<int:id_tukar_shift>', methods=['POST'])
def update_tukar_shift(id_tukar_shift):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'})
    
    new_status = request.json.get('status')
    
    if new_status not in ['OK', 'Menunggu', 'Ditolak']:
        return jsonify({'success': False, 'message': 'Status tidak valid'})
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM pengajuan_tukar_shift WHERE id_tukar_shift = %s", (id_tukar_shift,))
        tukar_shift_record = cursor.fetchone()
        
        if not tukar_shift_record:
            return jsonify({'success': False, 'message': 'Data pengajuan tukar shift tidak ditemukan'})
        
        cursor.execute("UPDATE pengajuan_tukar_shift SET status = %s WHERE id_tukar_shift = %s", (new_status, id_tukar_shift))
        connection.commit()
        
        return jsonify({'success': True, 'message': 'Status berhasil diupdate'})
        
    except Error as e:
        return jsonify({'success': False, 'message': f'Error: {str(e)}'})
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()

@app.route('/hapus-tukar-shift-admin/<int:id_tukar_shift>')
def hapus_tukar_shift_admin(id_tukar_shift):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM pengajuan_tukar_shift WHERE id_tukar_shift = %s", (id_tukar_shift,))
        tukar_shift_record = cursor.fetchone()
        
        if not tukar_shift_record:
            return redirect(url_for('daftar_tukar_shift'))
        
        cursor.execute("DELETE FROM pengajuan_tukar_shift WHERE id_tukar_shift = %s", (id_tukar_shift,))
        connection.commit()
        
    except Error as e:
        pass
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()
    
    return redirect(url_for('daftar_tukar_shift'))

from difflib import SequenceMatcher

def similarity(a, b):
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()

@app.route('/upload_tim', methods=['GET', 'POST'])
def upload_tim():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        file_nik = request.files.get('file_nik')
        file_nama = request.files.get('file_nama')
        
        if not file_nik or not file_nama:
            return redirect(url_for('upload_tim'))
        
        try:
            connection = get_db_connection()
            cursor = connection.cursor(dictionary=True)
            
            
            cursor.execute("UPDATE pegawai SET pembagian3_id = NULL WHERE pegawai_status != 0")
            
            
            df_nik = pd.read_excel(file_nik)
            df_nama = pd.read_excel(file_nama)
            
            
            tim_from_nik = set(df_nik['TIM'].dropna().astype(str).str.upper())
            tim_from_nama = set(df_nama['TIM'].dropna().astype(str).str.upper()) 
            all_tims = tim_from_nik.union(tim_from_nama)
            
            
            tim_id_map = {}
            for tim_name in all_tims:
                cursor.execute("SELECT pembagian3_id FROM pembagian3 WHERE UPPER(pembagian3_nama) = %s", (tim_name,))
                result = cursor.fetchone()
                
                if result:
                    tim_id_map[tim_name] = result['pembagian3_id']
                else:
                    cursor.execute("INSERT INTO pembagian3 (pembagian3_nama) VALUES (%s)", (tim_name,))
                    tim_id_map[tim_name] = cursor.lastrowid
            
            
            updated_nik = 0
            not_found_nik = []
            
            for _, row in df_nik.iterrows():
                nik = str(row['NIK']).strip()
                tim_name = str(row['TIM']).strip().upper()
                
                if tim_name in tim_id_map:
                    cursor.execute("""
                        UPDATE pegawai 
                        SET pembagian3_id = %s 
                        WHERE pegawai_nik_lintas = %s
                    """, (tim_id_map[tim_name], nik))
                    
                    if cursor.rowcount > 0:
                        updated_nik += 1
                    else:
                        not_found_nik.append({'nik': nik, 'tim': tim_name})
            
            
            cursor.execute("SELECT pegawai_nama FROM pegawai WHERE pegawai_status != 0")
            db_names = [row['pegawai_nama'] for row in cursor.fetchall()]
            
            updated_nama = 0
            not_found_nama = []
            similarity_matches = []
            
            for _, row in df_nama.iterrows():
                excel_nama = str(row['Nama']).strip()
                tim_name = str(row['TIM']).strip().upper()
                
                if tim_name not in tim_id_map:
                    continue
                
                
                exact_match = False
                for db_nama in db_names:
                    if excel_nama.lower() == db_nama.lower():
                        cursor.execute("""
                            UPDATE pegawai 
                            SET pembagian3_id = %s 
                            WHERE LOWER(pegawai_nama) = %s
                        """, (tim_id_map[tim_name], excel_nama.lower()))
                        updated_nama += 1
                        exact_match = True
                        break
                
                
                if not exact_match:
                    best_match = None
                    best_score = 0
                    
                    for db_nama in db_names:
                        score = similarity(excel_nama, db_nama)
                        if score > best_score and score >= 0.8:  
                            best_score = score
                            best_match = db_nama
                    
                    if best_match:
                        cursor.execute("""
                            UPDATE pegawai 
                            SET pembagian3_id = %s 
                            WHERE pegawai_nama = %s
                        """, (tim_id_map[tim_name], best_match))
                        updated_nama += 1
                        similarity_matches.append(f"{excel_nama} → {best_match} ({best_score:.2%}) - {tim_name}")
                    else:
                        not_found_nama.append({'nama': excel_nama, 'tim': tim_name})
            
            connection.commit()
            
            
            not_found_nik_details = []
            for item in not_found_nik:
                not_found_nik_details.append({
                    'nik': item['nik'],
                    'tim': item['tim'],
                    'status': 'NIK tidak ditemukan di database'
                })
            
            not_found_nama_details = []
            for item in not_found_nama:
                not_found_nama_details.append({
                    'nama': item['nama'],
                    'tim': item['tim'],
                    'status': 'Nama tidak cocok (similarity < 80%)'
                })
            
            
            cursor.execute("""
                SELECT p.pegawai_id, p.pegawai_nip, p.pegawai_nama, p.pegawai_nik_lintas,
                       pb1.pembagian1_nama, pb3.pembagian3_nama
                FROM pegawai p
                LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
                LEFT JOIN pembagian3 pb3 ON p.pembagian3_id = pb3.pembagian3_id
                WHERE p.pegawai_status != 0 AND p.pembagian3_id IS NULL
                ORDER BY pb1.pembagian1_nama, p.pegawai_nama
            """)
            pegawai_tanpa_tim = cursor.fetchall()
            
            
            cursor.execute("SELECT COUNT(*) as total FROM pegawai WHERE pegawai_status != 0")
            total_pegawai = cursor.fetchone()['total']
            
            cursor.execute("SELECT COUNT(*) as assigned FROM pegawai WHERE pembagian3_id IS NOT NULL AND pegawai_status != 0")
            total_assigned = cursor.fetchone()['assigned']
            
            
            cursor.execute("""
                SELECT pb3.pembagian3_nama, COUNT(*) as jumlah
                FROM pegawai p
                JOIN pembagian3 pb3 ON p.pembagian3_id = pb3.pembagian3_id
                WHERE p.pegawai_status != 0
                GROUP BY pb3.pembagian3_id, pb3.pembagian3_nama
                ORDER BY pb3.pembagian3_nama
            """)
            tim_statistics = cursor.fetchall()
            
            
            session['upload_result'] = {
                'updated_nik': updated_nik,
                'updated_nama': updated_nama,
                'total_pegawai': total_pegawai,
                'total_assigned': total_assigned,
                'not_found_nik': not_found_nik_details,
                'not_found_nama': not_found_nama_details,
                'similarity_matches': similarity_matches,
                'pegawai_tanpa_tim': pegawai_tanpa_tim,
                'tim_statistics': tim_statistics
            }
            
        except Exception as e:
            connection.rollback()
            pass
        finally:
            if connection.is_connected():
                cursor.close()
                connection.close()
        
        return redirect(url_for('upload_tim'))
    
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_pegawai,
                COUNT(CASE WHEN pembagian3_id IS NOT NULL THEN 1 END) as pegawai_with_tim
            FROM pegawai 
            WHERE pegawai_status != 0
        """)
        stats = cursor.fetchone()
        
        
        cursor.execute("""
            SELECT pb3.pembagian3_nama, COUNT(*) as jumlah
            FROM pegawai p
            JOIN pembagian3 pb3 ON p.pembagian3_id = pb3.pembagian3_id
            WHERE p.pegawai_status != 0
            GROUP BY pb3.pembagian3_id, pb3.pembagian3_nama
            ORDER BY pb3.pembagian3_nama
        """)
        tim_stats = cursor.fetchall()
        
        
        upload_result = session.pop('upload_result', None)
        
        return render_template('upload_tim.html', stats=stats, tim_stats=tim_stats, upload_result=upload_result)
        
    except Error as e:
        return render_template('upload_tim.html', stats=None, tim_stats=None)
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()



@app.route('/kalender-cuti')
def kalender_cuti():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('login'))

    month = request.args.get('month')
    year = request.args.get('year')
    
    if not month or not year:
        today = date.today()
        month = today.month
        year = today.year
    else:
        month = int(month)
        year = int(year)

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        
        cursor.execute("SELECT tanggal, keterangan, jenis FROM cuti_libur_nasional")
        libur_nasional_result = cursor.fetchall()
        libur_nasional_data = {}
        for row in libur_nasional_result:
            libur_nasional_data[row['tanggal'].strftime('%Y-%m-%d')] = {
                'keterangan': row['keterangan'],
                'jenis': row['jenis']
            }
        
        
        pegawai_id = session.get('id')  
        sisa_cuti = 0
        pegawai_nama = session.get('username', '')  
        
        if pegawai_id:
            cursor.execute("""
                SELECT sisa_cuti, pegawai_nama 
                FROM pegawai 
                WHERE pegawai_id = %s
            """, (pegawai_id,))
            pegawai_result = cursor.fetchone()
            if pegawai_result:
                sisa_cuti = pegawai_result['sisa_cuti'] or 0
                pegawai_nama = pegawai_result['pegawai_nama']
        
        
        first_day = date(year, month, 1)
        if month == 12:
            last_day = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            last_day = date(year, month + 1, 1) - timedelta(days=1)
        
        
        start_of_calendar = first_day - timedelta(days=first_day.weekday())
        end_of_calendar = start_of_calendar + timedelta(days=41)  
        
        
        cursor.execute("""
            SELECT pc.tanggal_cuti_mulai, pc.tanggal_cuti_akhir, pc.tanggal_pengajuan,
                   p.pegawai_nama, pc.kategori_cuti, pc.status
            FROM pengajuan_cuti pc
            JOIN pegawai p ON pc.pegawai_id = p.pegawai_id
            WHERE pc.status = 'OK'
              AND ((pc.tanggal_cuti_mulai BETWEEN %s AND %s)
                   OR (pc.tanggal_cuti_akhir BETWEEN %s AND %s)
                   OR (pc.tanggal_cuti_mulai <= %s AND pc.tanggal_cuti_akhir >= %s))
            ORDER BY pc.tanggal_cuti_mulai
        """, (start_of_calendar, end_of_calendar, start_of_calendar, end_of_calendar, start_of_calendar, end_of_calendar))
        
        cuti_records = cursor.fetchall()
        
        
        cuti_by_date = {}
        for record in cuti_records:
            start_date = record['tanggal_cuti_mulai']
            end_date = record['tanggal_cuti_akhir']
            
            current_date = start_date
            while current_date <= end_date:
                
                if start_of_calendar <= current_date <= end_of_calendar:
                    date_str = current_date.strftime('%Y-%m-%d')
                    
                    if date_str not in cuti_by_date:
                        cuti_by_date[date_str] = []
                    
                    cuti_by_date[date_str].append({
                        'nama': record['pegawai_nama'],
                        'kategori': record['kategori_cuti'],
                        'tanggal_pengajuan': record['tanggal_pengajuan'].strftime('%d/%m/%Y %H:%M') if record['tanggal_pengajuan'] else '-'
                    })
                
                current_date += timedelta(days=1)
        
        
        calendar_data = []
        
        
        current_date = start_of_calendar
        for week in range(6):
            week_data = []
            for day in range(7):
                date_str = current_date.strftime('%Y-%m-%d')
                
                
                cuti_count = len(cuti_by_date.get(date_str, []))
                
                
                status = 'normal'
                if current_date.weekday() in [5, 6]:  
                    status = 'weekend'
                elif date_str in libur_nasional_data:
                    status = 'holiday'
                elif cuti_count >= 7:
                    status = 'full'
                elif cuti_count > 0:
                    status = 'partial'
                
                week_data.append({
                    'date': current_date,
                    'date_str': date_str,
                    'day': current_date.day,
                    'is_current_month': current_date.month == month,
                    'is_today': current_date == date.today(),
                    'status': status,
                    'cuti_count': cuti_count,
                    'cuti_data': cuti_by_date.get(date_str, []),
                    'libur_info': libur_nasional_data.get(date_str, {})
                })
                
                current_date += timedelta(days=1)
            
            calendar_data.append(week_data)
        
        
        prev_month = month - 1 if month > 1 else 12
        prev_year = year if month > 1 else year - 1
        next_month = month + 1 if month < 12 else 1
        next_year = year if month < 12 else year + 1
        
        month_names = [
            '', 'Januari', 'Februari', 'Maret', 'April', 'Mei', 'Juni',
            'Juli', 'Agustus', 'September', 'Oktober', 'November', 'Desember'
        ]
        
        return render_template('kalender_cuti.html',
                             calendar_data=calendar_data,
                             current_month=month,
                             current_year=year,
                             month_name=month_names[month],
                             prev_month=prev_month,
                             prev_year=prev_year,
                             next_month=next_month,
                             next_year=next_year,
                             sisa_cuti=sisa_cuti,
                             pegawai_nama=pegawai_nama)
        
    except Error as e:
        return render_template('kalender_cuti.html', 
                             calendar_data=[], 
                             sisa_cuti=0, 
                             pegawai_nama=session.get('username', ''))
    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/cek_jadwal')
def cek_jadwal():
    import calendar
    # halaman ini untuk admin cek jadwal shift satu bulan penuh dalam bentuk matriks
    # tampilkan pegawai vs tanggal, isi sel adalah nama shift (jk_name)
    # bisa filter divisi dan nama pegawai, juga tampilkan total personil yang muncul

    # cek login admin dulu
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    # ambil bulan yang dipilih, default bulan ini (format yyyy-mm)
    selected_month = request.args.get('month', datetime.now().strftime('%Y-%m'))
    divisi = request.args.get('divisi', '')
    nama = request.args.get('nama', '')

    # pecah tahun dan bulan, hitung jumlah hari di bulan itu
    year, month = map(int, selected_month.split('-'))
    _, last_day = calendar.monthrange(year, month)
    start_date = f"{year}-{month:02d}-01"
    end_date = f"{year}-{month:02d}-{last_day}"

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # ambil semua nama divisi untuk dropdown filter
        query_divisi = """
            SELECT DISTINCT pb1.pembagian1_nama 
            FROM pembagian1 pb1
            WHERE pb1.pembagian1_nama IS NOT NULL
            AND pb1.pembagian1_nama NOT IN ('admin')
            ORDER BY pb1.pembagian1_nama
        """
        cursor.execute(query_divisi)
        divisions = [row['pembagian1_nama'] for row in cursor.fetchall()]

        # ambil daftar pegawai aktif + nama divisi
        # bisa difilter per divisi atau nama pegawai
        query_employees = """
            SELECT p.pegawai_id, p.pegawai_nama, pb1.pembagian1_nama
            FROM pegawai p
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE p.pegawai_status != 0 AND p.pegawai_id != 0 AND p.pegawai_nama != 'admin'
        """
        params = []
        if divisi:
            query_employees += " AND pb1.pembagian1_nama = %s"
            params.append(divisi)
        if nama:
            query_employees += " AND p.pegawai_nama LIKE %s"
            params.append(f"%{nama}%")
        query_employees += " ORDER BY p.pegawai_nama"

        cursor.execute(query_employees, params)
        employees = cursor.fetchall()
        # hitung total pegawai yang muncul setelah filter
        total_personil = len(employees)

        # ambil semua jadwal shift dalam rentang bulan
        query_shifts = """
            SELECT sr.pegawai_id, sr.tgl_shift, jk.jk_name
            FROM shift_result_new sr
            JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            JOIN pegawai p ON sr.pegawai_id = p.pegawai_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE sr.tgl_shift BETWEEN %s AND %s
            AND p.pegawai_status != 0 AND p.pegawai_id != 0 AND p.pegawai_nama != 'admin'
        """
        shift_params = [start_date, end_date]
        if divisi:
            query_shifts += " AND pb1.pembagian1_nama = %s"
            shift_params.append(divisi)
        if nama:
            query_shifts += " AND p.pegawai_nama LIKE %s"
            shift_params.append(f"%{nama}%")

        cursor.execute(query_shifts, shift_params)
        shifts = cursor.fetchall()

        # ambil semua daftar shift untuk dropdown update manual (kalau perlu)
        cursor.execute("SELECT jk_id, jk_name FROM jam_kerja ORDER BY jk_name")
        jam_kerja = cursor.fetchall()

        # buat struktur kalender: dict pegawai_id → {tanggal: nama_shift}
        shift_calendar = {emp['pegawai_id']: {} for emp in employees}
        for shift in shifts:
            shift_calendar[shift['pegawai_id']][shift['tgl_shift'].strftime('%Y-%m-%d')] = shift['jk_name']

        # buat list semua tanggal di bulan ini untuk header tabel
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        date_range = [(start + timedelta(days=x)).strftime('%Y-%m-%d') for x in range((end - start).days + 1)]

        # kirim semua data ke template untuk ditampilkan sebagai matriks
        return render_template(
            'cek_jadwal.html',
            employees=employees,
            shift_calendar=shift_calendar,
            date_range=date_range,
            selected_month=selected_month,
            divisions=divisions,
            divisi=divisi,
            nama=nama,
            total_personil=total_personil,
            jam_kerja=jam_kerja
        )
    except Error as e:
        # kalau ada error db, tampilkan halaman kosong
        return render_template(
            'cek_jadwal.html',
            employees=[], shift_calendar={}, date_range=[],
            selected_month=selected_month, divisions=[],
            total_personil=0, jam_kerja=[]
        )
    finally:
        # tutup koneksi db
        if connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/reset_jadwal', methods=['POST'])
def reset_jadwal():
    # api ini untuk reset jadwal shift massal ke jk_id = 0 (default/kosong)
    # hanya untuk record "utama" (bukan lembur/extra/temp)
    # kalau tidak ada record, insert baris baru dengan jk_id 0

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    try:
        # ambil data dari json body
        data = request.get_json()
        pegawai_ids = data.get('pegawai_ids', [])
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        # cek semua data wajib ada
        if not all([pegawai_ids, start_date_str, end_date_str]):
            return jsonify({'success': False, 'message': 'Data tidak lengkap'}), 400

        # ubah string tanggal jadi objek date
        try:
            sd = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            ed = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception:
            return jsonify({'success': False, 'message': 'Invalid date format, gunakan YYYY-MM-DD'}), 400

        # cek start tidak boleh lebih besar dari end
        if sd > ed:
            return jsonify({'success': False, 'message': 'start_date tidak boleh setelah end_date'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        # pertama coba update record yang sudah ada ke jk_id = 0
        placeholders = ','.join(['%s'] * len(pegawai_ids))
        update_query = f"""
            UPDATE shift_result_new
            SET jk_id = 0
            WHERE pegawai_id IN ({placeholders})
              AND tgl_shift BETWEEN %s AND %s
              AND khusus_lembur = 0
              AND khusus_extra = 0
              AND temp_id_auto = 0
        """
        update_params = pegawai_ids + [sd.isoformat(), ed.isoformat()]

        cursor.execute(update_query, update_params)
        connection.commit()
        rows_updated = cursor.rowcount

        # kalau tidak ada yang diupdate (belum ada record), insert baris baru
        if rows_updated == 0:
            insert_query = """
                INSERT INTO shift_result_new
                (pegawai_id, tgl_shift, khusus_lembur, khusus_extra, temp_id_auto, jdw_kerja_m_id, jk_id)
                VALUES (%s, %s, 0, 0, 0, 0, %s)
                ON DUPLICATE KEY UPDATE jk_id = VALUES(jk_id)
            """
            days = (ed - sd).days + 1
            to_insert = []
            for pid in pegawai_ids:
                for i in range(days):
                    d = (sd + timedelta(days=i)).isoformat()
                    to_insert.append((pid, d, 0))

            # insert satu per satu (bisa diganti executemany kalau banyak)
            for vals in to_insert:
                cursor.execute(insert_query, vals)
            connection.commit()

        # balas sukses
        return jsonify({'success': True, 'message': 'Jadwal berhasil direset'})
    except Error as e:
        return jsonify({'success': False, 'message': f'Error: {e}'}), 500
    finally:
        # tutup koneksi
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/update_jadwal', methods=['POST'])
def update_jadwal():
    # api ini untuk ubah jadwal shift massal ke jk_id tertentu
    # mirip reset, tapi set ke jk_id yang dipilih admin
    # prioritas update record existing, kalau tidak ada insert baru

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Unauthorized access'}), 403

    try:
        # ambil data dari json
        data = request.get_json()
        pegawai_ids = data.get('pegawai_ids', [])
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')
        jk_id = data.get('jk_id')

        # cek semua data wajib
        if not all([pegawai_ids, start_date_str, end_date_str, jk_id is not None]):
            return jsonify({'success': False, 'message': 'Data tidak lengkap'}), 400

        # ubah string jadi date
        try:
            sd = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            ed = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except Exception:
            return jsonify({'success': False, 'message': 'Invalid date format, gunakan YYYY-MM-DD'}), 400

        if sd > ed:
            return jsonify({'success': False, 'message': 'start_date tidak boleh setelah end_date'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        # update dulu record yang sudah ada ke jk_id baru
        placeholders = ','.join(['%s'] * len(pegawai_ids))
        update_query = f"""
            UPDATE shift_result_new
            SET jk_id = %s
            WHERE pegawai_id IN ({placeholders})
              AND tgl_shift BETWEEN %s AND %s
              AND khusus_lembur = 0
              AND khusus_extra = 0
              AND temp_id_auto = 0
        """
        update_params = [jk_id] + pegawai_ids + [sd.isoformat(), ed.isoformat()]

        cursor.execute(update_query, update_params)
        connection.commit()
        rows_updated = cursor.rowcount

        # kalau tidak ada yang diupdate, insert baris baru
        if rows_updated == 0:
            insert_query = """
                INSERT INTO shift_result_new
                (pegawai_id, tgl_shift, khusus_lembur, khusus_extra, temp_id_auto, jdw_kerja_m_id, jk_id)
                VALUES (%s, %s, 0, 0, 0, 0, %s)
                ON DUPLICATE KEY UPDATE jk_id = VALUES(jk_id)
            """
            days = (ed - sd).days + 1
            to_insert = []
            for pid in pegawai_ids:
                for i in range(days):
                    d = (sd + timedelta(days=i)).isoformat()
                    to_insert.append((pid, d, jk_id))

            for vals in to_insert:
                cursor.execute(insert_query, vals)
            connection.commit()

        return jsonify({'success': True, 'message': 'Jadwal berhasil diupdate'})
    except Error as e:
        return jsonify({'success': False, 'message': f'Error: {e}'}), 500
    finally:
        # tutup koneksi
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@app.route('/visualisasi_absen')
def visualisasi_absen():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))

    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    selected_division = request.args.get('divisi')

    if not start_date_str or not end_date_str:
        today = date.today()
        start_date_str = today.replace(day=1).strftime('%Y-%m-%d')
        end_of_month = (today.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        end_date_str = end_of_month.strftime('%Y-%m-%d')

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        date_range = [ start_date + timedelta(days=x) for x in range((end_date - start_date).days + 1) ]

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        
        cursor.execute("SELECT DISTINCT pembagian1_nama FROM pembagian1 WHERE pembagian1_nama IS NOT NULL AND pembagian1_nama != '' ORDER BY pembagian1_nama")
        divisions = [row['pembagian1_nama'] for row in cursor.fetchall()]

       
        query = """
            SELECT 
                p.pegawai_nama, sr.tgl_shift, sr.scan_in, sr.scan_out,
                jk.jk_id, jk.jk_name, jk.jk_bcin, jk.jk_ecout,
                pb1.pembagian1_nama
            FROM pegawai p
            JOIN shift_result_new sr ON p.pegawai_id = sr.pegawai_id
            LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            LEFT JOIN pembagian1 pb1 ON p.pembagian1_id = pb1.pembagian1_id
            WHERE sr.tgl_shift BETWEEN %s AND %s AND p.pegawai_status != 0
        """
        params = [start_date_str, end_date_str]

        if selected_division:
            query += " AND pb1.pembagian1_nama = %s"
            params.append(selected_division)
        
        query += " ORDER BY p.pegawai_nama, sr.tgl_shift"
        cursor.execute(query, params)
        records = cursor.fetchall()

        
        data_visual = {}
        summary_stats = {
            'H': 0, 'TL': 0, 'TP': 0, 'A': 0, 'S': 0, 'D': 0, 
            'CT': 0, 'LB': 0, 'LDL': 0, 'LD': 0, 'OT': 0, 'Libur': 0
        }
        daily_stats = {day.strftime('%Y-%m-%d'): summary_stats.copy() for day in date_range}


        for record in records:
            pegawai_nama = record['pegawai_nama']
            if pegawai_nama not in data_visual:
                data_visual[pegawai_nama] = {}

          
            keterangan = 'A'
            jk_id = record['jk_id']
            scan_in = record['scan_in']
            scan_out = record['scan_out']

            keterangan_map = {
                9: "D", 8: "CT", 11: "S", 29: "LB", 30: "LDL", 31: "LD"
            }
            if jk_id in keterangan_map:
                keterangan = keterangan_map[jk_id]
            elif jk_id == 10:
                keterangan = "OT" if (scan_in and scan_out) else "Libur"
            else:
                if scan_in and not scan_out:
                    keterangan = "TP"
                elif not scan_in and scan_out:
                    keterangan = "TL" 
                elif scan_in and scan_out:
                    keterangan = "H" 
                else:
                    keterangan = "A"
           
            tgl_shift_str = record['tgl_shift'].strftime('%Y-%m-%d')
            if keterangan in summary_stats:
                summary_stats[keterangan] += 1
                daily_stats[tgl_shift_str][keterangan] += 1

           
            jk_bcin_str = (datetime.min + record['jk_bcin']).strftime('%H:%M') if isinstance(record['jk_bcin'], timedelta) else None
            jk_ecout_str = (datetime.min + record['jk_ecout']).strftime('%H:%M') if isinstance(record['jk_ecout'], timedelta) else None
            scan_in_str = record['scan_in'].strftime('%H:%M') if record['scan_in'] else None
            scan_out_str = record['scan_out'].strftime('%H:%M') if record['scan_out'] else None

            data_visual[pegawai_nama][record['tgl_shift']] = {
                'keterangan': keterangan,
                'jk_name': record['jk_name'],
                'jk_bcin': jk_bcin_str,
                'jk_ecout': jk_ecout_str,
                'scan_in': scan_in_str,
                'scan_out': scan_out_str
            }
    
    except Error as e:
        data_visual = {}
        divisions = []
        date_range = []
        summary_stats = {}
        daily_stats = {}

    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()

    date_range_str = [d.strftime('%Y-%m-%d') for d in date_range]
    return render_template(
        'visualisasi_absen.html',
        data_visual=data_visual,
        date_range=date_range,
        start_date=start_date_str,
        end_date=end_date_str,
        date_range_str=date_range_str,
        divisions=divisions,
        selected_division=selected_division,
        summary_stats=summary_stats,
        daily_stats=daily_stats
    )


@app.route('/uploadnya')
def list_uploads():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    upload_dir = app.config['UPLOAD_FOLDER']
    files = []
    if os.path.exists(upload_dir):
        files = [
            {
                'name': f,
                'size': os.path.getsize(os.path.join(upload_dir, f)),
                'modified': datetime.fromtimestamp(os.path.getmtime(os.path.join(upload_dir, f))).strftime('%Y-%m-%d %H:%M:%S')
            }
            for f in os.listdir(upload_dir) if os.path.isfile(os.path.join(upload_dir, f))
        ]
    
    return render_template('uploadnya.html', files=files)

@app.route('/download/<filename>')
def download_file(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('login'))
    
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True)
    else:
        return redirect(url_for('list_uploads'))




if __name__ == "__main__":
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    app.run(host="0.0.0.0", port=80, debug=True)
