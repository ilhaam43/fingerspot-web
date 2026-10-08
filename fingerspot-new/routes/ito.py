from flask import Blueprint, render_template, request, redirect, url_for, session, flash, current_app
from utils.db import get_db_connection
import os
import pandas as pd
import pickle
from datetime import datetime

ito_bp = Blueprint('ito', __name__)

@ito_bp.route('/upload-ito', methods=['GET', 'POST'])
def upload_ito():
    # route ini untuk halaman import data absensi dari web ITO (ito-attendance.lintasarta.co.id)
    # admin upload file excel export dari ITO, lalu preview data sebelum simpan
    # data difilter berdasarkan divisi, dan hanya status 'Late' atau 'Leave Early' yang ditampilkan

    # cek dulu user sudah login sebagai admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))
    
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
            filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], file.filename)
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
                    return render_template('upload-ito.html', records=None)
                
                # simpan dataframe ke file pickle supaya bisa dipakai di update_ito
                pkl_filename = f"uploaded_{datetime.now().strftime('%Y%m%d%H%M%S')}.pkl"
                pkl_filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], pkl_filename)
                with open(pkl_filepath, 'wb') as f:
                    pickle.dump(df, f)

                # simpan path pickle ke session
                session['uploaded_file_path'] = pkl_filepath
                
                # ubah ke list dict untuk template
                records = df.to_dict(orient='records')
                
                # tampilkan preview data
                return render_template('upload-ito.html', records=records, divisions=divisions)

            except Exception as e:
                # kalau error saat proses excel, balik ke halaman
                return redirect(request.url)
        else:
            return redirect(request.url)

    # kalau GET, tampilkan halaman upload dengan dropdown divisi
    return render_template('upload-ito.html', divisions=divisions)


from datetime import datetime

@ito_bp.route('/simpan-ito', methods=['POST'])
def simpan_ito():
    # route ini dipanggil setelah admin pilih record dari preview ITO
    # tugasnya: simpan scan in/out dari data ITO ke tabel shift_result_new
    # hanya overwrite kalau nilai lama '0000-00-00 00:00:00'

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    # ambil list record yang dipilih (format: nik|date)
    selected_records = request.form.getlist('selected_records')

    # kalau tidak ada yang dipilih, balik ke halaman ITO
    if not selected_records:
        return redirect(url_for('ito.upload_ito'))

    try:
        # cek ada file pickle dari preview sebelumnya
        if 'uploaded_file_path' not in session:
            return redirect(url_for('ito.upload_ito'))

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
                INSERT INTO shift_result_new (pegawai_id, tgl_shift, scan_in, scan_out, scan_in_source, scan_out_source) 
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    scan_in_source = CASE 
                        WHEN scan_in = '0000-00-00 00:00:00' AND VALUES(scan_in) != '0000-00-00 00:00:00' THEN 'ITO' 
                        ELSE scan_in_source 
                    END,
                    scan_in = CASE 
                        WHEN scan_in = '0000-00-00 00:00:00' THEN VALUES(scan_in) 
                        ELSE scan_in 
                    END,
                    scan_out_source = CASE 
                        WHEN scan_out = '0000-00-00 00:00:00' AND VALUES(scan_out) != '0000-00-00 00:00:00' THEN 'ITO' 
                        ELSE scan_out_source 
                    END,
                    scan_out = CASE 
                        WHEN scan_out = '0000-00-00 00:00:00' THEN VALUES(scan_out) 
                        ELSE scan_out 
                    END
            """

            cursor.execute(upsert_query, (
                pegawai_id[0], tgl_shift, scan_in, scan_out,
                'ITO' if scan_in != '0000-00-00 00:00:00' else None,
                'ITO' if scan_out != '0000-00-00 00:00:00' else None
            ))

        # commit semua perubahan
        connection.commit()
        flash('Data ITO berhasil disimpan ke database!', 'success')

    except Exception as e:
        # kalau error, skip saja (tidak crash) tapi munculkan pesan
        flash(f'Gagal menyimpan data ITO: {str(e)}', 'error')

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
    return redirect(url_for('ito.upload_ito'))



