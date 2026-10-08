from io import BytesIO
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import (
    Font,
    PatternFill,
    Alignment,
    Border,
    Side
)

from flask import Blueprint, redirect, render_template, jsonify, request, send_file, session
from utils.db import get_db_connection
from datetime import datetime
from contextlib import suppress
from typing import Any, cast

view_data_pengajuan_tidak_hadir_bp = Blueprint('view_data_pengajuan_tidak_hadir', __name__)

@view_data_pengajuan_tidak_hadir_bp.route('/clear-filter-refresh-tidak-hadir')
def clear_filter_refresh():

    filter_keys = [
        "filterNik",
        "filterNama",
        "filterPersonil",
        "filterTanggalPengajuan",
        "periode",
        "filterJenis",
        "filterDurasi",
        "filterProjectManager",
        "filterSupervisor",
        "filterKeterangan",
        "filterStatus"
    ]

    for key in filter_keys:
        session.pop(key, None)

    return redirect("/view-data-pengajuan-tidak-hadir")

# =====================================================
# VIEW DATA
# =====================================================
@view_data_pengajuan_tidak_hadir_bp.route('/view-data-pengajuan-tidak-hadir', methods=['GET'])
def view_data():
    
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    data_pengajuan = []
    list_tim_hdti = []
    list_pegawai_hdti = []
    list_tim_hdbs = []
    list_pegawai_hdbs = []
    list_supervisor = []
    list_project_manager = []

    tanggal_filter = request.args.get("tanggal_pengajuan", "")
    periode = request.args.get("periode", "")

    # =====================================================
    # SIMPAN FILTER PERIODE
    # =====================================================
    if periode: 
        session["periode"] = periode

    periode = session.get("periode", "")
    
    try:

        # =====================================================
        # DATA PENGAJUAN
        # =====================================================

        query_pengajuan = """

            SELECT
                pk.Id,
                pk.Nik_Lintasarta,
                COALESCE(pg.pegawai_nama, '-') AS Nama_Lengkap,

                CASE
                    WHEN pk.Tim_Personil IS NOT NULL
                        AND pk.Tim_Personil <> ''
                        AND b.pembagian3_nama IS NOT NULL
                    THEN CONCAT(
                        'TIM ',
                        pk.Tim_Personil,
                        ' - ',
                        b.pembagian3_nama
                    )

                    WHEN pk.Tim_Personil IS NOT NULL
                        AND pk.Tim_Personil <> ''
                    THEN CONCAT(
                        'TIM ',
                        pk.Tim_Personil
                    )

                    WHEN b.pembagian3_nama IS NOT NULL
                    THEN CONCAT(
                        'TIM ',
                        b.pembagian3_nama
                    )

                    ELSE '-'
                END AS Nama_Tim,

                pk.Alamat_Email,
                pk.Status_Absensi,

                COALESCE(
                    pm.pegawai_nama,
                    '-'
                ) AS Project_Manager,

                COALESCE(
                    sp.nama,
                    '-'
                ) AS Pimpinan_Supervisor,

                DATE_FORMAT(
                    pk.Tanggal_Mulai,
                    '%d-%m-%Y'
                ) AS Tanggal_Mulai,

                DATE_FORMAT(
                    pk.Tanggal_Akhir,
                    '%d-%m-%Y'
                ) AS Tanggal_Akhir,

                pk.Jumlah_Hari,
                pk.Keterangan,
                pk.Status_Pengajuan,

                pk.Submitted_Date AS Raw_Submitted_Date,

                DATE_FORMAT(
                    pk.Submitted_Date,
                    '%d-%m-%Y %H:%i'
                ) AS Submitted_Date

            FROM pengajuan_ketidakhadiran pk

            LEFT JOIN pegawai pg
                ON pk.Personil_Id = pg.pegawai_id

            LEFT JOIN pegawai pm
                ON pk.Project_Manager = pm.pegawai_id

            LEFT JOIN pembagian3 b
                ON pk.Tim_Id = b.pembagian3_id

            LEFT JOIN pengajuan_nama_supervisor_form sp
                ON pk.PimpinanOrSupervisor_Id = sp.id

            WHERE
            (
                pk.IsDelete = 0
                OR pk.IsDelete IS NULL
            )
            AND pk.Status_Pengajuan = 'Tidak Hadir'

        """

        params = []

        # =====================================================
        # FILTER PERIODE
        # =====================================================

        if periode:

            tanggal_mulai, tanggal_akhir = [
                x.strip()
                for x in periode.split(" s/d ")
            ]

            query_pengajuan += """
                AND pk.Tanggal_Mulai <= STR_TO_DATE(%s, '%d-%m-%Y')
                AND pk.Tanggal_Akhir >= STR_TO_DATE(%s, '%d-%m-%Y')
            """

            params.extend([
                tanggal_akhir,
                tanggal_mulai
            ])

        # =====================================================
        # FILTER TANGGAL PENGAJUAN
        # =====================================================

        elif tanggal_filter:

            query_pengajuan += """
                AND DATE(pk.Submitted_Date)
                    = STR_TO_DATE(%s, '%d-%m-%Y')
            """

            params.append(tanggal_filter)

        # =====================================================
        # DEFAULT HARI INI
        # =====================================================

        else:

            query_pengajuan += """
                AND pk.Submitted_Date >= CURDATE()
                AND pk.Submitted_Date < DATE_ADD(
                    CURDATE(),
                    INTERVAL 1 DAY
                )
            """

        query_pengajuan += """
            ORDER BY
                pk.Submitted_Date DESC,
                pk.Id DESC
        """

        cursor.execute(
            query_pengajuan,
            params
        )

        data_pengajuan = cast(
            list[dict[str, Any]],
            cursor.fetchall()
        )

        print("=" * 100)
        print("TANGGAL FILTER =", tanggal_filter)
        print("PERIODE =", periode)
        print("TOTAL DATA =", len(data_pengajuan))
        print("=" * 100)

        # =====================================================
        # STATUS NEW
        # =====================================================

        latest_submit = None

        if data_pengajuan:

            if submitted_dates := [
                item["Raw_Submitted_Date"]
                for item in data_pengajuan
                if item["Raw_Submitted_Date"] is not None
            ]:
                latest_submit = max(submitted_dates)

        for item in data_pengajuan:

            submitted_date = item["Raw_Submitted_Date"]

            item["is_new"] = (
                submitted_date is not None
                and submitted_date == latest_submit
            )

        # =====================================================
        # TIM HDTI
        # =====================================================

        cursor.execute("""
            SELECT
                pembagian3_id,
                pembagian3_nama
            FROM pembagian3
            WHERE LOWER(pembagian3_ket)
                LIKE '%hdti%'
            ORDER BY pembagian3_nama
        """)

        list_tim_hdti = cursor.fetchall()

        # =====================================================
        # TIM HDBS
        # =====================================================

        cursor.execute("""
            SELECT
                pembagian3_id,
                pembagian3_nama
            FROM pembagian3
            WHERE LOWER(pembagian3_ket)
                LIKE '%hdbs%'
            ORDER BY pembagian3_nama
        """)

        list_tim_hdbs = cursor.fetchall()

        # =====================================================
        # PEGAWAI
        # =====================================================

        cursor.execute("""
            SELECT
                pegawai_id,
                pegawai_nama
            FROM pegawai
            ORDER BY pegawai_nama ASC
        """)

        list_pegawai_hdti = cursor.fetchall()
        list_pegawai_hdbs = list_pegawai_hdti

        # =====================================================
        # SUPERVISOR
        # =====================================================

        cursor.execute("""
            SELECT
                id,
                nama
            FROM pengajuan_nama_supervisor_form
            ORDER BY nama ASC
        """)

        list_supervisor = cursor.fetchall()

        # =====================================================
        # PROJECT MANAGER
        # =====================================================

        cursor.execute("""
            SELECT
                pegawai_id,
                pegawai_nama
            FROM pegawai
            WHERE pegawai_nik_lintas IN (098209384092834)
                AND pegawai_nama = 'MOHAMAD USMAN ANDI'
                AND pegawai_status = 1
            ORDER BY pegawai_nama
        """)

        list_project_manager = cursor.fetchall()

    except Exception as e:

        print("\n" + "=" * 50)
        print("ERROR MYSQL TERJADI BACA DI SINI:")
        print(e)
        print("=" * 50 + "\n")

    finally:

        cursor.close()
        connection.close()

    return render_template(
        "view-data-pengajuan-tidak-hadir.html",
        data_pengajuan=data_pengajuan,
        list_tim_hdti=list_tim_hdti,
        list_pegawai_hdti=list_pegawai_hdti,
        list_tim_hdbs=list_tim_hdbs,
        list_pegawai_hdbs=list_pegawai_hdbs,
        list_supervisor=list_supervisor,
        list_project_manager=list_project_manager,
        tanggal_filter=tanggal_filter,
        periode=periode
    )

