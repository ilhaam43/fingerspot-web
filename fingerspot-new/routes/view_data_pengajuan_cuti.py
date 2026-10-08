# ==============================================================================
# IMPORT LIBRARY
# ==============================================================================
from flask import Blueprint, render_template, jsonify, send_file, request, session, redirect
from utils.db import get_db_connection
from routes.form_pengajuan_cuti import (
    kirim_email_cuti_mendadak,
    create_pdf_fskct
)
from io import BytesIO
from contextlib import suppress
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import (
    Font,
    PatternFill,
    Alignment,
    Border,
    Side
)
from datetime import datetime
from typing import Any, cast

view_data_pengajuan_cuti_bp = Blueprint('view_data_pengajuan_cuti', __name__)

def close_db_resources(
    connection: Any | None = None,
    cursor: Any | None = None,
) -> None:
    if cursor is not None:
        with suppress(Exception):
            cursor.close()

    if connection is not None and getattr(connection, 'is_connected', lambda: False)():
        with suppress(Exception):
            connection.close()
            
@view_data_pengajuan_cuti_bp.route('/clear-filter-refresh-cuti')
def clear_filter_refresh():

    filter_keys = [
        "nik",
        "nama_lengkap",
        "personil_backup",
        "tim",
        "kategori_cuti",
        "tanggal_pengajuan",
        "periode",
        "sisa_awal_cuti",
        "jumlah_hari",
        "sisa_akhir_cuti",
        "project_manager",
        "supervisor",
        "keterangan",
        "status_pengajuan"
    ]

    for key in filter_keys:
        session.pop(key, None)

    return redirect("/view-data-pengajuan-cuti")

@view_data_pengajuan_cuti_bp.route('/view-data-pengajuan-cuti')
def view_data():
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        tanggal_filter = request.args.get("tanggal_pengajuan", "")
        periode = request.args.get("periode", "")

        if periode is not None:
            session["periode"] = periode

        periode = session.get("periode")
        
        query = """
            SELECT 
            pcn.Id, 
            pcn.Nik_Lintasarta, 
            COALESCE(pg.pegawai_nama, '-') AS Nama_Lengkap, 
            COALESCE(pg_backup.pegawai_nama, '-') AS Nama_Backup, 
            CASE WHEN pcn.Tim_Personil IS NOT NULL 
            AND pcn.Tim_Personil <> '' 
            AND b.pembagian3_nama IS NOT NULL THEN CONCAT (
                'TIM ', pcn.Tim_Personil, ' - ', b.pembagian3_nama
            ) WHEN pcn.Tim_Personil IS NOT NULL 
            AND pcn.Tim_Personil <> '' THEN CONCAT ('TIM ', pcn.Tim_Personil) WHEN b.pembagian3_nama IS NOT NULL THEN CONCAT ('TIM ', b.pembagian3_nama) ELSE '-' END AS Nama_Tim, 
            pcn.Alamat_Email, 
            pcn.Kategori_Cuti, 
            pcn.Sisa_Cuti_Awal, 
            pcn.Jumlah_Hari, 
            pcn.Sisa_Cuti_Akhir, 
            CONCAT (
                COALESCE(pcn.Sisa_Cuti_Awal, 0), 
                ' - ', 
                COALESCE(pcn.Jumlah_Hari, 0), 
                ' = ', 
                COALESCE(pcn.Sisa_Cuti_Akhir, 0)
            ) AS Ringkasan_Cuti, 
            COALESCE(pm.pegawai_nama, '-') AS Project_Manager, 
            COALESCE(sp.nama, '-') AS Pimpinan_Supervisor, 
            DATE_FORMAT(
                pcn.Submitted_Date, '%d-%m-%Y %H:%i'
            ) AS Tanggal_Pengajuan, 
            DATE_FORMAT(pcn.Tanggal_Mulai, '%d-%m-%Y') AS Tanggal_Mulai, 
            DATE_FORMAT(pcn.Tanggal_Akhir, '%d-%m-%Y') AS Tanggal_Akhir, 
            pcn.Keterangan, 
            pcn.Status_Pengajuan, 
            pcn.Submitted_Date AS Raw_Submitted_Date, 
            DATE_FORMAT(
                pcn.Submitted_Date, '%d-%m-%Y %H:%i'
            ) AS Submitted_Date 
            FROM 
            pengajuan_cuti_new pcn 
            LEFT JOIN pegawai pg ON pcn.Personil_Id = pg.pegawai_id 
            LEFT JOIN pegawai pg_backup ON pcn.Backup_Id = pg_backup.pegawai_id 
            LEFT JOIN pegawai pm ON pcn.Project_Manager = pm.pegawai_id 
            LEFT JOIN pembagian3 b ON pcn.Tim_Id = b.pembagian3_id 
            LEFT JOIN pengajuan_nama_supervisor_form sp ON pcn.PimpinanOrSupervisor_Id = sp.id 
            WHERE 
            (
                pcn.IsDelete = 0 
                OR pcn.IsDelete IS NULL
            ) 
            AND pcn.Status_Pengajuan IN (
                'Pending', 'Received', 'Approved', 
                'Rejected'
            )

        """

        params = []

        if periode:
            
            tanggal_mulai, tanggal_akhir = [
                x.strip()
                for x in periode.split(" s/d ")
            ]

            query += """
                AND pcn.Tanggal_Mulai <= STR_TO_DATE(%s, '%d-%m-%Y')
                AND pcn.Tanggal_Akhir >= STR_TO_DATE(%s, '%d-%m-%Y')
            """

            params.extend([
                tanggal_akhir,
                tanggal_mulai
            ])

        elif tanggal_filter:

            query += """
                AND DATE(pcn.Submitted_Date) = STR_TO_DATE(%s, '%d-%m-%Y')
            """

            params.append(tanggal_filter)

        else:

            query += """
                AND pcn.Submitted_Date >= CURDATE()
                AND pcn.Submitted_Date < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
            """

        query += """
            ORDER BY pcn.Submitted_Date DESC, pcn.Id DESC
        """


        cursor.execute(query, params)

        data_pengajuan_cuti = [
            cast(dict[str, Any], row) for row in cursor.fetchall()
        ]

        print("=" * 100)
        print("PERIODE =", periode)
        print("TOTAL BACKEND =", len(data_pengajuan_cuti))
        for item in data_pengajuan_cuti:
            print(
                "ID =", item["Id"],
                "| STATUS =", item["Status_Pengajuan"],
                "| SUBMITTED =", item["Submitted_Date"]
            )
        print("=" * 100)

        latest_submit = max(
            (
                item['Raw_Submitted_Date']
                for item in data_pengajuan_cuti
                if item.get('Raw_Submitted_Date') is not None
            ),
            default=None,
        )

        for item in data_pengajuan_cuti:
            item['is_new'] = (
                item.get('Raw_Submitted_Date') is not None
                and item['Raw_Submitted_Date'] == latest_submit
            )

        return render_template(
            'view-data-pengajuan-cuti.html',
            data_pengajuan_cuti=data_pengajuan_cuti,
            tanggal_filter=tanggal_filter,
            periode=periode
        )


    except Exception as e:
        print(f"ERROR VIEW DATA PENGAJUAN CUTI : {e}")

        return render_template(
            'view-data-pengajuan-cuti.html',
            data_pengajuan_cuti=[],
            error=str(e)
        )

    finally:
        close_db_resources(connection, cursor)

