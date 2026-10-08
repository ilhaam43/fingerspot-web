from datetime import datetime, timedelta
from typing import Any, cast
from flask import Blueprint, render_template
from utils.db import get_db_connection

view_kalender_ketersediaan_cuti_bp = Blueprint('view_kalender_ketersediaan_cuti', __name__)

def format_tanggal(dt):

    if not dt: return ""
    bulan = [
        "",
        "Januari",
        "Februari",
        "Maret",
        "April",
        "Mei",
        "Juni",
        "Juli",
        "Agustus",
        "September",
        "Oktober",
        "November",
        "Desember"
    ]
    return f"{dt.day:02d} {bulan[dt.month]} {dt.year}"


def clean_value(value):

    if value is None:
        return ""

    return (
        value.decode("utf-8")
        if isinstance(value, bytes)
        else value
    )

def get_status_icon(status):
    
    status = clean_value(status)

    icons = {
        "Pending": "⏳",
        "Received": "📥",
        "Approved": "✅",
        "Rejected": "❌"
    }

    return icons.get(status, "📄")

# ==============================================================================
# VIEW DATA
# ==============================================================================
@view_kalender_ketersediaan_cuti_bp.route('/view-kalender-ketersediaan-cuti')
def view_data():

    conn = None
    cursor = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        query = """
            SELECT 
            pc.Id,
            pc.Nik_Lintasarta AS nik_lintasarta, 
            p.pegawai_nama AS nama_personil, 
            pc.Created_At AS created_at, 
            pc.Tanggal_Mulai AS tanggal_mulai, 
            pc.Tanggal_Akhir AS tanggal_akhir, 
            pc.Jumlah_Hari AS jumlah_hari, 
            pc.Kategori_Cuti AS jenis_pengajuan, 
            pc.Status_Pengajuan AS status, 
            pc.Keterangan AS keterangan, 
            '#2563eb' AS color, 
            pc.Tanggal_Mulai AS start, 
            DATE_ADD(pc.Tanggal_Akhir, INTERVAL 1 DAY) AS end 
            FROM 
            pengajuan_cuti_new pc 
            INNER JOIN pegawai p ON p.pegawai_id = pc.Personil_Id 
            WHERE 
            pc.IsActive = 1 
            AND pc.IsDelete = 0 
            AND pc.Status_Pengajuan IN (
                'Pending', 'Received', 'Approved'
            ) 
            -- ORDER BY 
            -- pc.Created_At ASC, 
            -- p.pegawai_nama ASC
            ORDER BY
                DATE(pc.Tanggal_Mulai) ASC,
                pc.Submitted_Date ASC,
                pc.Id ASC
        """

        cursor.execute(query)

        rows = cursor.fetchall()

        events = []

        today = datetime.now()

        for row in rows:

            row = cast(dict[str, Any], row)

            start_date = row.get("start")
            end_date = row.get("end")

            if not start_date or not end_date:
                continue

            created_at = row.get("created_at")
            tanggal_mulai = row.get("tanggal_mulai")
            tanggal_akhir = row.get("tanggal_akhir")

            jumlah_hari = clean_value(
                row.get("jumlah_hari")
            )

            actual_end = end_date - timedelta(days=1)

            current_date = start_date

            while current_date <= actual_end:

                
                is_old_month = (
                    current_date.year < today.year
                    or (
                        current_date.year == today.year
                        and current_date.month < today.month
                    )
                )
                
                events.append({

                    "title": (
                        f"{get_status_icon(row.get('status'))} "
                        f"{clean_value(row.get('nama_personil'))}"
                    ),

                    "start": current_date.strftime(
                        "%Y-%m-%d"
                    ),
                    
                    "allDay": True,

                    "color": clean_value(
                        row.get("color")
                    ),

                    "extendedProps": 
                    {


                        "is_old_month": is_old_month,
                        
                        "created_at": (
                            created_at.strftime("%Y-%m-%d %H:%M:%S")
                            if created_at
                            else ""
                        ),
                        
                        "nik_lintasarta": clean_value(
                            row.get("nik_lintasarta")
                        ),

                        "nama_personil": clean_value(
                            row.get("nama_personil")
                        ),

                        "tanggal_pengajuan": (
                            f"{format_tanggal(created_at)} "
                            f"(Jam {created_at.strftime('%H:%M')})"
                            if created_at
                            else ""
                        ),

                        "tanggal_mulai": (
                            tanggal_mulai.strftime("%d/%m/%Y")
                            if tanggal_mulai
                            else ""
                        ),

                        "tanggal_akhir": (
                            tanggal_akhir.strftime("%d/%m/%Y")
                            if tanggal_akhir
                            else ""
                        ),


                        "periode_pengajuan": (
                            f"{format_tanggal(tanggal_mulai)} "
                            f"s/d "
                            f"{format_tanggal(tanggal_akhir)} "
                            f"({jumlah_hari} Hari)"
                            if tanggal_mulai and tanggal_akhir
                            else ""
                        ),
                        
                        "jenis_pengajuan": clean_value(
                            row.get("jenis_pengajuan")
                        ),

                        "status": clean_value(
                            row.get("status")
                        ),

                        "keterangan": clean_value(
                            row.get("keterangan")
                        )
                    }
                })

                current_date += timedelta(days=1)

        return render_template(
            "view-kalender-ketersediaan-cuti.html",
            events=events
        )

    except Exception as e:

        print("=" * 100)
        print(
            "Terjadi error pada saat menampilkan data di halaman ini."
        )
        print(type(e))
        print(e)
        print("=" * 100)

        return render_template(
            "view-kalender-ketersediaan-cuti.html",
            events=[],
            error=str(e)
        )

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()