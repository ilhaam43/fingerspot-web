from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from utils.db import get_db_connection
from datetime import datetime, date, timedelta

dashboard_pegawai_bp = Blueprint('dashboard_pegawai', __name__)

# TAMBAHKAN BARIS INI: Blueprint untuk Form Pengajuan
form_pengajuan_bp = Blueprint('form_pengajuan', __name__, url_prefix='/form-pengajuan')

@form_pengajuan_bp.route('/')
def index():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('auth.auth_login'))
    return render_template('form-pengajuan-ketidakhadiran.html')

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

            


@dashboard_pegawai_bp.route('/dasbor-pegawai')
def dasbor_pegawai():
    # halaman ini adalah dashboard utama buat pegawai
    # tugasnya: tampilkan rekap absensi pribadi dalam rentang tanggal tertentu
    # defaultnya minggu berjalan (senin-minggu), tapi minimum tanggal 6 Mei 2025
    # pegawai hanya bisa lihat data miliknya sendiri (berdasarkan nip dari session)

    # cek dulu user sudah login dan role-nya pegawai
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('auth.auth_login'))

    # ambil nip dari session (harus ada, kalau enggak balik ke login)
    nip = session.get('nip')
    if not nip:
        return redirect(url_for('auth.auth_login'))

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
        template_name = 'beranda_new.html' if preview else 'dasbor-pegawai.html'

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

    except Exception as e:
        # kalau error db, tampilkan halaman dengan pesan error
        return render_template('dasbor-pegawai.html', records=[], pegawai=None, error="Failed to fetch data")

    finally:
        # pastikan tutup koneksi db
        if connection.is_connected():
            cursor.close()
            connection.close()





@dashboard_pegawai_bp.route('/akun-pegawai', methods=['GET', 'POST'])
def akun_pegawai():

    import re

    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('auth.auth_login'))

    nip = session.get('nip')
    if not nip:
        return redirect(url_for('auth.auth_login'))

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

                except Exception as e:
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
            return render_template('akun-pegawai.html', pegawai=None, error="Data pengguna enggak ditemukan", success=None)

        return render_template('akun-pegawai.html', pegawai=pegawai, error=error, success=success)

    except Exception as e:
        return render_template('akun-pegawai.html', pegawai=None, error="Gagal ambil data profil", success=None)

    finally:
        if connection.is_connected():
            cursor.close()
            connection.close()


def get_next_week_dates():
    today = date.today()
    days_ahead = 0 - today.weekday() + 7
    if days_ahead <= 0:
        days_ahead += 7
    next_monday = today + timedelta(days=days_ahead)
    next_sunday = next_monday + timedelta(days=6)
    return next_monday, next_sunday

@dashboard_pegawai_bp.route('/tukar-shift', methods=['GET'])
def tukar_shift_page():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('auth.auth_login'))

    nip = session.get('nip')
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT pegawai_id, pembagian1_id FROM pegawai WHERE pegawai_nip = %s", (nip,))
        user = cursor.fetchone()
        
        if not user or not user.get('pembagian1_id'):
            return render_template('tukar-shift.html', error="Divisi tidak ditemukan untuk user ini")

        pegawai_id = user['pegawai_id']
        pembagian1_id = user['pembagian1_id']

        cursor.execute("""
            SELECT pegawai_id, pegawai_nama 
            FROM pegawai 
            WHERE pembagian1_id = %s AND pegawai_status IN (1, 3, 4) AND pegawai_id != %s
            ORDER BY pegawai_nama
        """, (pembagian1_id, pegawai_id))
        coworkers = cursor.fetchall()
        
        cursor.execute("SELECT nama FROM pengajuan_nama_leader ORDER BY nama")
        leaders = cursor.fetchall()
        
        cursor.execute("SELECT nama FROM pengajuan_nama_supervisor ORDER BY nama")
        supervisors = cursor.fetchall()
        
        cursor.execute("SELECT pembagian3_nama FROM pembagian3 ORDER BY pembagian3_nama")
        teams = cursor.fetchall()
        
        return render_template('tukar-shift.html', coworkers=coworkers, current_pegawai_id=pegawai_id, leaders=leaders, supervisors=supervisors, teams=teams)

    except Exception as e:
        return render_template('tukar-shift.html', error=str(e))
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()

