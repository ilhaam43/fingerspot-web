from flask import Blueprint, render_template, request, session, redirect, url_for, jsonify, send_file
import os
import re
import sys
import subprocess
import uuid
import base64
from datetime import datetime

# Path ke helper script konversi Word → PDF
_HELPER_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'files', 'word_to_pdf.py'
)

from utils.db import get_db_connection

admin_tukar_shift_bp = Blueprint('admin_tukar_shift', __name__)

@admin_tukar_shift_bp.route('/admin/tukar-shift')
def admin_tukar_shift_page():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return redirect(url_for('auth.auth_login'))

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM pengajuan_tukar_shift ORDER BY tanggal_pengajuan DESC")
        pengajuan = cursor.fetchall()
        return render_template('admin-tukar-shift.html', pengajuan=pengajuan)
    except Exception as e:
        return render_template('admin-tukar-shift.html', error=str(e), pengajuan=[])
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@admin_tukar_shift_bp.route('/admin/api/employees', methods=['GET'])
def get_employees():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        # Fetching basic employee list for dropdowns
        cursor.execute("SELECT pegawai_id, pegawai_nip, pegawai_nama, pembagian1_id FROM pegawai ORDER BY pegawai_nama")
        employees = cursor.fetchall()

        # Fetching master data for dropdowns
        cursor.execute("SELECT tim_nama FROM tim ORDER BY tim_nama")
        tim_list = [row['tim_nama'] for row in cursor.fetchall()]

        cursor.execute("SELECT nama FROM pengajuan_nama_leader ORDER BY nama")
        leader_list = [row['nama'] for row in cursor.fetchall()]

        cursor.execute("SELECT nama FROM pengajuan_nama_supervisor ORDER BY nama")
        supervisor_list = [row['nama'] for row in cursor.fetchall()]

        return jsonify({
            'success': True, 
            'employees': employees,
            'tim': tim_list,
            'leader': leader_list,
            'supervisor': supervisor_list
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@admin_tukar_shift_bp.route('/admin/tukar-shift/add', methods=['POST'])
def add_tukar_shift():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data = request.get_json() or {}
    pegawai_id = data.get('pegawai_id')
    pegawai_tukar_id = data.get('pegawai_tukar_id')
    start_date_str = data.get('tanggal_mulai')
    end_date_str = data.get('tanggal_akhir')
    tim = data.get('tim', '')
    leader = data.get('leader', '')
    supervisor = data.get('supervisor', '')
    alasan = data.get('alasan', '')

    if not all([pegawai_id, pegawai_tukar_id, start_date_str, end_date_str]):
        return jsonify({'success': False, 'error': 'Data tidak lengkap.'}), 400

    if str(pegawai_id) == str(pegawai_tukar_id):
        return jsonify({'success': False, 'error': 'Pegawai dan rekan tidak boleh sama.'}), 400

    from datetime import datetime, timedelta

    try:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        if start_date > end_date:
            return jsonify({'success': False, 'error': 'Tanggal mulai tidak boleh lebih dari tanggal selesai'}), 400
    except ValueError:
        return jsonify({'success': False, 'error': 'Format tanggal tidak valid'}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SELECT pegawai_id, pegawai_nip, pegawai_nik_lintas, pegawai_nama FROM pegawai WHERE pegawai_id = %s", (pegawai_id,))
        user = cursor.fetchone()
        
        cursor.execute("SELECT pegawai_id, pegawai_nip, pegawai_nama FROM pegawai WHERE pegawai_id = %s", (pegawai_tukar_id,))
        target = cursor.fetchone()

        if not user or not target:
            return jsonify({'success': False, 'error': 'Pegawai tidak ditemukan.'}), 404

        # Fetch shifts
        cursor.execute("SELECT s.tgl_shift, s.jk_id, j.jk_name FROM shift_result_new s LEFT JOIN jam_kerja j ON s.jk_id = j.jk_id WHERE s.pegawai_id = %s AND s.tgl_shift BETWEEN %s AND %s", (pegawai_id, start_date, end_date))
        curr_shifts_full = cursor.fetchall()
        curr_shifts = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_id'] for row in curr_shifts_full}
        curr_shift_names = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_name'] for row in curr_shifts_full}

        cursor.execute("SELECT s.tgl_shift, s.jk_id, j.jk_name FROM shift_result_new s LEFT JOIN jam_kerja j ON s.jk_id = j.jk_id WHERE s.pegawai_id = %s AND s.tgl_shift BETWEEN %s AND %s", (pegawai_tukar_id, start_date, end_date))
        tgt_shifts_full = cursor.fetchall()
        tgt_shifts = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_id'] for row in tgt_shifts_full}
        tgt_shift_names = {row['tgl_shift'].strftime('%Y-%m-%d') if hasattr(row['tgl_shift'], 'strftime') else str(row['tgl_shift']): row['jk_name'] for row in tgt_shifts_full}

        NON_SWAPPABLE = ['cuti', 'dinas', 'libur', 'sakit', 'izin']
        def is_non_swap(name):
            return any(kw in (name or '').lower() for kw in NON_SWAPPABLE)

        rekan_base_jk = None
        for row in tgt_shifts_full:
            if not is_non_swap(row['jk_name'] or ''):
                rekan_base_jk = row['jk_id']
                break

        pegawai_base_jk = None
        for row in curr_shifts_full:
            if not is_non_swap(row['jk_name'] or ''):
                pegawai_base_jk = row['jk_id']
                break

        # Apply Smart Swaps
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
                if pegawai_base_jk:
                    cursor.execute("UPDATE shift_result_new SET jk_id = %s WHERE pegawai_id = %s AND tgl_shift = %s", (pegawai_base_jk, pegawai_tukar_id, dt_str))
            elif tgt_anom:
                if rekan_base_jk:
                    cursor.execute("UPDATE shift_result_new SET jk_id = %s WHERE pegawai_id = %s AND tgl_shift = %s", (rekan_base_jk, pegawai_id, dt_str))
            else:
                cursor.execute("UPDATE shift_result_new SET jk_id = %s WHERE pegawai_id = %s AND tgl_shift = %s", (tgt_jk, pegawai_id, dt_str))
                cursor.execute("UPDATE shift_result_new SET jk_id = %s WHERE pegawai_id = %s AND tgl_shift = %s", (curr_jk, pegawai_tukar_id, dt_str))

            curr_dt += timedelta(days=1)

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
        return jsonify({'success': True, 'message': 'Data berhasil ditambahkan dan jadwal ditukar!'})

    except Exception as e:
        if 'connection' in locals() and connection.is_connected():
            connection.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@admin_tukar_shift_bp.route('/admin/tukar-shift/update', methods=['POST'])
def update_tukar_shift():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data = request.get_json() or {}
    id_tukar_shift = data.get('id_tukar_shift')
    tim = data.get('tim', '')
    leader = data.get('leader', '')
    supervisor = data.get('supervisor', '')
    alasan = data.get('alasan', '')

    if not id_tukar_shift:
        return jsonify({'success': False, 'error': 'ID tidak valid.'}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        
        # We only allow updating metadata like tim, leader, supervisor, alasan from Admin 
        # to avoid complex reverse-swaps. If they need to change dates, they should delete and re-create.
        cursor.execute("""
            UPDATE pengajuan_tukar_shift 
            SET tim=%s, nama_leader=%s, nama_supervisor=%s, alasan_tukar_shift=%s 
            WHERE id_tukar_shift=%s
        """, (tim, leader, supervisor, alasan, id_tukar_shift))
        
        connection.commit()
        return jsonify({'success': True, 'message': 'Data berhasil diupdate!'})
    except Exception as e:
        if 'connection' in locals() and connection.is_connected():
            connection.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()


@admin_tukar_shift_bp.route('/admin/tukar-shift/delete', methods=['POST'])
def delete_tukar_shift():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    data = request.get_json() or {}
    id_tukar_shift = data.get('id_tukar_shift')

    if not id_tukar_shift:
        return jsonify({'success': False, 'error': 'ID tidak valid.'}), 400

    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        # Deleting only removes the log/history. It does NOT revert the schedule, 
        # because the swap could have happened a long time ago and reverting would overwrite other valid edits.
        cursor.execute("DELETE FROM pengajuan_tukar_shift WHERE id_tukar_shift=%s", (id_tukar_shift,))
        connection.commit()
        return jsonify({'success': True, 'message': 'Data berhasil dihapus!'})
    except Exception as e:
        if 'connection' in locals() and connection.is_connected():
            connection.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()

def _replace_placeholder_in_paragraph(paragraph, placeholders: dict):
    """Ganti semua placeholder {%key%} dalam satu paragraph, sambil mempertahankan
    format dari run pertama yang mengandung placeholder."""
    # Gabung seluruh text di paragraph, ganti, lalu tulis kembali ke run pertama
    full_text = ''.join(run.text for run in paragraph.runs)
    replaced = full_text
    for key, value in placeholders.items():
        # Cocokkan variasi: {%key%}, {% key %}, {%key %}, {% key%}
        pattern = r'\{%\s*' + re.escape(key) + r'\s*%\}'
        replaced = re.sub(pattern, str(value), replaced)
    
    if replaced != full_text:
        # Simpan format dari run pertama yang ada teks, lalu hapus sisanya
        first_run = None
        for run in paragraph.runs:
            if run.text:
                if first_run is None:
                    first_run = run
                    first_run.text = replaced
                else:
                    run.text = ''
            else:
                run.text = ''
        if first_run is None and paragraph.runs:
            paragraph.runs[0].text = replaced


def _fill_docx_template(template_path: str, output_path: str, placeholders: dict):
    """Buka template DOCX, isi semua placeholder, simpan ke output_path."""
    from docx import Document
    doc = Document(template_path)

    # Ganti di paragraphs body
    for paragraph in doc.paragraphs:
        _replace_placeholder_in_paragraph(paragraph, placeholders)

    # Ganti di semua cell tabel
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    _replace_placeholder_in_paragraph(paragraph, placeholders)

    # Ganti di header & footer
    for section in doc.sections:
        for paragraph in section.header.paragraphs:
            _replace_placeholder_in_paragraph(paragraph, placeholders)
        for paragraph in section.footer.paragraphs:
            _replace_placeholder_in_paragraph(paragraph, placeholders)

    doc.save(output_path)


def _convert_docx_to_pdf_via_word(docx_path: str, pdf_path: str):
    """Konversi DOCX ke PDF via subprocess Python terpisah yang menjalankan Word COM.
    Pendekatan ini bebas dari masalah threading Flask karena Word berjalan
    di proses sendiri dengan main thread-nya sendiri.
    """
    result = subprocess.run(
        [sys.executable, _HELPER_SCRIPT, docx_path, pdf_path],
        capture_output=True,
        text=True,
        timeout=120,          # maks 2 menit
    )

    if result.returncode != 0:
        err = result.stderr.strip() or 'Konversi gagal (exit non-zero)'
        raise RuntimeError(f'Word PDF error: {err}')

    # Helper mencetak "OK:<size>" ke stdout jika berhasil
    out = result.stdout.strip()
    if not out.startswith('OK:'):
        raise RuntimeError(f'Word PDF output tak terduga: {out or result.stderr}')

    pdf_size = int(out.split(':', 1)[1])
    if pdf_size == 0:
        raise RuntimeError('Word menghasilkan PDF 0 byte — cek template DOCX.')


@admin_tukar_shift_bp.route('/admin/tukar-shift/download/<int:id_tukar>', methods=['GET'])
def download_tukar_shift_pdf(id_tukar):
    if 'loggedin' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401

    tmp_docx = None
    tmp_pdf = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM pengajuan_tukar_shift WHERE id_tukar_shift=%s", (id_tukar,))
        row = cursor.fetchone()

        if not row:
            return jsonify({'success': False, 'error': 'Data tidak ditemukan.'}), 404

        # --- Format tanggal agar tampil lebih rapi (DD/MM/YYYY) ---
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

        # Hitung jumlah hari jika belum ada di DB
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

        # Tentukan path template
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        template_path = os.path.join(base_dir, 'files', 'template_tukar_shift.docx')

        # Buat folder temp di dalam project (files/temp/)
        tmp_dir = os.path.join(base_dir, 'files', 'temp')
        os.makedirs(tmp_dir, exist_ok=True)

        # Nama file untuk hasil download
        safe_name = re.sub(r'[^\w\s-]', '', row.get('nama_pegawai', 'Pegawai')).strip().replace(' ', '_')
        
        d_mulai = row.get('tanggal_mulai')
        if hasattr(d_mulai, 'strftime'):
            str_tgl_mulai = d_mulai.strftime('%d%m%Y')
        else:
            try:
                str_tgl_mulai = datetime.strptime(str(d_mulai), '%Y-%m-%d').strftime('%d%m%Y')
            except Exception:
                str_tgl_mulai = str(d_mulai).replace('-', '')
                
        download_name = f"Tukar_Shift_{safe_name}_{str_tgl_mulai}.pdf"

        # Buat nama unik untuk DOCX sementara (agar tidak bentrok jika request bersamaan)
        uid = uuid.uuid4().hex
        tmp_docx = os.path.join(tmp_dir, f'ts_{uid}.docx')
        # PDF disimpan secara permanen dengan nama aslinya di folder temp
        tmp_pdf = os.path.join(tmp_dir, download_name)

        # Isi template dan simpan DOCX
        _fill_docx_template(template_path, tmp_docx, placeholders)

        # Konversi ke PDF via Word COM
        _convert_docx_to_pdf_via_word(tmp_docx, tmp_pdf)

        # Hapus DOCX karena hanya file perantara
        if os.path.exists(tmp_docx):
            try:
                os.remove(tmp_docx)
            except Exception:
                pass

        if not os.path.exists(tmp_pdf):
            return jsonify({'success': False, 'error': 'Gagal membuat PDF.'}), 500

        # Jika frontend merequest JSON, kita kirimkan data base64
        # Ini mencegah Download Manager (IDM) mencuri file saat fetch
        if request.args.get('format') == 'json':
            with open(tmp_pdf, 'rb') as f:
                pdf_b64 = base64.b64encode(f.read()).decode('utf-8')
            return jsonify({
                'success': True,
                'filename': download_name,
                'pdf_base64': pdf_b64
            })

        # Endpoint default jika dibuka langsung di browser
        return send_file(
            tmp_pdf,
            as_attachment=True,
            download_name=download_name,
            mimetype='application/pdf'
        )

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        if 'connection' in locals() and connection.is_connected():
            cursor.close()
            connection.close()
