from flask import Blueprint, render_template, request, redirect, url_for, session, send_file
from datetime import datetime, timedelta, date, time
from utils.db import get_db_connection

main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    # route ini adalah dashboard utama admin
    # tugasnya: tampilkan rekap absensi harian semua pegawai dalam rentang tanggal tertentu
    # plus statistik, filter nama/divisi/keterangan, dan opsi tampilkan PM (pegawai id 180)
    
    # pertama-tama cek apakah user sudah login dan benar-benar admin
    # kalau belum, langsung arahkan ke halaman login
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))
    
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
    keterangan = request.args.getlist('keterangan')
    search = request.args.get('search_name')
    pembagian1_nama = request.args.getlist('pembagian1_nama')
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
            
        # ambil aturan puasa
        cursor.execute("""
            SELECT tanggal_mulai, tanggal_selesai, potongan_menit 
            FROM aturan_puasa 
            WHERE tanggal_mulai <= %s AND tanggal_selesai >= %s
        """, (end_date, start_date))
        aturan_puasa_rows = cursor.fetchall()
        
        def get_potongan_puasa(tgl):
            if not isinstance(tgl, date):
                return 0
            for aturan in aturan_puasa_rows:
                if aturan['tanggal_mulai'] <= tgl <= aturan['tanggal_selesai']:
                    return aturan['potongan_menit']
            return 0
        
        # query utama: ambil semua data absensi + info pegawai + shift + divisi
        # hanya ambil pegawai aktif (status != 0) dan bukan id 0
        query = """
            SELECT sr.pegawai_id, p.pegawai_nip, p.pegawai_nama, sr.tgl_shift, 
                   sr.scan_in, sr.scan_out, sr.scan_in_source, sr.scan_out_source, jk.jk_id, jk.jk_bcin, jk.jk_ecout, jk.jk_name,
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
        
        # filter divisi (bisa lebih dari satu)
        if pembagian1_nama:
            valid_divs = []
            for d in pembagian1_nama:
                if d == "HDTI-HDBS":
                    valid_divs.extend(["HDTI", "HDBS"])
                elif d == "QAQC-TA":
                    valid_divs.extend(["QAQC", "TA"])
                elif d:
                    valid_divs.append(d)
            if valid_divs:
                placeholders = ', '.join(['%s'] * len(valid_divs))
                query += f" AND pb1.pembagian1_nama IN ({placeholders})"
                params.extend(valid_divs)
        
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
                            'scan_in_source': None,
                            'scan_out_source': None,
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
                        
                        # kalau puasa, pulang lebih awal sesuai aturan puasa dinamis
                        potongan_menit = get_potongan_puasa(tgl_shift)
                        if potongan_menit > 0:
                            ec_time_limit = (jk_ecout_eval - timedelta(minutes=potongan_menit)).time()
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
            record['raw_scan_in'] = scan_in.strftime('%Y-%m-%dT%H:%M') if scan_in else ''
            record['raw_scan_out'] = scan_out.strftime('%Y-%m-%dT%H:%M') if scan_out else ''

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
                    # tampilan puasa dinamis: kurangi menit hanya di layar
                    potongan_menit = get_potongan_puasa(tgl_shift)
                    if potongan_menit > 0:
                        adjusted_display = (jk_ecout_display - timedelta(minutes=potongan_menit))
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

@main_bp.route('/reload-shift', methods=['GET', 'POST'])
def reload_shift():
    # route ini untuk fitur copy jadwal shift massal oleh admin
    # admin pilih tanggal referensi dan target, lalu copy jk_id dari referensi ke target
    # bisa untuk satu divisi atau semua (kecuali nip tertentu)
    # ada opsi apakah ikut copy shift BIFAST atau tidak
    # juga bisa pilih apakah cuti/dinas/sakit/izin ikut dicopy atau tidak

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

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



    
@main_bp.route('/export-laporan-excel', methods=['GET'])
def export_laporan_excel():
    from datetime import datetime, timedelta, time, date
    import pandas as pd
    import os
    
    # route ini untuk export rekap absensi ke file excel
    # dipakai dari dashboard admin, hasilnya sama persis dengan tampilan dashboard tapi dalam format xlsx
    # user bisa filter tanggal, nama, divisi, keterangan, dan opsi tampilkan PM
    
    # ambil semua parameter filter dari url (sama seperti dashboard)
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    keterangan = request.args.getlist('keterangan')
    search = request.args.get('search_name')
    # untuk excel, namanya 'division_ext'
    pembagian1_nama = request.args.getlist('division_ext')
    # checkbox show_pm default nyala
    show_pm = request.args.get('show_pm', 'on') == 'on'
    
    # siapkan nama file dan folder reports
    today = datetime.now().strftime('%d-%m-%Y')
    today2 = datetime.now().strftime('%d-%b-%Y')
    reports_dir = "reports"
    os.makedirs(reports_dir, exist_ok=True)

    # nama file output: kalau ada divisi spesifik, sertakan nama divisi di filename
    if pembagian1_nama:
        div_str = "-".join(pembagian1_nama)
        output_file = os.path.join(reports_dir, f'report_{div_str}_{start_date}.xlsx')
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
        
        # filter divisi (bisa lebih dari satu)
        if pembagian1_nama:
            valid_divs = []
            for d in pembagian1_nama:
                if d == "HDTI-HDBS":
                    valid_divs.extend(["HDTI", "HDBS"])
                elif d == "QAQC-TA":
                    valid_divs.extend(["QAQC", "TA"])
                elif d:
                    valid_divs.append(d)
            if valid_divs:
                placeholders = ', '.join(['%s'] * len(valid_divs))
                query += f" AND pb.pembagian1_nama IN ({placeholders})"
                params.extend(valid_divs)

        # jalankan query dan ambil semua record
        cursor.execute(query, params)
        records = cursor.fetchall()
        
        # ambil aturan puasa
        cursor.execute("""
            SELECT tanggal_mulai, tanggal_selesai, potongan_menit 
            FROM aturan_puasa 
            WHERE tanggal_mulai <= %s AND tanggal_selesai >= %s
        """, (end_date, start_date))
        aturan_puasa_rows = cursor.fetchall()
        
        def get_potongan_puasa(tgl):
            if not isinstance(tgl, date):
                return 0
            for aturan in aturan_puasa_rows:
                if aturan['tanggal_mulai'] <= tgl <= aturan['tanggal_selesai']:
                    return aturan['potongan_menit']
            return 0

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
                    record['keterangan'] = "Libur"
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
                record['keterangan'] = "OT" if (scan_in and scan_out) else "Libur"
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

                            # aturan puasa: pulang maju dinamis
                            potongan_menit = get_potongan_puasa(tgl_shift)
                            if potongan_menit > 0:
                                if isinstance(jk_ecout_time, datetime):
                                     ec_time_limit = (jk_ecout_time - timedelta(minutes=potongan_menit)).time()
                                else:
                                     ec_time_limit = (datetime.combine(date.min, jk_ecout_time) - timedelta(minutes=potongan_menit)).time()
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
            if record['keterangan'] in ["LB", "CT", "LDL", "LD", "S", "", "Libur"]:
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
                else:
                    record['jk_bcin'] = "-"
                
                if jk_ecout_time and jk_ecout_time != "-":
                    if isinstance(jk_ecout_time, time):  
                        jk_ecout_datetime = datetime.combine(date.today(), jk_ecout_time)
                    else:
                        jk_ecout_datetime = jk_ecout_time

                    # kalau puasa, tampilan pulang dikurangi dinamis
                    potongan_menit = get_potongan_puasa(tgl_shift)
                    if potongan_menit > 0:
                        adjusted_time = (jk_ecout_datetime - timedelta(minutes=potongan_menit)).time()
                        record['jk_ecout'] = adjusted_time.strftime('%H:%M')
                    else:
                        record['jk_ecout'] = jk_ecout_datetime.time().strftime('%H:%M')
                else:
                    record['jk_ecout'] = "-"

        # kalau ada filter keterangan, hapus record yang tidak cocok
        if keterangan:
            records = [record for record in records if record['keterangan'] in keterangan]

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
        return redirect(url_for('main.index'))
    finally:
        # tutup koneksi db
        if connection.is_connected():
            cursor.close()
            connection.close()



@main_bp.route('/edit-jadwal-shift-peruser', methods=['GET', 'POST'])
def edit_jadwal_shift_peruser():
    # route ini untuk koreksi manual jadwal dan scan absensi satu pegawai satu tanggal
    # admin bisa ubah jk_id (shift), scan_in, scan_out dari form edit
    # kalau POST: simpan perubahan pakai upsert (insert kalau belum ada, update kalau sudah)
    # kalau GET: tampilkan form edit dengan data existing + dropdown semua shift

    # cek dulu user sudah login sebagai admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    if request.method == 'POST':
        # ambil data dari form edit
        pegawai_id = request.form['pegawai_id']
        tgl_shift = request.form['tgl_shift']
        jk_id = request.form['jk_id']
        scan_in = request.form['scan_in']
        scan_out = request.form['scan_out']
        notes = request.form.get('notes', '')
        # simpan referrer supaya balik ke halaman sebelumnya (misal att_log atau dashboard)
        referrer = request.form.get('referrer', url_for('main.index'))

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
                notes = VALUES(notes),
                scan_in_source = CASE WHEN VALUES(scan_in) = '' OR VALUES(scan_in) = '0000-00-00 00:00:00' OR VALUES(scan_in) IS NULL THEN NULL ELSE scan_in_source END,
                scan_out_source = CASE WHEN VALUES(scan_out) = '' OR VALUES(scan_out) = '0000-00-00 00:00:00' OR VALUES(scan_out) IS NULL THEN NULL ELSE scan_out_source END
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
        referrer = request.referrer or url_for('main.index')
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