# ==============================================================================
# BULK APPROVE
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/bulk-approve-pengajuan-cuti', methods=['POST'])
def bulk_approve_pengajuan_cuti():

    connection = None
    cursor = None

    try:

        data = request.get_json()

        ids = data.get('ids', [])

        print("IDS TERPILIH :", ids)

        if not ids:
            return jsonify({
                "success": False,
                "message": "Tidak ada data yang dipilih."
            }), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        placeholders = ",".join(["%s"] * len(ids))

        query = f"""
            UPDATE pengajuan_cuti_new
            SET Status_Pengajuan = 'Approved'
            WHERE Id IN ({placeholders})
        """

        cursor.execute(query, ids)

        print("ROW-COUNT UPDATE :", cursor.rowcount)

        connection.commit()

        return jsonify({
            "success": True,
            "message": f"{cursor.rowcount} data berhasil di-approve."
        })

    except Exception as e:

        if connection:
            connection.rollback()

        print("=" * 100)
        print("ERROR BULK APPROVE")
        print(e)
        print("=" * 100)

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()

# ==============================================================================
# BULK APPROVE WITH PASSPHRASE
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/bulk-approve-pengajuan-cuti-with-passphrase', methods=['POST'])
def bulk_approve_pengajuan_cuti_with_passphrase():
    connection = None
    cursor = None

    try:

        data = request.get_json()

        ids = data.get('ids', [])

        print("IDS TERPILIH :", ids)

        if not ids:
            return jsonify({
                "success": False,
                "message": "Tidak ada data yang dipilih."
            }), 400

        connection = get_db_connection()
        cursor = connection.cursor()

        placeholders = ",".join(["%s"] * len(ids))

        query = f"""
            UPDATE pengajuan_cuti_new
            SET Status_Pengajuan = 'Approved'
            WHERE Id IN ({placeholders})
        """

        cursor.execute(query, ids)

        print("ROW-COUNT UPDATE :", cursor.rowcount)

        connection.commit()

        return jsonify({
            "success": True,
            "message": f"{cursor.rowcount} data berhasil di-approve."
        })

    except Exception as e:

        if connection:
            connection.rollback()

        print("=" * 100)
        print("ERROR BULK APPROVE")
        print(e)
        print("=" * 100)

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:

        if cursor:
            cursor.close()

        if connection:
            connection.close()