# =====================================================
# DETAIL DATA UNTUK POPUP MODAL
# =====================================================
@view_data_pengajuan_tidak_hadir_bp.route('/detail-pengajuan/<int:record_id>', methods=['GET'])
def detail_pengajuan(record_id):
    
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:

        query = """
            SELECT

                pk.Id AS id,

                pk.Nik_Lintasarta AS nik_lintasarta,

                pk.Tim_Personil AS tim_personil,

                pk.Tim_Id AS tim_id,

                pk.Personil_Id AS personil_id,

                pk.Project_Manager AS project_manager_id,

                pk.PimpinanOrSupervisor_Id AS supervisor_id,

                COALESCE(
                    pg.pegawai_nama,
                    '-'
                ) AS nama_lengkap,

                CASE
                    WHEN pk.Tim_Personil IS NOT NULL
                    AND pk.Tim_Personil <> ''
                    AND b.pembagian3_nama IS NOT NULL
                    THEN CONCAT(
                        'TIM ',
                        pk.Tim_Personil,
                        ' - ',
                        b.pembagian3_nama
                    )

                    WHEN pk.Tim_Personil IS NOT NULL
                    AND pk.Tim_Personil <> ''
                    THEN CONCAT(
                        'TIM ',
                        pk.Tim_Personil
                    )

                    WHEN b.pembagian3_nama IS NOT NULL
                    THEN CONCAT(
                        'TIM ',
                        b.pembagian3_nama
                    )

                    ELSE '-'
                END AS tim,

                COALESCE(
                    pk.Alamat_Email,
                    '-'
                ) AS email,

                COALESCE(
                    pk.Status_Absensi,
                    '-'
                ) AS jenis_pengajuan,

                COALESCE(
                    pm.pegawai_nama,
                    '-'
                ) AS project_manager,

                COALESCE(
                    sp.nama,
                    '-'
                ) AS pimpinan,

                CONCAT(
                    DATE_FORMAT(
                        pk.Submitted_Date,
                        '%d-%m-%Y'
                    ),
                    ' (Jam ',
                    DATE_FORMAT(
                        pk.Submitted_Date,
                        '%H:%i'
                    ),
                    ')'
                ) AS tgl_pengajuan,

                DATE_FORMAT(
                    pk.Tanggal_Mulai,
                    '%d-%m-%Y'
                ) AS tgl_mulai,

                DATE_FORMAT(
                    pk.Tanggal_Akhir,
                    '%d-%m-%Y'
                ) AS tgl_selesai,

                IFNULL(
                    pk.Jumlah_Hari,
                    0
                ) AS durasi,

                COALESCE(
                    pk.Keterangan,
                    '-'
                ) AS keterangan,

                COALESCE(
                    pk.Status_Pengajuan,
                    'Pending'
                ) AS status

            FROM pengajuan_ketidakhadiran pk

            LEFT JOIN pegawai pg
                ON pk.Personil_Id = pg.pegawai_id

            LEFT JOIN pegawai pm
                ON pk.Project_Manager = pm.pegawai_id

            LEFT JOIN pembagian3 b
                ON pk.Tim_Id = b.pembagian3_id

            LEFT JOIN pengajuan_nama_supervisor_form sp
                ON pk.PimpinanOrSupervisor_Id = sp.id

            WHERE pk.Id = %s

            LIMIT 1
        """

        cursor.execute(query, (record_id,))
        if data := cursor.fetchone():
            return jsonify({
                "success": True,
                "data": data
            })

        return jsonify({
            "success": False,
            "message": "Data tidak ditemukan"
        }), 404

    except Exception as e:

        print("\n" + "=" * 50)
        print("ERROR DETAIL PENGAJUAN:")
        print(e)
        print("=" * 50 + "\n")

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:

        cursor.close()
        connection.close()

