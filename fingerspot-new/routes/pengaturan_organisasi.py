from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from utils.db import get_db_connection

pengaturan_organisasi_bp = Blueprint('pengaturan_organisasi', __name__)

@pengaturan_organisasi_bp.route('/pengaturan-organisasi', methods=['GET'])
def index():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    
    data_divisi = []
    data_fungsi = []
    data_tim = []

    try:
        cursor.execute("SELECT pembagian1_id as id, pembagian1_nama as nama, pembagian1_ket as ket FROM pembagian1 ORDER BY pembagian1_id ASC")
        data_divisi = cursor.fetchall()

        cursor.execute("SELECT pembagian2_id as id, pembagian2_nama as nama, pembagian2_ket as ket FROM pembagian2 ORDER BY pembagian2_id ASC")
        data_fungsi = cursor.fetchall()

        cursor.execute("SELECT pembagian3_id as id, pembagian3_nama as nama, pembagian3_ket as ket FROM pembagian3 ORDER BY pembagian3_id ASC")
        data_tim = cursor.fetchall()

    except Exception as e:
        print(f"Error getting organisasi data: {e}")
    finally:
        cursor.close()
        connection.close()

    return render_template('pengaturan-organisasi.html', 
                           data_divisi=data_divisi, 
                           data_fungsi=data_fungsi, 
                           data_tim=data_tim)

@pengaturan_organisasi_bp.route('/api/organisasi/<tipe>/simpan', methods=['POST'])
def simpan(tipe):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        data = request.json
        item_id = data.get('id')
        nama = data.get('nama')
        ket = data.get('ket', '')

        if not nama:
            return jsonify({'status': 'error', 'message': 'Nama wajib diisi'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        # Konfigurasi berdasarkan tipe
        if tipe == 'divisi':
            table = 'pembagian1'
            id_col = 'pembagian1_id'
            nama_col = 'pembagian1_nama'
            ket_col = 'pembagian1_ket'
            is_auto_increment = False
        elif tipe == 'fungsi':
            table = 'pembagian2'
            id_col = 'pembagian2_id'
            nama_col = 'pembagian2_nama'
            ket_col = 'pembagian2_ket'
            is_auto_increment = True
        elif tipe == 'tim':
            table = 'pembagian3'
            id_col = 'pembagian3_id'
            nama_col = 'pembagian3_nama'
            ket_col = 'pembagian3_ket'
            is_auto_increment = False
        else:
            return jsonify({'status': 'error', 'message': 'Tipe tidak valid'}), 400

        if item_id:
            # Update existing
            query = f"UPDATE {table} SET {nama_col} = %s, {ket_col} = %s WHERE {id_col} = %s"
            cursor.execute(query, (nama, ket, item_id))
            message = f"Data {tipe} berhasil diperbarui."
        else:
            # Insert new
            if not is_auto_increment:
                # Find max id
                cursor.execute(f"SELECT MAX({id_col}) FROM {table}")
                max_id = cursor.fetchone()[0]
                new_id = (max_id + 1) if max_id is not None else 1
                
                query = f"INSERT INTO {table} ({id_col}, {nama_col}, {ket_col}) VALUES (%s, %s, %s)"
                cursor.execute(query, (new_id, nama, ket))
            else:
                query = f"INSERT INTO {table} ({nama_col}, {ket_col}) VALUES (%s, %s)"
                cursor.execute(query, (nama, ket))
            message = f"Data {tipe} berhasil ditambahkan."

        connection.commit()
        return jsonify({'status': 'success', 'message': message})

    except Exception as e:
        print(f"Error saving {tipe}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()

@pengaturan_organisasi_bp.route('/api/organisasi/<tipe>/hapus', methods=['POST'])
def hapus(tipe):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        data = request.json
        item_id = data.get('id')

        if not item_id:
            return jsonify({'status': 'error', 'message': 'ID tidak valid'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        if tipe == 'divisi':
            table = 'pembagian1'
            id_col = 'pembagian1_id'
        elif tipe == 'fungsi':
            table = 'pembagian2'
            id_col = 'pembagian2_id'
        elif tipe == 'tim':
            table = 'pembagian3'
            id_col = 'pembagian3_id'
        else:
            return jsonify({'status': 'error', 'message': 'Tipe tidak valid'}), 400

        # Safety check: pastikan tidak dipakai di tabel pegawai
        cursor.execute(f"SELECT COUNT(*) FROM pegawai WHERE {id_col} = %s", (item_id,))
        count = cursor.fetchone()[0]
        if count > 0:
            return jsonify({'status': 'error', 'message': f'Tidak dapat menghapus karena sedang digunakan oleh {count} pegawai.'}), 400

        cursor.execute(f"DELETE FROM {table} WHERE {id_col} = %s", (item_id,))
        connection.commit()

        return jsonify({'status': 'success', 'message': f'Data {tipe} berhasil dihapus.'})

    except Exception as e:
        print(f"Error deleting {tipe}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()
