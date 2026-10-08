from flask import Blueprint, render_template, request, jsonify, session, redirect, url_for
from utils.db import get_db_connection

aturan_puasa_bp = Blueprint('aturan_puasa', __name__)

@aturan_puasa_bp.route('/aturan-puasa', methods=['GET'])
def index():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)
    
    try:
        cursor.execute("SELECT * FROM aturan_puasa ORDER BY tanggal_mulai DESC")
        data = cursor.fetchall()
    except Exception as e:
        print(f"Error getting aturan puasa: {e}")
        data = []
    finally:
        cursor.close()
        connection.close()

    return render_template('aturan-puasa.html', data=data)

@aturan_puasa_bp.route('/api/aturan-puasa/simpan', methods=['POST'])
def simpan():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

    try:
        data = request.json
        id = data.get('id')
        nama_periode = data.get('nama_periode')
        tanggal_mulai = data.get('tanggal_mulai')
        tanggal_selesai = data.get('tanggal_selesai')
        potongan_menit = data.get('potongan_menit', 30)

        if not nama_periode or not tanggal_mulai or not tanggal_selesai:
            return jsonify({'status': 'error', 'message': 'Nama periode, tanggal mulai, dan tanggal selesai wajib diisi'}), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        if id:
            cursor.execute("""
                UPDATE aturan_puasa 
                SET nama_periode = %s, tanggal_mulai = %s, tanggal_selesai = %s, potongan_menit = %s
                WHERE id = %s
            """, (nama_periode, tanggal_mulai, tanggal_selesai, potongan_menit, id))
            message = "Data aturan puasa berhasil diperbarui."
        else:
            cursor.execute("""
                INSERT INTO aturan_puasa (nama_periode, tanggal_mulai, tanggal_selesai, potongan_menit)
                VALUES (%s, %s, %s, %s)
            """, (nama_periode, tanggal_mulai, tanggal_selesai, potongan_menit))
            message = "Data aturan puasa berhasil ditambahkan."

        connection.commit()
        return jsonify({'status': 'success', 'message': message})

    except Exception as e:
        print(f"Error saving aturan puasa: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()

@aturan_puasa_bp.route('/api/aturan-puasa/hapus', methods=['POST'])
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

        cursor.execute("DELETE FROM aturan_puasa WHERE id = %s", (id,))
        connection.commit()

        return jsonify({'status': 'success', 'message': 'Data aturan puasa berhasil dihapus.'})

    except Exception as e:
        print(f"Error deleting aturan puasa: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500
    finally:
        if 'cursor' in locals(): cursor.close()
        if 'connection' in locals(): connection.close()