@view_data_pengajuan_tidak_hadir_bp.route('/update-pengajuan-tidak-hadir', methods=['POST'])
def update_pengajuan():
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # Read optional form values consistently and preserve the existing
        # application status when the edit form does not submit one.
        form = request.form
        record_id = form.get('id')
        if not record_id:
            return jsonify({'success': False, 'message': 'ID pengajuan wajib diisi.'}), 400

        status_pengajuan = form.get('status_pengajuan')
        if not status_pengajuan:
            cursor.execute("""
                SELECT Status_Pengajuan
                FROM pengajuan_ketidakhadiran
                WHERE Id = %s
            """, (record_id,))
            existing = cursor.fetchone()
            if existing is None:
                return jsonify({'success': False, 'message': 'Data tidak ditemukan'}), 404
            status_pengajuan = cast(dict[str, Any], existing)['Status_Pengajuan']

        status_absensi = form.get('status_absensi')
        project_manager = form.get('project_manager')
        tanggal_mulai = form.get('tanggal_mulai')
        tanggal_akhir = form.get('tanggal_akhir')
        pimpinan_supervisor_nama = form.get('pimpinan_supervisor')
        keterangan = form.get('keterangan')

        # Penanganan dinamik personil dan tim (HDTI / HDBS)
        pegawai_id = form.get('pegawai_id_hdti') or form.get('pegawai_id_hdbs')

        # Cari Tim_Id berdasarkan nama tim yang dipilih
        tim_id = form.get('tim_hdti') or form.get('tim_hdbs')

        # Cari Supervisor ID
        spv_id = None

        if pimpinan_supervisor_nama:
            cursor.execute(
                """
                SELECT id
                FROM pengajuan_nama_supervisor_form
                WHERE nama = %s
                LIMIT 1
                """,
                (pimpinan_supervisor_nama,)
            )

            res_spv = cursor.fetchone()
            if res_spv is not None:
                spv_id = cast(dict[str, Any], res_spv).get('id')

        jumlah_hari = 0
        if tanggal_mulai and tanggal_akhir:
            def parse_date(value):
                for date_format in ("%Y-%m-%d", "%d-%m-%Y"):
                    with suppress(ValueError):
                        return datetime.strptime(value, date_format)
                raise ValueError(f"Format tanggal tidak valid: {value}")

            d_mulai = parse_date(tanggal_mulai)
            d_akhir = parse_date(tanggal_akhir)
            tanggal_mulai = d_mulai.strftime("%Y-%m-%d")
            tanggal_akhir = d_akhir.strftime("%Y-%m-%d")
            jumlah_hari = (d_akhir - d_mulai).days + 1

        # Query update data ke database
        update_query = """
            UPDATE pengajuan_ketidakhadiran 
            SET 
                Status_Pengajuan = %s,
                Status_Absensi = %s,
                Project_Manager = %s,
                Keterangan = %s,
                Tanggal_Mulai = %s,
                Tanggal_Akhir = %s,
                Jumlah_Hari = %s,
                Personil_Id = COALESCE(%s, Personil_Id),
                Tim_Id = COALESCE(%s, Tim_Id),
                PimpinanOrSupervisor_Id = COALESCE(%s, PimpinanOrSupervisor_Id)
            WHERE Id = %s
        """
        cursor.execute(update_query, (
            str(status_pengajuan) if status_pengajuan is not None else None,
            status_absensi,
            project_manager,
            keterangan,
            tanggal_mulai,
            tanggal_akhir,
            jumlah_hari,
            pegawai_id or None,
            tim_id,
            str(spv_id) if spv_id is not None else None,
            str(record_id) if record_id is not None else None
        ))
        
        connection.commit()
        return jsonify({'success': True, 'message': 'Data pengajuan berhasil diperbarui.'})

    except Exception as e:
        connection.rollback()
        print("\n" + "="*50)
        print("ERROR UPDATE MYSQL:")
        print(e)
        print("="*50 + "\n")
        return jsonify({'success': False, 'message': str(e)}), 500

    finally:
        cursor.close()
        connection.close()
                
