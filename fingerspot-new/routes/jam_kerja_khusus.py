from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from utils.db import get_db_connection

jam_kerja_khusus_bp = Blueprint('jam_kerja_khusus', __name__)

@jam_kerja_khusus_bp.route('/jam-kerja-khusus', methods=['GET'])
def index():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # Get list of all jam_kerja_khusus
        cursor.execute("""
            SELECT jkk.id, jkk.tanggal, jkk.jk_id, jkk.jam_masuk_khusus, jkk.jam_pulang_khusus, jk.jk_name
            FROM jam_kerja_khusus jkk
            LEFT JOIN jam_kerja jk ON jkk.jk_id = jk.jk_id
            ORDER BY jkk.tanggal DESC
        """)
        data = cursor.fetchall()
        
        # Get jam_kerja for dropdown
        cursor.execute("SELECT jk_id, jk_name FROM jam_kerja ORDER BY jk_name")
        jam_kerja_list = cursor.fetchall()

    except Exception as e:
        print(f"Error getting jam kerja khusus: {e}")
        data = []
        jam_kerja_list = []
    finally:
        cursor.close()
        connection.close()

    return render_template('jam-kerja-khusus.html', data=data, jam_kerja_list=jam_kerja_list)


@jam_kerja_khusus_bp.route('/api/jam-kerja-khusus/simpan', methods=['POST'])
def simpan():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        data = request.json
        id = data.get('id')
        tanggal = data.get('tanggal')
        jk_id = data.get('jk_id')
        jam_masuk_khusus = data.get('jam_masuk_khusus') or None
        jam_pulang_khusus = data.get('jam_pulang_khusus') or None

        if not tanggal or not jk_id:
            return jsonify({'status': 'error', 'message': 'Tanggal dan Shift wajib diisi'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        if id:
            # Update existing
            cursor.execute("""
                UPDATE jam_kerja_khusus 
                SET tanggal = %s, jk_id = %s, jam_masuk_khusus = %s, jam_pulang_khusus = %s
                WHERE id = %s
            """, (tanggal, jk_id, jam_masuk_khusus, jam_pulang_khusus, id))
            message = "Data berhasil diperbarui."
        else:
            # Insert new
            # Assuming id is auto_increment, we can also use ON DUPLICATE KEY UPDATE for safety
            # but since we have 'id' we just INSERT
            cursor.execute("""
                INSERT INTO jam_kerja_khusus (tanggal, jk_id, jam_masuk_khusus, jam_pulang_khusus)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE 
                    jam_masuk_khusus = VALUES(jam_masuk_khusus),
                    jam_pulang_khusus = VALUES(jam_pulang_khusus)
            """, (tanggal, jk_id, jam_masuk_khusus, jam_pulang_khusus))
            message = "Data berhasil ditambahkan."

        connection.commit()
        return jsonify({'status': 'success', 'message': message})

    except Exception as e:
        print(f"Error saving jam kerja khusus: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()


@jam_kerja_khusus_bp.route('/api/jam-kerja-khusus/hapus', methods=['POST'])
def hapus():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        data = request.json
        id = data.get('id')

        if not id:
            return jsonify({'status': 'error', 'message': 'ID tidak valid'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("DELETE FROM jam_kerja_khusus WHERE id = %s", (id,))
        connection.commit()

        return jsonify({'status': 'success', 'message': 'Data berhasil dihapus.'})

    except Exception as e:
        print(f"Error deleting jam kerja khusus: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()
