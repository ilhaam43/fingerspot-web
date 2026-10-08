import os
from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from utils.db import get_db_connection

pegawai_bp = Blueprint('pegawai', __name__)

# helper functions
def get_pembagian_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian1_id, pembagian1_nama FROM pembagian1 ORDER BY pembagian1_nama")
    pembagian1 = cursor.fetchall()
    cursor.close()
    conn.close()
    return pembagian1

def get_pembagian2_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian2_id, pembagian2_nama FROM pembagian2 ORDER BY pembagian2_nama")
    pembagian2 = cursor.fetchall()
    cursor.close()
    conn.close()
    return pembagian2

def get_pembagian3_list():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT pembagian3_id, pembagian3_nama FROM pembagian3 ORDER BY pembagian3_nama")
    pembagian3 = cursor.fetchall()
    cursor.close()
    conn.close()
    return pembagian3

@pegawai_bp.route('/data-pegawai')
def data_pegawai():
    # halaman master data pegawai – daftar semua karyawan
    # admin bisa lihat detail, edit, hapus dari sini
    # admin qaqc dibatasi hanya lihat pegawai divisi QAQC dan TA

    # cek login dan role admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))
    
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
    return render_template('data-pegawai.html', pegawai=pegawai)





@pegawai_bp.route('/data-pegawai/tambah', methods=['GET', 'POST'])
def tambah_pegawai():
    # halaman tambah pegawai baru – hanya admin biasa
    # GET: tampilkan form kosong + dropdown divisi/jabatan/tim
    # POST: simpan data baru ke tabel pegawai dengan password default

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

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
        return redirect(url_for('pegawai.data_pegawai'))

    # kalau GET: ambil daftar untuk dropdown
    pembagian1 = get_pembagian_list() 
    pembagian2 = get_pembagian2_list()
    pembagian3 = get_pembagian3_list()
    # tampilkan form create
    return render_template('tambah-pegawai.html', pembagian1=pembagian1, pembagian2=pembagian2, pembagian3=pembagian3)


@pegawai_bp.route('/data-pegawai/edit/<int:pegawai_id>', methods=['GET', 'POST'])
def edit_pegawai(pegawai_id):
    # halaman edit data pegawai tertentu
    # GET: tampilkan form dengan data pegawai yang sudah ada + dropdown
    # POST: simpan perubahan (password tidak diubah di sini)

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # ambil data pegawai berdasarkan id
        cursor.execute("SELECT * FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
        pegawai = cursor.fetchone()

        # kalau tidak ketemu, balik ke daftar
        if not pegawai:
            return redirect(url_for('pegawai.data_pegawai'))

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
            return redirect(url_for('pegawai.data_pegawai'))
        
        # kalau GET: ambil daftar dropdown
        pembagian1 = get_pembagian_list()
        pembagian2 = get_pembagian2_list()
        pembagian3 = get_pembagian3_list()
        return render_template('edit-pegawai.html', pegawai=pegawai, pembagian1=pembagian1, pembagian2=pembagian2, pembagian3=pembagian3)

    except Exception as e:
        # kalau error, tetap tampilkan form dengan data existing
        return render_template('edit-pegawai.html', pegawai=pegawai, pembagian1=get_pembagian_list(), pembagian2=get_pembagian2_list(), pembagian3=get_pembagian3_list())
    finally:
        # pastikan koneksi ditutup
        cursor.close()
        conn.close()

@pegawai_bp.route('/data-pegawai/hapus/<int:pegawai_id>', methods=['POST'])
def hapus_pegawai(pegawai_id):
    # route hapus pegawai – dipanggil via POST (biasanya tombol konfirmasi)
    # hard delete langsung dari tabel pegawai

    # cek login admin
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))
    
    conn = get_db_connection()
    cursor = conn.cursor()
    # hapus pegawai berdasarkan id
    cursor.execute("DELETE FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
    conn.commit()
    cursor.close()
    conn.close()

    # setelah hapus, balik ke daftar pegawai
    return redirect(url_for('pegawai.data_pegawai'))