@view_data_pengajuan_tidak_hadir_bp.route('/delete-pengajuan-tidak-hadir/<int:pengajuan_id>', methods=['POST'])
def delete_pengajuan(pengajuan_id):

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:

        query = """
            UPDATE pengajuan_ketidakhadiran
            SET
                IsDelete = 1,
                Deleted_At = NOW()
            WHERE Id = %s
        """

        cursor.execute(query, (pengajuan_id,))
        connection.commit()

        return jsonify({
            'success': True,
            'message': 'Data berhasil dihapus.'
        })

    except Exception as e:
        connection.rollback()

        print("\n" + "=" * 50)
        print("ERROR DELETE PENGAJUAN")
        print(e)
        print("=" * 50 + "\n")

        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

    finally:
        cursor.close()
        connection.close()
                
@view_data_pengajuan_tidak_hadir_bp.route('/approve-pengajuan/<int:id>', methods=['POST'])
def approve_pengajuan(pengajuan_id):
    
    connection = get_db_connection()
    cursor = connection.cursor()

    try:

        cursor.execute("""
            UPDATE pengajuan_ketidakhadiran
            SET Status_Pengajuan = 'Approved'
            WHERE Id = %s
        """, (pengajuan_id,))

        connection.commit()

        return jsonify({
            'success': True
        })

    except Exception as e:
        connection.rollback()
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500

    finally:
        cursor.close()
        connection.close()

