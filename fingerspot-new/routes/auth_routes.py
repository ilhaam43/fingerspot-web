from flask import Blueprint, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime
import pytz
import string
import random
import re
from utils.db import get_db_connection, Error

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

@auth_bp.route('/login', methods=['GET', 'POST'])
def auth_login():
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
            return redirect(url_for('main.index'))

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
                return render_template('auth/login.html', force_reset=True)
            return redirect(url_for('dashboard_pegawai.dasbor_pegawai'))
        else:
            cursor.close()
            conn.close()
            return render_template('auth/login.html', force_reset=False, error='Username atau password salah.')

    preview = request.args.get('new') == '1'
    # Menggunakan folder auth/ untuk template
    template_name = 'auth/login_new.html' if preview else 'auth/login.html'

    return render_template(template_name, force_reset=False)

@auth_bp.route('/forgot-password', methods=['POST'])
def auth_forgot_password():
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
     
        return jsonify({'success': True, 'message': 'Data valid, silakan masukkan password baru.'})

    except Error as e:
        return jsonify({'success': False, 'error': f'Gagal memproses: {str(e)}'})

    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

@auth_bp.route('/direct-reset-password', methods=['POST'])
def auth_direct_reset_password():
    data = request.get_json()
    nip = data.get('nip')
    card_id = data.get('card_id')
    new_password = data.get('new_password')

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
 
        if not nip or not card_id or not new_password:
            return jsonify({'success': False, 'error': 'NIP, ID kartu, dan password baru harus diisi'})
        if not card_id.isdigit() or len(card_id) != 8:
            return jsonify({'success': False, 'error': 'ID kartu Lintasarta harus 8 digit'})
       
        cursor.execute("SELECT pegawai_nip FROM pegawai WHERE pegawai_nip = %s AND pegawai_status = 1", (nip,))
        user = cursor.fetchone()

        if not user:
            return jsonify({'success': False, 'error': 'NIP enggak ditemukan atau akun enggak aktif'})
       
        if card_id[-4:] != nip:
            return jsonify({'success': False, 'error': 'ID kartu enggak sesuai sama NIP'})
     
        if len(new_password) < 6:
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})
        if not re.search(r"[A-Z]", new_password):
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})
        if not re.search(r"[0-9]", new_password):
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})
        if not re.search(r"[!@#$%^&*()_+\-=\[\]{};:'\"\\|,.<>/?]", new_password):
            return jsonify({'success': False, 'error': 'Password harus minimal 6 karakter, 1 huruf besar, 1 angka dan 1 karakter khusus'})

        cursor.execute("UPDATE pegawai SET pegawai_pwd = %s WHERE pegawai_nip = %s", (new_password, nip))
        conn.commit()
        return jsonify({'success': True, 'message': 'Password berhasil direset, silakan login dengan password baru.'})

    except Error as e:
        return jsonify({'success': False, 'error': f'Gagal memproses: {str(e)}'})

    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

# Endpoint ini masih dipakai untuk:
# 1. Pegawai lama yang statusnya masih 4 dari sisa alur reset password lama.
# 2. Pegawai baru yang diset statusnya menjadi 4 oleh admin agar wajib mengganti password saat pertama login.
@auth_bp.route('/set-new-password', methods=['POST'])
def auth_set_new_password():
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
        return jsonify({'success': True, 'message': 'Password berhasil diubah', 'redirect': url_for('dashboard_pegawai.dasbor_pegawai')})

    except Error as e:
        return jsonify({'success': False, 'error': f'Gagal mengubah password: {str(e)}'})

    finally:
        if 'conn' in locals() and conn.is_connected():
            cursor.close()
            conn.close()

@auth_bp.route('/logout')
def auth_logout():
    login_time = datetime.now(pytz.timezone('Asia/Jakarta')).strftime('%Y-%m-%d %H:%M:%S %Z')
    role = session.get('role')
    user_info = "Unknown"
    
    if role == 'admin':
        user_info = f"Admin, Username={session.get('username', 'Unknown')}"
    elif role == 'pegawai':
        user_info = f"Pegawai, NIP={session.get('nip', 'Unknown')}, Name={session.get('nama', 'Unknown')}"
    
    session.clear()
    return redirect(url_for('auth.auth_login'))