@dashboard_pegawai_bp.route('/tukar-shift/riwayat', methods=['GET'])
def riwayat_tukar_shift_page():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return redirect(url_for('auth.auth_login'))

    nip = session.get('nip')
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s", (nip,))
        user = cursor.fetchone()
        
        if not user:
            return redirect(url_for('dashboard_pegawai.tukar_shift_page'))

        pegawai_id = user['pegawai_id']
        
        filter_tanggal = request.args.get('filter_tanggal', '')
        filter_personil = request.args.get('filter_personil', '')
        
        riwayat = []
        is_filtered = False

        if filter_tanggal or filter_personil:
            is_filtered = True
            query = "SELECT * FROM pengajuan_tukar_shift WHERE pegawai_id = %s"
            params = [pegawai_id]
            
            if filter_tanggal:
                query += " AND tanggal_mulai = %s"
                params.append(filter_tanggal)
            
            if filter_personil:
                query += " AND pegawai_tukar_nama LIKE %s"
                params.append(f"%{filter_personil}%")
                
            query += " ORDER BY tanggal_pengajuan DESC"
            cursor.execute(query, tuple(params))
            riwayat = cursor.fetchall()
        
        return render_template('riwayat-tukar-shift.html', 
                               riwayat=riwayat, 
                               is_filtered=is_filtered,
                               filter_tanggal=filter_tanggal,
                               filter_personil=filter_personil)

    except Exception as e:
        return render_template('riwayat-tukar-shift.html', error=str(e))
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()