@view_data_pengajuan_tidak_hadir_bp.route('/reject-pengajuan/<int:id>', methods=['POST'])
def reject_pengajuan(pengajuan_id):

    connection = get_db_connection()
    cursor = connection.cursor()

    try:

        cursor.execute("""
            UPDATE pengajuan_ketidakhadiran
            SET Status_Pengajuan = 'Rejected'
            WHERE Id = %s
        """, (pengajuan_id,))

        connection.commit()

        return jsonify({
            'success': True
        })

    except Exception as e:

        connection.rollback()

        return jsonify({
            'success': False,
            'message': str(e)
        })

    finally:

        cursor.close()
        connection.close()        
                
# ==============================================================================
# EXPORT EXCEL DATA PENGAJUAN TIDAK HADIR
# ==============================================================================
@view_data_pengajuan_tidak_hadir_bp.route('/export-pengajuan-tidak-hadir', methods=['GET'])
def export_pengajuan_tidak_hadir():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        
        query = """
    SELECT
        pk.Nik_Lintasarta AS Nik_Lintasarta,

        COALESCE(
            NULLIF(pk.Alamat_Email, ''),
            '-'
        ) AS alamat_email,

        COALESCE(
            NULLIF(pg.pegawai_nama, ''),
            '-'
        ) AS nama_lengkap,

        COALESCE(
            NULLIF(pk.Tim_Personil, ''),
            '-'
        ) AS tim,

        COALESCE(
            NULLIF(b.pembagian3_nama, ''),
            '-'
        ) AS personil,

        DATE_FORMAT(
            pk.Submitted_Date,
            '%d-%m-%Y %H:%i'
        ) AS tgl_pengajuan,

        CASE
            WHEN pk.Tanggal_Mulai IS NOT NULL
            AND pk.Tanggal_Akhir IS NOT NULL
            THEN CONCAT(
                DATE_FORMAT(
                    pk.Tanggal_Mulai,
                    '%d-%m-%Y'
                ),
                ' s/d ',
                DATE_FORMAT(
                    pk.Tanggal_Akhir,
                    '%d-%m-%Y'
                )
            )
            ELSE '-'
        END AS periode_tanggal,

        COALESCE(
            NULLIF(pk.Status_Absensi, ''),
            '-'
        ) AS jenis_pengajuan,

        IFNULL(
            pk.Jumlah_Hari,
            0
        ) AS durasi,

        COALESCE(
            NULLIF(pm.pegawai_nama, ''),
            '-'
        ) AS project_manager,

        COALESCE(
            NULLIF(sp.nama, ''),
            '-'
        ) AS pimpinan,

        COALESCE(
            NULLIF(pk.Keterangan, ''),
            '-'
        ) AS keterangan,

        COALESCE(
            NULLIF(pk.Status_Pengajuan, ''),
            '-'
        ) AS status

    FROM pengajuan_ketidakhadiran pk

    LEFT JOIN pegawai pg
        ON pk.Personil_Id = pg.pegawai_id

    LEFT JOIN pegawai pm
        ON pk.Project_Manager = pm.pegawai_id

    LEFT JOIN pembagian3 b
        ON pk.Tim_Id = b.pembagian3_id

    LEFT JOIN pengajuan_nama_supervisor_form sp
        ON pk.PimpinanOrSupervisor_Id = sp.id

    WHERE
        COALESCE(pk.IsDelete, 0) = 0
        AND pk.Status_Pengajuan = 'Tidak Hadir'
        AND pk.Submitted_Date >= CURDATE()
        AND pk.Submitted_Date < DATE_ADD(
            CURDATE(),
            INTERVAL 1 DAY
        )

    ORDER BY
        pk.Submitted_Date DESC,
        pk.Id DESC
"""

        cursor.execute(query)
        data = cursor.fetchall()

        wb = Workbook()
        ws = wb.active

        if ws is None:
            ws = wb.create_sheet()

        ws.title = "Pengajuan Tidak Hadir"

        headers = [
            "No.",
            "NIK LINTASARTA",
            "ALAMAT EMAIL",
            "NAMA LENGKAP",
            "TIM",
            "PERSONIL",
            "TANGGAL PENGAJUAN",
            "PERIODE PENGAJUAN",
            "JENIS PENGAJUAN",
            "JUMLAH HARI (DURASI)",
            "PROJECT MANAGER",
            "PIMPINAN / SUPERVISOR",
            "KETERANGAN",
            "STATUS"
        ]

        # HEADER
        for col_num, header in enumerate(headers, 1):

            cell = ws.cell(
                row=1,
                column=col_num,
                value=header
            )

            cell.font = Font(
                bold=True,
                color="000000"
            )

            if col_num == 1:
                fill = PatternFill(
                    "solid",
                    fgColor="D9D9D9"
                )

            elif header == "STATUS":
                fill = PatternFill(
                    "solid",
                    fgColor="FFF200"
                )

            else:
                fill = PatternFill(
                    "solid",
                    fgColor="00B0F0"
                )

            cell.fill = fill

            cell.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )

        # DATA
        for idx, raw_row in enumerate(data, start=2):
            # ``dictionary=True`` returns mapping rows at runtime, while the
            # connector type stub exposes them as sequence rows.
            row = cast(dict[str, Any], raw_row)
            
            cell_no = ws.cell(
                row=idx,
                column=1,
                value=idx - 1
            )

            cell_no.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )

            try:
                nik_lintasarta = int(row["Nik_Lintasarta"])
            except Exception:
                nik_lintasarta = row["Nik_Lintasarta"]
                
            ws.cell(row=idx, column=2, value=nik_lintasarta)
            ws.cell(row=idx, column=3, value=row["alamat_email"])
            ws.cell(row=idx, column=4, value=row["nama_lengkap"])
            ws.cell(row=idx, column=5, value=row["tim"])
            ws.cell(row=idx, column=6, value=row["personil"])
            ws.cell(row=idx, column=7, value=row["tgl_pengajuan"])
            ws.cell(row=idx, column=8, value=row["periode_tanggal"])
            ws.cell(row=idx, column=9, value=row["jenis_pengajuan"])
            ws.cell(row=idx, column=10, value=f"{row['durasi']} Hari")
            ws.cell(row=idx, column=11, value=row["project_manager"])
            ws.cell(row=idx, column=12, value=row["pimpinan"])
            ws.cell(row=idx, column=13, value=row["keterangan"])
            ws.cell(row=idx, column=14, value=row["status"])

        # BORDER
        thin = Side(border_style="thin", color="000000")

        border = Border(
            left=thin,
            right=thin,
            top=thin,
            bottom=thin
        )

        for row in ws.iter_rows():
            for cell in row:
                cell.border = border

        for row_num in range(2, ws.max_row + 1):
            
            # No.
            ws.cell(row=row_num,column=1).alignment = Alignment(horizontal="center",vertical="center")

            # NIK Lintasarta
            ws.cell(row=row_num,column=2).alignment = Alignment(horizontal="left",vertical="center")

            # TIM
            ws.cell(row=row_num,column=5).alignment = Alignment(horizontal="center",vertical="center")

            # TANGGAL PENGAJUAN
            ws.cell(row=row_num,column=7).alignment = Alignment(horizontal="center",vertical="center")

            # PERIODE PENGAJUAN
            ws.cell(row=row_num,column=8).alignment = Alignment(horizontal="center",vertical="center")

            # JENIS PENGAJUAN
            ws.cell(row=row_num,column=9).alignment = Alignment(horizontal="center",vertical="center")

            # DURASI
            ws.cell(row=row_num,column=10).alignment = Alignment(horizontal="center",vertical="center")

            # PROJECT MANAGER
            ws.cell(row=row_num,column=11).alignment = Alignment(horizontal="center",vertical="center")

            # STATUS
            ws.cell(row=row_num,column=14).alignment = Alignment(horizontal="center",vertical="center")

        # FILTER HEADER
        ws.auto_filter.ref = (f"A1:N{ws.max_row}")

        # FREEZE
        ws.freeze_panes = "A2"

        # AUTO WIDTH
        for column_cells in ws.columns:

            max_length = 0

            for cell in column_cells:

                with suppress(Exception):
                    if cell.value:

                        max_length = max(
                            max_length,
                            len(str(cell.value))
                        )

            col_idx = column_cells[0].column
            if col_idx is None:
                continue

            ws.column_dimensions[
                get_column_letter(col_idx)
            ].width = max_length + 3

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        return send_file(
            output,
            download_name = f"Laporan Pengajuan Tidak Hadir_{datetime.now().strftime('%d%m%Y_%H%M%S')}.xlsx",
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    except Exception as e:
        print("=" * 60)
        print("An error occurred during the Excel file export process.")
        print(e)
        print("=" * 100)
        return jsonify({"success": False, "message": str(e)}), 500
    finally:
        cursor.close()
        connection.close()