# ==============================================================================
# APPROVE
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/approve-pengajuan-cuti/<int:pengajuan_id>', methods=['POST'])
def approve_pengajuan_cuti(pengajuan_id):

    connection = None
    cursor = None

    try:

        print("=" * 80)
        print("APPROVE CLICK")
        print("ID :", pengajuan_id)
        print("=" * 80)

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        # =====================================
        # CEK DATA
        # =====================================

        cursor.execute("""
            SELECT
                pc.*,
                p.pegawai_nama
            FROM pengajuan_cuti_new pc
            LEFT JOIN pegawai p
                ON p.pegawai_id = pc.Personil_Id
            WHERE pc.Id = %s
        """, (pengajuan_id,))

        data = cast(dict[str, Any] | None, cursor.fetchone())

        print("=" * 80)
        print("DATA APPROVE :")
        print(data)
        print("=" * 80)

        if not data:
            return jsonify({
                "success": False,
                "message": "Data pengajuan tidak ditemukan."
            }), 404

        # =====================================
        # UPDATE STATUS
        # =====================================

        cursor.execute("""
            UPDATE pengajuan_cuti_new
            SET
                Status_Pengajuan = 'Approved',
                Updated_At = NOW()
            WHERE Id = %s
        """, (pengajuan_id,))

        print("ROW-COUNT UPDATE :", cursor.rowcount)

        connection.commit()

        # =====================================
        # VALIDASI HASIL UPDATE
        # =====================================

        cursor.execute("""
            SELECT
                Status_Pengajuan,
                Updated_At
            FROM pengajuan_cuti_new
            WHERE Id = %s
        """, (pengajuan_id,))

        cek_status = cursor.fetchone()

        print("=" * 80)
        print("SETELAH UPDATE :")
        print(cek_status)
        print("=" * 80)

        # =====================================
        # PDF & EMAIL
        # =====================================

        try:

            pdf_path = create_pdf_fskct(pengajuan_id)

            kirim_email_cuti_mendadak(
                recipient_email=data["Alamat_Email"],
                nama_penerima=data["pegawai_nama"],
                nik_lintasarta=data["Nik_Lintasarta"],
                kategori_cuti=data["Kategori_Cuti"],
                pdf_path=pdf_path
            )

            print("PDF DAN EMAIL BERHASIL")

        except Exception as email_error:

            print("=" * 80)
            print("PDF / EMAIL ERROR")
            print(email_error)
            print("=" * 80)

        return jsonify({
            "success": True,
            "message": "Pengajuan berhasil diapprove."
        })

    except Exception as e:

        if connection:
            connection.rollback()

        import traceback
        traceback.print_exc()

        print("=" * 80)
        print("ERROR APPROVE")
        print(type(e))
        print(e)
        print("=" * 80)

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:

        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

# ==============================================================================
# REJECT
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/reject-pengajuan-cuti/<int:pengajuan_id>', methods=['POST'])
def reject_pengajuan(pengajuan_id):

    connection = None
    cursor = None

    try:

        alasan_reject = request.form['alasan_reject'] if 'alasan_reject' in request.form else ''

        connection = get_db_connection()
        cursor = connection.cursor()

        query = """
            UPDATE pengajuan_cuti_new
            SET
                status = 'Rejected',
                alasan_reject = %s,
                updated_at = NOW()
            WHERE id = %s
        """

        cursor.execute(query, (
            alasan_reject,
            pengajuan_id
        ))

        connection.commit()

        return jsonify({
            "success": True,
            "message": "Pengajuan berhasil ditolak."
        }), 200

    except Exception as e:

        print("=" * 100)
        print("An error occurred during the data rejection process.")
        print(e)
        print("=" * 100)

        return jsonify({
            "success": False,
            "message": str(e)
        }), 500

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