@dashboard_pegawai_bp.route('/tukar-shift/download/<int:id_tukar>', methods=['GET'])
def download_tukar_shift_pdf(id_tukar):
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    nip = session.get('nip')
    
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s", (nip,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
            
        pegawai_id = user['pegawai_id']
        
        # Validasi kepemilikan data
        cursor.execute("SELECT * FROM pengajuan_tukar_shift WHERE id_tukar_shift=%s AND pegawai_id=%s", (id_tukar, pegawai_id))
        row = cursor.fetchone()

        if not row:
            return jsonify({'success': False, 'error': 'Data tidak ditemukan atau bukan milik Anda.'}), 404

        import os, re, uuid, base64
        from datetime import datetime
        from flask import send_file
        from routes.admin_tukar_shift import _fill_docx_template, _convert_docx_to_pdf_via_word

        def fmt_date(val):
            if not val:
                return ''
            if hasattr(val, 'strftime'):
                return val.strftime('%d/%m/%Y')
            try:
                return datetime.strptime(str(val), '%Y-%m-%d').strftime('%d/%m/%Y')
            except Exception:
                return str(val)

        tgl_mulai = row.get('tanggal_mulai')
        tgl_akhir = row.get('tanggal_akhir')

        jumlah_hari = row.get('jumlah_hari') or ''
        if not jumlah_hari and tgl_mulai and tgl_akhir:
            try:
                d1 = tgl_mulai if hasattr(tgl_mulai, 'date') else datetime.strptime(str(tgl_mulai), '%Y-%m-%d').date()
                d2 = tgl_akhir if hasattr(tgl_akhir, 'date') else datetime.strptime(str(tgl_akhir), '%Y-%m-%d').date()
                if hasattr(d1, 'date'): d1 = d1.date()
                if hasattr(d2, 'date'): d2 = d2.date()
                jumlah_hari = str((d2 - d1).days + 1) + ' Hari'
            except Exception:
                jumlah_hari = ''

        tgl_pengajuan = row.get('tanggal_pengajuan') or datetime.now()

        placeholders = {
            'nama_pegawai':      row.get('nama_pegawai', ''),
            'pegawai_tukar_nama': row.get('pegawai_tukar_nama', ''),
            'nama_supervisor':   row.get('nama_supervisor', ''),
            'nama_leader':       row.get('nama_leader', ''),
            'tanggal_mulai':     fmt_date(tgl_mulai),
            'tanggal_akhir':     fmt_date(tgl_akhir),
            'tanggal_pembuatan': fmt_date(tgl_pengajuan),
            'tim':               row.get('tim', ''),
            'jumlah_hari':       jumlah_hari,
            'shift_awal':        row.get('shift_awal_nama', ''),
            'shift_baru':        row.get('shift_baru_nama', ''),
            'alasan_tukar_shift': row.get('alasan_tukar_shift', ''),
        }

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_path = os.path.join(base_dir, 'files', 'template_tukar_shift.docx')
        tmp_dir = os.path.join(base_dir, 'files', 'temp')
        os.makedirs(tmp_dir, exist_ok=True)

        safe_name = re.sub(r'[^\w\s-]', '', row.get('nama_pegawai', 'Pegawai')).strip().replace(' ', '_')
        if hasattr(tgl_mulai, 'strftime'):
            str_tgl_mulai = tgl_mulai.strftime('%d%m%Y')
        else:
            try:
                str_tgl_mulai = datetime.strptime(str(tgl_mulai), '%Y-%m-%d').strftime('%d%m%Y')
            except Exception:
                str_tgl_mulai = str(tgl_mulai).replace('-', '')
                
        download_name = f"Tukar_Shift_{safe_name}_{str_tgl_mulai}.pdf"

        uid = uuid.uuid4().hex
        tmp_docx = os.path.join(tmp_dir, f'ts_{uid}.docx')
        tmp_pdf = os.path.join(tmp_dir, download_name)

        _fill_docx_template(template_path, tmp_docx, placeholders)
        _convert_docx_to_pdf_via_word(tmp_docx, tmp_pdf)

        if os.path.exists(tmp_docx):
            try: os.remove(tmp_docx)
            except: pass

        if not os.path.exists(tmp_pdf):
            return jsonify({'success': False, 'error': 'Gagal membuat PDF.'}), 500

        if request.args.get('format') == 'json':
            with open(tmp_pdf, 'rb') as f:
                pdf_b64 = base64.b64encode(f.read()).decode('utf-8')
            return jsonify({
                'success': True,
                'filename': download_name,
                'pdf_base64': pdf_b64
            })

        from flask import make_response
        response = make_response(send_file(
            tmp_pdf,
            as_attachment=True,
            download_name=download_name,
            mimetype='application/pdf'
        ))
        
        token = request.args.get('download_token')
        if token:
            response.set_cookie('download_token', token, path='/')
            
        return response

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@dashboard_pegawai_bp.route('/api/shift-info', methods=['GET'])
def api_shift_info():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    target_id = request.args.get('target_id')
    nip = session.get('nip')

    if not target_id:
        return jsonify({'success': False, 'error': 'Target ID missing'}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s", (nip,))
        user = cursor.fetchone()
        if not user:
            return jsonify({'success': False, 'error': 'User not found'}), 404
        current_id = user['pegawai_id']

        start_date_str = request.args.get('start_date')
        end_date_str = request.args.get('end_date')

        if not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Tanggal mulai dan selesai harus diisi'}), 400

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            if start_date > end_date:
                return jsonify({'success': False, 'error': 'Tanggal mulai tidak boleh lebih dari tanggal selesai'}), 400
        except ValueError:
            return jsonify({'success': False, 'error': 'Format tanggal tidak valid'}), 400

        def fetch_schedule(peg_id):
            cursor.execute("""
                SELECT 
                    s.tgl_shift, 
                    s.jk_id, 
                    j.jk_name
                FROM shift_result_new s
                LEFT JOIN jam_kerja j ON s.jk_id = j.jk_id
                WHERE s.pegawai_id = %s AND s.tgl_shift BETWEEN %s AND %s
                ORDER BY s.tgl_shift
            """, (peg_id, start_date, end_date))
            results = cursor.fetchall()
            
            schedule_map = {}
            for r in results:
                tgl_str = r['tgl_shift'].strftime('%Y-%m-%d') if hasattr(r['tgl_shift'], 'strftime') else str(r['tgl_shift'])
                schedule_map[tgl_str] = {
                    'jk_id': r['jk_id'],
                    'jk_name': r['jk_name'] or str(r['jk_id'])
                }
            return schedule_map

        current_schedule = fetch_schedule(current_id)
        target_schedule = fetch_schedule(target_id)

        NON_SWAPPABLE = ['cuti', 'dinas', 'libur', 'sakit', 'izin']
        def is_non_swap(name):
            return any(kw in (name or '').lower() for kw in NON_SWAPPABLE)

        # Find rekan's "base" working shift (first swappable shift found in range)
        rekan_base = None
        for info in target_schedule.values():
            if not is_non_swap(info['jk_name']):
                rekan_base = info
                break

        # Find pegawai's "base" working shift
        pegawai_base = None
        for info in current_schedule.values():
            if not is_non_swap(info['jk_name']):
                pegawai_base = info
                break

        dates = []
        curr_dt = start_date
        while curr_dt <= end_date:
            dt_str = curr_dt.strftime('%Y-%m-%d')
            day_map = {0:'Senin', 1:'Selasa', 2:'Rabu', 3:'Kamis', 4:'Jumat', 5:'Sabtu', 6:'Minggu'}
            day_name = day_map[curr_dt.weekday()]

            curr_info = current_schedule.get(dt_str, {'jk_id': 10, 'jk_name': 'Libur'})
            tgt_info = target_schedule.get(dt_str, {'jk_id': 10, 'jk_name': 'Libur'})

            curr_anom = is_non_swap(curr_info['jk_name'])
            tgt_anom = is_non_swap(tgt_info['jk_name'])

            if curr_anom:
                # Pegawai sedang Cuti/Dinas/dll → Rekan dapat shift normal pegawai, pegawai tetap
                action = 'one_way_rev'
                new_curr = curr_info  # pegawai tetap (Cuti/Dinas/dll)
                new_tgt = pegawai_base if pegawai_base else tgt_info
            elif tgt_anom:
                # Rekan Cuti/Dinas/dll → Pegawai dapat shift normal rekan, rekan tetap
                action = 'one_way'
                new_curr = rekan_base if rekan_base else curr_info
                new_tgt = tgt_info  # stay unchanged
            else:
                # Normal: full swap
                action = 'swap'
                new_curr = tgt_info
                new_tgt = curr_info

            dates.append({
                'date': dt_str,
                'day_name': day_name,
                'current_shift': curr_info['jk_name'],
                'current_jk_id': curr_info['jk_id'],
                'target_shift': tgt_info['jk_name'],
                'target_jk_id': tgt_info['jk_id'],
                'new_current_shift': new_curr['jk_name'],
                'new_current_jk_id': new_curr['jk_id'],
                'new_target_shift': new_tgt['jk_name'],
                'new_target_jk_id': new_tgt['jk_id'],
                'action': action
            })
            curr_dt += timedelta(days=1)

        return jsonify({
            'success': True,
            'start_date': start_date.strftime('%d %b %Y'),
            'end_date': end_date.strftime('%d %b %Y'),
            'schedule': dates
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if connection and connection.is_connected():
            cursor.close()
            connection.close()


@dashboard_pegawai_bp.route('/api/swap-shift', methods=['POST'])
def api_swap_shift():
    if 'loggedin' not in session or session.get('role') != 'pegawai':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data = request.get_json() or {}
    target_id = data.get('target_id')
    nip = session.get('nip')
    tim = data.get('tim')
    leader = data.get('leader')
    supervisor = data.get('supervisor')
    alasan = data.get('alasan')
    selected_shifts = data.get('selected_shifts', [])

    if not target_id:
        return jsonify({'success': False, 'error': 'Target ID missing'}), 400

    if not tim or not leader or not supervisor or not alasan:
        return jsonify({'success': False, 'error': 'Pastikan semua form (Tim, Leader, Supervisor, Alasan) terisi'}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT pegawai_id, pembagian1_id, pegawai_nip, pegawai_nik_lintas, pegawai_nama FROM pegawai WHERE pegawai_nip = %s", (nip,))
        user = cursor.fetchone()
        
        cursor.execute("SELECT pegawai_id, pembagian1_id, pegawai_nip, pegawai_nama FROM pegawai WHERE pegawai_id = %s", (target_id,))
        target = cursor.fetchone()

        if not user or not target or user['pembagian1_id'] != target['pembagian1_id']:
            return jsonify({'success': False, 'error': 'Target tidak valid atau beda divisi.'}), 403
            
        current_id = user['pegawai_id']
        
        start_date_str = data.get('start_date')
        end_date_str = data.get('end_date')

        if not start_date_str or not end_date_str:
            return jsonify({'success': False, 'error': 'Tanggal mulai dan selesai harus diisi'}), 400

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
            if start_date > end_date:
                return jsonify({'success': False, 'error': 'Tanggal mulai tidak boleh lebih dari tanggal selesai'}), 400
        except ValueError:
            return jsonify({'success': False, 'error': 'Format tanggal tidak valid'}), 400

        # Fetch current assignments
        cursor.execute("SELECT s.tgl_shift, s.jk_id, j.jk_name FROM shift_result_new s LEFT JOIN jam_kerja j ON s.jk_id = j.jk_id WHERE s.pegawai_id = %s AND s.tgl_shift BETWEEN %s AND %s", (current_id, start_date, end_date))
        curr_shifts_full = cursor.fetchall()
        curr_shifts = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_id'] for row in curr_shifts_full}
        curr_shift_names = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_name'] for row in curr_shifts_full}

        cursor.execute("SELECT s.tgl_shift, s.jk_id, j.jk_name FROM shift_result_new s LEFT JOIN jam_kerja j ON s.jk_id = j.jk_id WHERE s.pegawai_id = %s AND s.tgl_shift BETWEEN %s AND %s", (target_id, start_date, end_date))
        tgt_shifts_full = cursor.fetchall()
        tgt_shifts = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_id'] for row in tgt_shifts_full}
        tgt_shift_names = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_name'] for row in tgt_shifts_full}

        NON_SWAPPABLE = ['cuti', 'dinas', 'libur', 'sakit', 'izin']
        def is_non_swap(name):
            return any(kw in (name or '').lower() for kw in NON_SWAPPABLE)

        # Find rekan's base working shift (first non-anomaly in range)
        rekan_base_jk = None
        for row in tgt_shifts_full:
            if not is_non_swap(row['jk_name'] or ''):
                rekan_base_jk = row['jk_id']
                break

        # Find pegawai's base working shift
        pegawai_base_jk = None
        for row in curr_shifts_full:
            if not is_non_swap(row['jk_name'] or ''):
                pegawai_base_jk = row['jk_id']
                break

        # Perform auto-smart swaps
        curr_dt = start_date
        while curr_dt <= end_date:
            dt_str = curr_dt.strftime('%Y-%m-%d')
            curr_jk = curr_shifts.get(dt_str)
            tgt_jk = tgt_shifts.get(dt_str)
            curr_nm = curr_shift_names.get(dt_str, 'Libur')
            tgt_nm = tgt_shift_names.get(dt_str, 'Libur')

            if curr_jk is None or tgt_jk is None:
                curr_dt += timedelta(days=1)
                continue

            curr_anom = is_non_swap(curr_nm)
            tgt_anom = is_non_swap(tgt_nm)

            if curr_anom:
                # Pegawai sedang Cuti/Dinas/dll → Rekan dapat shift normal pegawai, pegawai tetap
                if pegawai_base_jk:
                    cursor.execute("""
                        UPDATE shift_result_new SET jk_id = %s
                        WHERE pegawai_id = %s AND tgl_shift = %s
                    """, (pegawai_base_jk, target_id, dt_str))
            elif tgt_anom:
                # Rekan Cuti/Dinas/dll → Pegawai dapat shift normal rekan, rekan tetap
                if rekan_base_jk:
                    cursor.execute("""
                        UPDATE shift_result_new SET jk_id = %s
                        WHERE pegawai_id = %s AND tgl_shift = %s
                    """, (rekan_base_jk, current_id, dt_str))
            else:
                # Full swap
                cursor.execute("""
                    UPDATE shift_result_new SET jk_id = %s
                    WHERE pegawai_id = %s AND tgl_shift = %s
                """, (tgt_jk, current_id, dt_str))
                cursor.execute("""
                    UPDATE shift_result_new SET jk_id = %s
                    WHERE pegawai_id = %s AND tgl_shift = %s
                """, (curr_jk, target_id, dt_str))

            curr_dt += timedelta(days=1)

        # Insert to pengajuan_tukar_shift
        # Format nomor surat: NIP/FTS/DDMMYYYY
        import random
        nomor_surat = f"{user['pegawai_nip']}/FTS/{start_date.strftime('%d%m%Y')}"
        
        while True:
            new_id_tukar = random.randint(10000000, 99999999)
            cursor.execute("SELECT 1 FROM pengajuan_tukar_shift WHERE id_tukar_shift = %s", (new_id_tukar,))
            if not cursor.fetchone():
                break
        
        shift_awal_id = curr_shifts.get(start_date_str, 0)
        shift_awal_nama = curr_shift_names.get(start_date_str, 'Libur')
        shift_baru_id = tgt_shifts.get(start_date_str, 0)
        shift_baru_nama = tgt_shift_names.get(start_date_str, 'Libur')
        
        cursor.execute("""
            INSERT INTO pengajuan_tukar_shift (
                id_tukar_shift, pegawai_id, pegawai_nip, pegawai_nik_lintas, nama_pegawai, nomor_surat, tim,
                tanggal_mulai, tanggal_akhir, shift_awal_id, shift_awal_nama,
                shift_baru_id, shift_baru_nama, pegawai_tukar_id, pegawai_tukar_nip,
                pegawai_tukar_nama, alasan_tukar_shift, nama_leader, nama_supervisor, status
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OK'
            )
        """, (
            new_id_tukar, user['pegawai_id'], user['pegawai_nip'], user.get('pegawai_nik_lintas', ''), user['pegawai_nama'],
            nomor_surat, tim, start_date, end_date, shift_awal_id, shift_awal_nama,
            shift_baru_id, shift_baru_nama, target['pegawai_id'], target['pegawai_nip'],
            target['pegawai_nama'], alasan, leader, supervisor
        ))

        connection.commit()
        return jsonify({'success': True, 'message': 'Shift berhasil ditukar!', 'id_tukar_shift': new_id_tukar})

    except Exception as e:
        if 'connection' in locals() and connection.is_connected():
            connection.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if 'connection' in locals() and connection and connection.is_connected():
            cursor.close()
            connection.close()

