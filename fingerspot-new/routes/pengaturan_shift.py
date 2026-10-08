from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from utils.db import get_db_connection

pengaturan_shift_bp = Blueprint('pengaturan_shift', __name__)

@pengaturan_shift_bp.route('/pengaturan-shift', methods=['GET'])
def index():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT jk_id, jk_name, jk_kode, jk_bcin, jk_ecout, jk_ket FROM jam_kerja ORDER BY jk_id ASC")
        data = cursor.fetchall()
    except Exception as e:
        print(f"Error getting jam_kerja: {e}")
        data = []
    finally:
        cursor.close()
        connection.close()

    return render_template('pengaturan-shift.html', data=data)

@pengaturan_shift_bp.route('/api/pengaturan-shift/simpan', methods=['POST'])
def simpan():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        data = request.json
        jk_id_req = data.get('jk_id')
        jk_name = data.get('jk_name')
        jk_kode = data.get('jk_kode')
        jk_bcin = data.get('jk_bcin')
        jk_ecout = data.get('jk_ecout')
        jk_ket = data.get('jk_ket')

        if not jk_name or not jk_bcin or not jk_ecout:
            return jsonify({'status': 'error', 'message': 'Nama Shift, Jam Masuk, dan Jam Pulang wajib diisi'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        if jk_id_req:
            # Update existing
            cursor.execute("""
                UPDATE jam_kerja 
                SET jk_name = %s, jk_kode = %s, jk_bcin = %s, jk_ecout = %s, jk_ket = %s
                WHERE jk_id = %s
            """, (jk_name, jk_kode, jk_bcin, jk_ecout, jk_ket, jk_id_req))
            message = "Data shift berhasil diperbarui."
        else:
            # Insert new
            # Find the max jk_id
            cursor.execute("SELECT MAX(jk_id) FROM jam_kerja")
            max_id = cursor.fetchone()[0]
            new_id = (max_id + 1) if max_id is not None else 1

            cursor.execute("""
                INSERT INTO jam_kerja (jk_id, jk_name, jk_kode, jk_bcin, jk_ecout, jk_ket)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (new_id, jk_name, jk_kode, jk_bcin, jk_ecout, jk_ket))
            message = "Data shift berhasil ditambahkan."

        connection.commit()
        return jsonify({'status': 'success', 'message': message})

    except Exception as e:
        print(f"Error saving jam_kerja: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()

@pengaturan_shift_bp.route('/api/pengaturan-shift/hapus', methods=['POST'])
def hapus():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        data = request.json
        jk_id = data.get('jk_id')

        if not jk_id:
            return jsonify({'status': 'error', 'message': 'ID tidak valid'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        # Pastikan tidak ada data absensi yang memakai shift ini
        cursor.execute("SELECT COUNT(*) FROM shift_result_new WHERE jk_id = %s", (jk_id,))
        count = cursor.fetchone()[0]
        
        if count > 0:
            return jsonify({'status': 'error', 'message': f'Shift tidak dapat dihapus karena sedang digunakan oleh {count} data absensi.'}), 400

        cursor.execute("DELETE FROM jam_kerja WHERE jk_id = %s", (jk_id,))
        connection.commit()

        return jsonify({'status': 'success', 'message': 'Data shift berhasil dihapus.'})

    except Exception as e:
        print(f"Error deleting jam_kerja: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()