# ==============================================================================
# DETAIL
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/detail-pengajuan-cuti/<int:pengajuan_id>', methods=['GET'])
def detail_pengajuan(pengajuan_id):
    
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        
        cursor.execute("""
            SELECT *
            FROM pengajuan_cuti_new
            WHERE Id = %s
            AND (IsDelete = 0 OR IsDelete IS NULL)
        """, (pengajuan_id,))
        
        data = cursor.fetchone()

        if data:
            return render_template('detail_pengajuan.html', data=data)

        return "Data not found", 404
    except Exception as e:   
        return jsonify({ "success": False, "message": str(e) }), 500
    finally:
    
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

# ==============================================================================
# UPDATE
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/update-pengajuan-cuti', methods=['POST'])
def update_pengajuan():
    
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        ...

        return jsonify({ "success": True })
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the data updating process.")
        print(e)
        print("=" * 100)

        return jsonify({ "success": False, "message": str(e) }), 500
    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

# ==============================================================================
# DELETE
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/delete-pengajuan-cuti/<int:pengajuan_id>', methods=['POST'])
def delete_pengajuan(pengajuan_id):
    
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            UPDATE pengajuan_cuti_new
            SET IsDelete = 1
            WHERE Id = %s
            AND (IsDelete = 0 OR IsDelete IS NULL)
            """,
            (pengajuan_id,)
        )

        connection.commit()

        if cursor.rowcount == 0:
            return jsonify({ "success": False, "message": "Data not found" }), 404

        return jsonify({ "success": True })
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the data deletion process.")
        print(e)
        print("=" * 100)

        return jsonify({ "success": False, "message": str(e) }), 500
    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()
                
# ==============================================================================
# EXPORT EXCEL DATA PENGAJUAN CUTI
# ==============================================================================
@view_data_pengajuan_cuti_bp.route('/export-pengajuan-cuti', methods=['GET'])
def export_pengajuan_cuti():

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:

        query = """
        
            SELECT pcn.Nik_Lintasarta AS Nik_Lintasarta
                ,COALESCE(NULLIF(pcn.Alamat_Email, ''), '-') AS alamat_email
                ,COALESCE(NULLIF(pg.pegawai_nama, ''), '-') AS nama_lengkap
                ,COALESCE(NULLIF(pg_backup.pegawai_nama, ''), '-') AS nama_personil_backup
                ,COALESCE(NULLIF(pcn.Tim_Personil, ''), '-') AS tim
                ,COALESCE(NULLIF(b.pembagian3_nama, ''), '-') AS personil
                ,COALESCE(NULLIF(pcn.Kategori_Cuti, ''), '-') AS jenis_pengajuan
                ,DATE_FORMAT(pcn.Submitted_Date, '%d-%m-%Y %H:%i') AS tgl_pengajuan
                ,CONCAT (DATE_FORMAT(pcn.Tanggal_Mulai, '%d-%m-%Y'),' s/d ', DATE_FORMAT(pcn.Tanggal_Akhir, '%d-%m-%Y')) AS periode_pengajuan
                ,IFNULL(pcn.Sisa_Cuti_Awal, 0) AS sisa_cuti_awal
                ,IFNULL(pcn.Jumlah_Hari, 0) AS jumlah_hari
                ,IFNULL(pcn.Sisa_Cuti_Akhir, 0) AS sisa_cuti_akhir
                ,COALESCE(pm.pegawai_nama, '-') AS project_manager
                ,COALESCE(sp.nama, '-') AS pimpinan
                ,COALESCE(pcn.Keterangan, '-') AS keterangan
                ,COALESCE(pcn.Status_Pengajuan, '-') AS STATUS
            FROM pengajuan_cuti_new pcn
            LEFT JOIN pegawai pg ON pcn.Personil_Id = pg.pegawai_id
            LEFT JOIN pegawai pg_backup ON pcn.Backup_Id = pg_backup.pegawai_id
            LEFT JOIN pegawai pm ON pcn.Project_Manager = pm.pegawai_id
            LEFT JOIN pembagian3 b ON pcn.Tim_Id = b.pembagian3_id
            LEFT JOIN pengajuan_nama_supervisor_form sp ON pcn.PimpinanOrSupervisor_Id = sp.id
            WHERE 
            (
                pcn.IsDelete = 0 
                OR 
                pcn.IsDelete IS NULL
            )
            AND pcn.Status_Pengajuan IN 
            (
                'Pending'
                ,'Received'
                ,'Approved'
                ,'Rejected'
            )
            AND pcn.Submitted_Date >= CURDATE()
            AND pcn.Submitted_Date < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
            ORDER BY pcn.Submitted_Date DESC, pcn.Id DESC

        """

        cursor.execute(query)
        data = cursor.fetchall()

        wb = Workbook()
        ws = wb.active or wb.create_sheet()

        ws.title = "Pengajuan Cuti"

        headers = [
            "No.",
            "NIK LINTASARTA",
            "ALAMAT EMAIL",
            "NAMA LENGKAP",
            "NAMA PERSONIL BACKUP",
            "TIM",
            "PERSONIL",
            "JENIS PENGAJUAN",
            "TANGGAL PENGAJUAN",
            "PERIODE PENGAJUAN",
            "SISA CUTI (AWAL)",
            "JUMLAH HARI CUTI",
            "SISA CUTI (AKHIR)",
            "PROJECT MANAGER",
            "PIMPINAN / SUPERVISOR",
            "KETERANGAN",
            "STATUS"
        ]

        # =====================================
        # HEADER
        # =====================================

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

        # =====================================
        # DATA
        # =====================================

        for idx, row in enumerate(data, start=2):

            row_data = cast(dict[str, Any], row)

            cell_no = ws.cell(
                row=idx,
                column=1,
                value=idx - 1
            )

            cell_no.alignment = Alignment(
                horizontal="center",
                vertical="center"
            )

            nik_lintasarta = row_data.get("Nik_Lintasarta")
            if isinstance(nik_lintasarta, (int, str)):
                with suppress(ValueError):
                    nik_lintasarta = int(nik_lintasarta)

            ws.cell(row=idx, column=2, value=nik_lintasarta)
            ws.cell(row=idx, column=3, value=row_data.get("alamat_email"))
            ws.cell(row=idx, column=4, value=row_data.get("nama_lengkap"))
            ws.cell(row=idx, column=5, value=row_data.get("nama_personil_backup"))
            ws.cell(row=idx, column=6, value=row_data.get("tim"))
            ws.cell(row=idx, column=7, value=row_data.get("personil"))
            ws.cell(row=idx, column=8, value=row_data.get("jenis_pengajuan"))
            ws.cell(row=idx, column=9, value=row_data.get("tgl_pengajuan"))
            ws.cell(row=idx, column=10, value=row_data.get("periode_pengajuan"))
            ws.cell(row=idx, column=11, value=row_data.get("sisa_cuti_awal"))
            jumlah_hari = row_data.get("jumlah_hari")
            ws.cell(row=idx, column=12, value=f"{jumlah_hari} Hari" if jumlah_hari is not None else None)
            ws.cell(row=idx, column=13, value=row_data.get("sisa_cuti_akhir"))
            ws.cell(row=idx, column=14, value=row_data.get("project_manager"))
            ws.cell(row=idx, column=15, value=row_data.get("pimpinan"))
            ws.cell(row=idx, column=16, value=row_data.get("keterangan"))
            ws.cell(row=idx, column=17, value=row_data.get("status"))

        # =====================================
        # BORDER
        # =====================================

        thin = Side(
            border_style="thin",
            color="000000"
        )

        border = Border(
            left=thin,
            right=thin,
            top=thin,
            bottom=thin
        )

        for row_cells in ws.iter_rows():
            for cell in row_cells:
                cell.border = border

        # =====================================
        # ALIGNMENT
        # =====================================

        for row_num in range(2, ws.max_row + 1):

            ws.cell(row=row_num, column=1).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=2).alignment = Alignment(horizontal="left", vertical="center")
            ws.cell(row=row_num, column=6).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=7).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=8).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=9).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=10).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=11).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=12).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=13).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=14).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=15).alignment = Alignment(horizontal="center", vertical="center")
            ws.cell(row=row_num, column=17).alignment = Alignment(horizontal="center", vertical="center")

        # FILTER
        ws.auto_filter.ref = f"A1:Q{ws.max_row}"

        # FREEZE
        ws.freeze_panes = "A2"

        # AUTO WIDTH
        for column_index, column_cells in enumerate(ws.iter_cols(), start=1):

            max_length = 0

            for cell in column_cells:

                with suppress(Exception):
                    if cell.value:
                        max_length = max(
                            max_length,
                            len(str(cell.value))
                        )

            ws.column_dimensions[get_column_letter(column_index)].width = max_length + 3

        # =====================================
        # FINAL RETURN (FILE)
        # =====================================
        output = BytesIO()

        wb.save(output)

        output.seek(0)

        return send_file(
            output,
            download_name=f"Laporan Pengajuan Cuti_{datetime.now().strftime('%d%m%Y_%H%M%S')}.xlsx",
            as_attachment=True,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the data export process.")
        print(e)
        print("=" * 100)
        return jsonify({ "success": False, "message": str(e) }), 500
    finally:
        cursor.close()
        if connection and connection.is_connected():
            connection.close()