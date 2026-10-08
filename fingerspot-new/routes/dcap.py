from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app, jsonify
import pandas as pd
import os
import json
from datetime import datetime, timedelta, date, time
from werkzeug.utils import secure_filename
from utils.db import get_db_connection
import numpy as np

dcap_bp = Blueprint('dcap', __name__)

@dcap_bp.route('/upload-dcap', methods=['GET', 'POST'])
def upload_dcap():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    if request.method == 'POST':
        if 'file' not in request.files:
            return redirect(request.url)
        file = request.files['file']
        if file.filename == '':
            return redirect(request.url)
        if file:
            filename = os.path.join(current_app.config['UPLOAD_FOLDER'], file.filename)
            file.save(filename)
            return redirect(url_for('dcap.hasil_upload_dcap', filename=file.filename))
            
    return render_template('upload-dcap.html')

@dcap_bp.route('/hasil-upload-dcap/<filename>')
def hasil_upload_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    
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
        
        return render_template('hasil-upload-dcap.html', tables=table_html, filename=filename)
    except Exception as e:
        return f"Gagal membaca file: {str(e)}"

@dcap_bp.route('/konversi-dcap/<filename>')
def konversi_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    
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
        
        return render_template('konversi-dcap.html', tables=table_html, filename=filename)
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

@dcap_bp.route('/mapping-dcap')
def mapping_dcap():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))
        
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
        w_in = str(waktu_masuk_str).strip()
        if len(w_in) == 5: w_in += ':00'
        waktu_masuk_time = pd.to_datetime(w_in, format='%H:%M:%S').time() if w_in and w_in != '00:00:00' else None
    except:
        waktu_masuk_time = None
        
    try:
        w_out = str(waktu_keluar_str).strip()
        if len(w_out) == 5: w_out += ':00'
        waktu_keluar_time = pd.to_datetime(w_out, format='%H:%M:%S').time() if w_out and w_out != '00:00:00' else None
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

@dcap_bp.route('/terapkan-aturan-dcap/<filename>')
def terapkan_aturan_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    
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

        kolom_pilihan = ['Pin', 'Nama', 'Tanggal', 'Waktu Masuk', 'Waktu Keluar', 'Lokasi Masuk', 'NIK', 'NIP', 'Departemen', 'Divisi']
        kolom_yang_ada = [col for col in kolom_pilihan if col in df.columns]
        df = df[kolom_yang_ada]
        
        if 'Departemen' in df.columns and 'Divisi' not in df.columns:
            df.rename(columns={'Departemen': 'Divisi'}, inplace=True)
        
        if 'Pin' in df.columns:
            df['Pin'] = pd.to_numeric(df['Pin'], errors='coerce').astype('Int64')
            
        if 'Tanggal' in df.columns:
            # Format tanggal ke YYYY-MM-DD agar mudah dicocokkan dengan DB
            df['Tanggal'] = pd.to_datetime(df['Tanggal'], errors='coerce').dt.strftime('%Y-%m-%d')

        # Koneksi ke database untuk mengecek jk_id
        connection = get_db_connection()
        cursor = connection.cursor()

        if 'Divisi' not in df.columns:
            df['Divisi'] = ''
        df['Scan In Final'] = None
        df['Scan Out Final'] = None

        malam_jk_ids = {7, 15, 18, 22}

        for index, row in df.iterrows():
            pin_val = row.get('Pin')
            nip_val = row.get('NIP')
            
            # Clean up nip string to prevent 2475.0 or spaces
            def clean_nip(v):
                if not pd.notna(v): return None
                s = str(v).strip()
                if s.endswith('.0'): s = s[:-2]
                return s if s else None

            nip = clean_nip(pin_val) or clean_nip(nip_val)
            
            tanggal_shift = row.get('Tanggal')
            waktu_masuk = row.get('Waktu Masuk')
            waktu_keluar = row.get('Waktu Keluar')

            if not nip or not tanggal_shift:
                continue

            # Ambil jk_id
            query = '''
                SELECT jk.jk_id
                FROM pegawai p
                LEFT JOIN shift_result_new sr ON p.pegawai_id = sr.pegawai_id AND sr.tgl_shift = %s
                LEFT JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
                WHERE p.pegawai_nip = %s
            '''
            cursor.execute(query, (tanggal_shift, nip))
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
        
        return render_template('terapkan-aturan-dcap.html', tables=table_html, filename=filename)
    except Exception as e:
        return f"Gagal membaca/apply rules file: {str(e)}"

@dcap_bp.route('/simpan-dcap/<filename>', methods=['POST'])
def simpan_dcap(filename):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], filename)
    
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

        if 'Pin' in df.columns:
            df['Pin'] = pd.to_numeric(df['Pin'], errors='coerce').astype('Int64')

        # Read selected records from frontend
        selected_records_json = request.form.get('selected_records', '[]')
        try:
            import json
            selected_records = json.loads(selected_records_json)
        except Exception:
            selected_records = []
            
        allowed_keys = set()
        for rec in selected_records:
            nip = str(rec.get('nip', '')).strip()
            pin = str(rec.get('pin', '')).strip()
            tgl_raw = str(rec.get('tanggal', '')).strip()
            
            try:
                if '/' in tgl_raw:
                    pts = tgl_raw.split('/')
                    if len(pts) == 3:
                        tgl = f"{pts[2]}-{pts[1]}-{pts[0]}"
                else:
                    tgl = pd.to_datetime(tgl_raw).strftime('%Y-%m-%d')
            except:
                tgl = tgl_raw
                
            if nip and tgl:
                allowed_keys.add(f"{nip}_{tgl}")
            elif pin and tgl:
                allowed_keys.add(f"{pin}_{tgl}")
            
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
                
            if f"{nip}_{tanggal_shift}" not in allowed_keys:
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
                    SET scan_in_source = CASE WHEN (scan_in IS NULL OR scan_in = '0000-00-00 00:00:00') AND %s != '0000-00-00 00:00:00' THEN 'DCAP' ELSE scan_in_source END,
                        scan_in = CASE WHEN scan_in IS NULL OR scan_in = '0000-00-00 00:00:00' THEN %s ELSE scan_in END,
                        scan_out_source = CASE WHEN (scan_out IS NULL OR scan_out = '0000-00-00 00:00:00') AND %s != '0000-00-00 00:00:00' THEN 'DCAP' ELSE scan_out_source END,
                        scan_out = CASE WHEN scan_out IS NULL OR scan_out = '0000-00-00 00:00:00' THEN %s ELSE scan_out END 
                    WHERE pegawai_id = %s AND tgl_shift = %s
                ''', (db_scan_in, db_scan_in, db_scan_out, db_scan_out, pegawai_id, tanggal_shift))
            else:
                cursor.execute('''
                    INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out, scan_in_source, scan_out_source)
                    VALUES (%s, %s, %s, %s, %s, %s)
                ''', (
                    pegawai_id, tanggal_shift, db_scan_in, db_scan_out, 
                    'DCAP' if db_scan_in != '0000-00-00 00:00:00' else None, 
                    'DCAP' if db_scan_out != '0000-00-00 00:00:00' else None
                ))

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
        
        return jsonify({'status': 'success', 'message': 'Data DCAP berhasil diproses dan disimpan ke database!'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': f"Gagal memproses save ke DB: {str(e)}"})


