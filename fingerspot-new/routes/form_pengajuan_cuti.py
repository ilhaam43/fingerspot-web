# ======================================================================================================
# IMPORT LIBRARY
# ======================================================================================================
import os
import smtplib
import requests

from reportlab.lib.units import cm
from PIL import Image as PILImage

from datetime import datetime, timedelta
from typing import Any, cast
from utils.db import get_db_connection

from flask import Blueprint, render_template, request, redirect, url_for

from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage

# ======================================================================================================
# Blueprint
# ======================================================================================================
form_pengajuan_cuti = Blueprint("form_pengajuan_cuti", __name__)


# ======================================================================================================
# HELPER : PARSE TANGGAL & FORMAT PERIODE
# ======================================================================================================
def parse_date_value(value):
    """Parse supported Indonesian/ISO date formats into a datetime object."""

    if value is None:
        return None

    raw_value = str(value).strip()
    if not raw_value:
        return None

    if isinstance(value, datetime):
        return value

    month_map = {
        "januari": "January",
        "februari": "February",
        "maret": "March",
        "april": "April",
        "mei": "May",
        "juni": "June",
        "juli": "July",
        "agustus": "August",
        "september": "September",
        "oktober": "October",
        "november": "November",
        "desember": "December",
        "jan": "Jan",
        "feb": "Feb",
        "mar": "Mar",
        "apr": "Apr",
        "jun": "Jun",
        "jul": "Jul",
        "aug": "Aug",
        "sep": "Sep",
        "sept": "Sep",
        "okt": "Oct",
        "nov": "Nov",
        "des": "Dec",
    }

    normalized_value = raw_value
    for month_name, month_en in month_map.items():
        normalized_value = (
            normalized_value.lower().replace(month_name, month_en)
        )

    for fmt in (
        "%d-%m-%Y",
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d %B %Y",
        "%d %b %Y",
        "%Y-%m-%d %H:%M:%S",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(normalized_value, fmt)
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(normalized_value)
    except ValueError:
        return None

def format_indonesian_date(date_obj):
    if not date_obj:
        return ""

    month_names = {
        1: "Januari",
        2: "Februari",
        3: "Maret",
        4: "April",
        5: "Mei",
        6: "Juni",
        7: "Juli",
        8: "Agustus",
        9: "September",
        10: "Oktober",
        11: "November",
        12: "Desember",
    }

    return f"{date_obj.day:02d} {month_names[date_obj.month]} {date_obj.year}"

def parse_periode_tanggal(periode_tanggal):

    if not periode_tanggal:
        return None, None, 1

    raw_text = str(periode_tanggal).strip()
    
    if not raw_text:
        return None, None, 1

    if " s/d " in raw_text:
        separator = " s/d "
    elif " - " in raw_text:
        separator = " - "
    elif " to " in raw_text:
        separator = " to "
    else:
        separator = None

    tanggal = [
        part.strip()
        for part in raw_text.split(separator, 1)
    ]

    raw_mulai = tanggal[0]
    raw_akhir = tanggal[1] if len(tanggal) > 1 else raw_mulai

    dt_mulai = parse_date_value(raw_mulai)
    dt_akhir = parse_date_value(raw_akhir)

    if dt_mulai is None:
        raise ValueError(f"Tanggal mulai tidak valid: {raw_mulai!r}")

    if dt_akhir is None:
        raise ValueError(f"Tanggal akhir tidak valid: {raw_akhir!r}")

    if dt_akhir < dt_mulai:
        raise ValueError("Tanggal akhir tidak boleh lebih kecil dari tanggal mulai.")

    jumlah_hari = hitung_hari_cuti(dt_mulai, dt_akhir)

    return (
        dt_mulai.strftime("%Y-%m-%d"),
        dt_akhir.strftime("%Y-%m-%d"),
        jumlah_hari,
    )

def format_periode_fsk(periode):
    """Format date range into Indonesian display format."""

    try:
        if not periode:
            return "-"

        raw_value = str(periode).strip()
        if " s/d " in raw_value:
            awal_raw, akhir_raw = raw_value.split(" s/d ", 1)
        elif " - " in raw_value:
            awal_raw, akhir_raw = raw_value.split(" - ", 1)
        else:
            awal_raw = raw_value
            akhir_raw = None

        tgl_awal = parse_date_value(awal_raw)
        if not tgl_awal:
            return raw_value

        if akhir_raw:
            if tgl_akhir := parse_date_value(akhir_raw):
                if tgl_awal.date() == tgl_akhir.date():
                    return format_indonesian_date(tgl_awal)
                return (
                    f"{format_indonesian_date(tgl_awal)} "
                    f"s/d "
                    f"{format_indonesian_date(tgl_akhir)}"
                )

        return format_indonesian_date(tgl_awal)

    except Exception:
        return periode

def get_hari_libur_nasional(year):
    try:

        response = requests.get(
            f"https://api-hari-libur.vercel.app/api?year={year}",
            timeout=10
        )

        if response.status_code != 200:
            return set()

        data = response.json()

        hari_libur = set()

        if isinstance(data, list):

            for item in data:

                if tanggal := (
                    item.get("holiday_date")
                    or item.get("date")
                ): hari_libur.add(tanggal)

        return hari_libur

    except Exception as e:
        print("An error occurred while retrieving the national holiday calendar. :", e)
        return set()

def hitung_hari_cuti(dt_mulai, dt_akhir):
    
    hari_libur = set()

    for tahun in range(
        dt_mulai.year,
        dt_akhir.year + 1
    ):
        hari_libur.update(
            get_hari_libur_nasional(tahun)
        )

    print("=" * 50)
    print("DAFTAR HARI LIBUR")
    print(sorted(hari_libur))
    print("=" * 50)

    total = 0

    current = dt_mulai

    while current <= dt_akhir:

        tanggal_str = current.strftime("%Y-%m-%d")

        is_weekend = current.weekday() in (5, 6)

        is_libur_nasional = tanggal_str in hari_libur

        print(
            tanggal_str,
            "WEEKEND =", is_weekend,
            "LIBUR =", is_libur_nasional
        )

        if not is_weekend and not is_libur_nasional:
            total += 1

        current += timedelta(days=1)

    print("TOTAL =", total)

    return total

def format_tanggal_jam(dt):
    
    if not dt: return "-"

    bulan_id = {
        1: "Januari",
        2: "Februari",
        3: "Maret",
        4: "April",
        5: "Mei",
        6: "Juni",
        7: "Juli",
        8: "Agustus",
        9: "September",
        10: "Oktober",
        11: "November",
        12: "Desember"
    }

    return (
        f"{dt.day} "
        f"{bulan_id[dt.month]} "
        f"{dt.year} "
        f"(Jam {dt.strftime('%H:%M:%S')})"
    )

def get_project_root():
    return os.path.dirname(
        os.path.dirname(
            os.path.abspath(__file__)
        )
    )

def log_file_status(label, file_path):
    print("=" * 100)
    print(f"{label} EXISTS :", os.path.exists(file_path))
    print(f"{label} PATH   :", file_path)
    print("=" * 100)

# ======================================================================================================
# PDF : FORM SURAT KETERANGAN CUTI TAHUNAN (FSKCT)
# ======================================================================================================
@form_pengajuan_cuti.route('/create_pdf_fskct/<int:pengajuan_id>')
def create_pdf_fskct(pengajuan_id):

    from reportlab.lib import colors
    from reportlab.lib import enums
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        Image
    )
    
    # ==========================================================================================
    # QUERY DATA
    # ==========================================================================================

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT 
        pc.*, 
        p.pegawai_nama, 
        pm.pegawai_nama AS project_manager_nama, 
        pimp.nama AS pimpinan_nama, 
        b.pegawai_nama AS backup_nama, 
        pb3.pembagian3_nama AS tim_nama 
        FROM 
        pengajuan_cuti_new pc 
        LEFT JOIN pegawai p ON p.pegawai_id = pc.Personil_Id 
        LEFT JOIN pegawai pm ON pm.pegawai_id = pc.Project_Manager 
        LEFT JOIN pengajuan_nama_supervisor_form pimp ON pimp.id = pc.PimpinanOrSupervisor_Id 
        LEFT JOIN pegawai b ON b.pegawai_id = pc.Backup_Id 
        LEFT JOIN pembagian3 pb3 ON pb3.pembagian3_id = pc.Tim_Id 
        WHERE 
        pc.Id = %s
    """, (pengajuan_id,))

    data = cursor.fetchone()

    print("=" * 100)
    print(data)
    print("=" * 100)

    cursor.close()
    conn.close()
    
    if not data:
        return "Data not found"
    
    data_dict = cast(dict[str, Any], data)
    kategori_cuti = str(data_dict.get("Kategori_Cuti") or "").strip().lower()
    
    if kategori_cuti == "cuti mendadak":
        judul_form = "FORM CUTI TIDAK TERENCANA"
        informasi_form = "Informasi Cuti Tidak Terencana"
    elif kategori_cuti == "cuti tahunan":
        judul_form = "FORM SURAT KETERANGAN CUTI TAHUNAN"
        informasi_form = "Informasi Pengajuan Cuti Tahunan"
    elif kategori_cuti == "cuti extra":
        judul_form = "FORM SURAT KETERANGAN CUTI EXTRA"
        informasi_form = "Informasi Pengajuan Cuti Extra"
    else:

        judul_form = "FORM PENGAJUAN CUTI"
        informasi_form = "Informasi Pengajuan Cuti"

    # ==========================================================================================
    # FORMAT TANGGAL
    # ==========================================================================================
    bulan_id = {
        1:"Januari",
        2:"Februari",
        3:"Maret",
        4:"April",
        5:"Mei",
        6:"Juni",
        7:"Juli",
        8:"Agustus",
        9:"September",
        10:"Oktober",
        11:"November",
        12:"Desember"
    }

    def format_tanggal(dt):
        
        if not dt:
            return "-"

        if isinstance(dt, str):
            dt = datetime.strptime(dt, "%Y-%m-%d")

        return (
            f"{dt.day} "
            f"{bulan_id[dt.month]} "
            f"{dt.year}"
        )

    # ==========================================================================================
    # PDF SETUP
    # ==========================================================================================
    project_root = get_project_root()

    output_folder = os.path.join(
        project_root,
        "generated_pdf"
    )

    os.makedirs(
        output_folder,
        exist_ok=True
    )

    styles = getSampleStyleSheet()
    
    style_center = ParagraphStyle(
        'center',
        parent=styles['Normal'],
        alignment=enums.TA_CENTER,
        fontSize=9
    )

    style_right = ParagraphStyle(
        'right',
        parent=styles['Normal'],
        alignment=enums.TA_RIGHT,
        fontSize=10,
        leading=12,
        fontName='Helvetica',
        spaceBefore=20, # tambahin ini juga
    )

    style_title = ParagraphStyle(
        'title',
        parent=styles['Normal'],
        alignment=enums.TA_CENTER,
        fontSize=12,
        leading=14,
        fontName='Helvetica-Bold',
        spaceBefore=20, # tambahin ini
    )

    story = []

    # ==========================================================================================
    # HEADER
    # ==========================================================================================

    # ==================================================
    # LOGO BI
    # ==================================================

    project_root = get_project_root()

    logo_candidates = [
        os.path.join(project_root, "images", "Logo_Bank_Indonesia.png"),
        os.path.join(project_root, "images", "logo_bank_indonesia.png"),
        os.path.join(project_root, "images", "logo_bi.png"),
    ]

    logo_path = next(
        (
            p
            for p in logo_candidates
            if os.path.exists(p)
        ),
        None
    )

    logo = Paragraph("", styles["Normal"])

    try:

        if logo_path:

            with PILImage.open(logo_path) as img:
                width_px, height_px = img.size
                ratio = width_px / height_px
                logo = Image(logo_path)
                logo_height = 1.7 * cm
                logo.drawHeight = logo_height
                logo.drawWidth = logo_height * ratio

    except Exception as e:

        print("An error occurred while displaying the logo image. :", e)

        logo = Paragraph("", styles["Normal"])
            
    header = Table(
        [[
            logo,

            Paragraph(
                f"<b>{judul_form}</b>",
                style_title
            ),

            Paragraph(
                format_tanggal(datetime.now()),
                style_right
            )
        ]],
        colWidths=[120, 290, 105]
    )

    header.setStyle(TableStyle([

        # Logo tetap atas
        ('VALIGN',(0,0),(0,0),'TOP'),

        # Judul + tanggal turun ke bawah
        ('VALIGN',(1,0),(2,0),'BOTTOM'),

        ('ALIGN',(0,0),(0,0),'CENTER'),
        ('ALIGN',(1,0),(1,0),'CENTER'),
        ('ALIGN',(2,0),(2,0),'RIGHT'),

    ]))

    story.append(header)
    story.append(Spacer(1,5))

    # ==========================================================================================
    # BODY
    # ==========================================================================================
    body_data = [
        [
            Paragraph("<b>No.</b>", style_center),
            Paragraph(f"<b>Bank Indonesia</b><br/><b>{informasi_form}</b>", style_center), ""
        ],
        ["1", "NIK Lintasarta", data_dict.get("Nik_Lintasarta", "-")],
        ["", "Nama Lengkap", data_dict.get("pegawai_nama") or "-"],
        [
            "",
            "Tanggal & Jam Pengajuan",
            format_tanggal_jam(data_dict["Submitted_Date"])
        ],
        ["", "Tanggal Mulai", format_tanggal(data_dict["Tanggal_Mulai"])],
        ["", "Tanggal Akhir", format_tanggal(data_dict["Tanggal_Akhir"])],
        ["", "Sisa Cuti (Awal)", str(data_dict["Sisa_Cuti_Awal"])],
        ["", "Jumlah Hari Cuti", f"{data_dict['Jumlah_Hari']} Hari"],
        ["", "Sisa Pemakaian Cuti (Akhir)", str(data_dict["Sisa_Cuti_Akhir"])],
        ["", "Kategori Cuti", data_dict["Kategori_Cuti"]],
        [
            "",
            "Tim",
            f"{data_dict['Tim_Personil']} ({data_dict.get('tim_nama', '-')})"
        ],
        ["", "Nama Personil Backup", data_dict["backup_nama"] or "-"],
        ["", "Keperluan Cuti (Keterangan)", data_dict["Keterangan"]],
    ]

    body_table = Table(
        body_data,
        colWidths=[35,190,290]
    )

    body_table.setStyle(TableStyle([

        # ==================================================
        # DEFAULT PADDING
        # ==================================================
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),

        # ==================================================
        # BORDER
        # ==================================================
        ('GRID', (0,0), (-1,-1), 0.7, colors.black),

        # ==================================================
        # HEADER
        # ==================================================
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#D9D9D9')),

        ('TOPPADDING', (0,0), (-1,0), 6),
        ('BOTTOMPADDING', (0,0), (-1,0), 6),

        # ==================================================
        # MERGE
        # ==================================================

        # Header kanan
        ('SPAN', (1,0), (2,0)),

        # Nomor 1 merge sampai bawah
        ('SPAN', (0,1), (0,-1)),

        # ==================================================
        # FONT
        # ==================================================
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),

        # Label kiri
        ('FONTNAME', (1,1), (1,-1), 'Helvetica-Bold'),

        # ==================================================
        # ALIGNMENT
        # ==================================================
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),

        # Header center
        ('ALIGN', (0,0), (2,0), 'CENTER'),

        # Kolom nomor
        ('ALIGN', (0,1), (0,-1), 'CENTER'),

        # Label kiri
        ('ALIGN', (1,1), (1,-1), 'LEFT'),

        # Value kanan
        ('ALIGN', (2,1), (2,-1), 'LEFT'),

    ]))    
    
    story.append(body_table)
    story.append(Spacer(1,25))

    # ==========================================================================================
    # TTD
    # ==========================================================================================

    sign_table = Table(
        [
            [
                Paragraph(
                    f"<b>Personil,<br/>({data_dict['Tim_Personil']})</b>",
                    style_center
                ),

                Paragraph(
                    "<b>Mengetahui,<br/>Pimpinan</b>",
                    style_center
                )
            ],
            ["", ""],
            [
                Paragraph(
                    f"<u>({data_dict['pegawai_nama']})</u>",
                    style_center
                ),

                Paragraph(
                    f"<u>({data_dict.get('pimpinan_nama') or '-'})</u>",
                    style_center
                )
            ]
        ],
        colWidths=[257,258]
    )

    sign_table.setStyle(TableStyle([
        
        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),

        ('BOTTOMPADDING',(0,1),(-1,1),60),

    ]))

    story.append(sign_table)
    story.append(Spacer(1,55))

    pm_table = Table(
        [
            [
                Paragraph(
                    "<b>Menyetujui,<br/>Project Manager</b>",
                    style_center
                )
            ],
            [
                ""
            ],
            [
                Paragraph(
                    f"<u>({data_dict.get('project_manager_nama') or '-'})</u>",
                    style_center
                )
            ]
        ],
        colWidths=[515]
    )

    pm_table.setStyle(TableStyle([

        ('ALIGN',(0,0),(-1,-1),'CENTER'),
        ('VALIGN',(0,0),(-1,-1),'MIDDLE'),

        # Project Manager ke Nama PM
        ('BOTTOMPADDING',(0,1),(0,1),35),

    ]))

    story.append(pm_table)

    # Nama PM ke Nomor Surat
    story.append(Spacer(1, 35))

    # ==========================================================================================
    # FOOTER FILE NAME
    # ==========================================================================================

    file_name = (
        f"FSKCT_{data_dict.get('Nik_Lintasarta', pengajuan_id)}_"
        f"{datetime.now().strftime('%d%m%Y_%H%M%S')}"
    )

    story.append(
        Paragraph(
            file_name,
            styles["BodyText"]
        )
    )

    # ==========================================================================================
    # PDF PATH
    # ==========================================================================================

    pdf_path = os.path.join(
        output_folder,
        f"{file_name}.pdf"
    )

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=25,
        rightMargin=25,
        topMargin=40,
        bottomMargin=25
    )

    # ==========================================================================================
    # BUILD PDF
    # ==========================================================================================
    doc.build(story)

    print("=" * 100)
    print("PDF CREATED")
    print("PDF PATH :", pdf_path)
    print("=" * 100)

    return pdf_path

# ======================================================================================================
# EMAIL
# ======================================================================================================
def kirim_email_cuti_mendadak(
    recipient_email,
    nama_penerima,
    nik_lintasarta,
    kategori_cuti,
    pdf_path
):
    
    sender_email = os.getenv(
        "SMTP_EMAIL"
    )

    sender_password = os.getenv(
        "SMTP_PASSWORD"
    )
    
    if not sender_email:
        raise ValueError("SMTP_EMAIL belum diset")

    if not sender_password:
        raise ValueError("SMTP_PASSWORD belum diset")

    if not recipient_email:
        raise ValueError("Recipient email is empty.")

    # ==================================================
    # FOOTER IMAGE
    # ==================================================

    project_root = get_project_root()

    footer_path = os.path.join(
        project_root,
        "images",
        "Footer_Email_Lintasarta.png"
    )

    log_file_status("FOOTER", footer_path)


    # ==================================================
    # SUBJECT
    # ==================================================

    subject = (
        f"[RPA] Form Pengajuan {kategori_cuti} "
        f"{nik_lintasarta} - "
        f"{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    )

    # ==================================================
    # CC & BCC
    # ==================================================

    cc_emails = [
        "demo@example.com"
    ]

    bcc_emails = [
        "demo@example.com"
    ]

    # ==================================================
    # BODY
    # ==================================================
    body = f"""
    <html>
        <body style="font-family: Arial, Helvetica, sans-serif; font-size:13px; color:#333333;">
            <p>
                Dear <b>{nama_penerima}</b>,
            </p>
            <p>
                PFA.
            </p>
            <p>
                This is an auto generated email, please do not reply to this email.
            </p>
            <p>
                Regards,<br>
                <b>RPA</b>
            </p>
            <img src="cid:Footer_Email_Lintasarta" alt="Footer" style="width:100%; max-width:700px; display:block; border:0;">
        </body>
    </html>
    """

    # ==================================================
    # MESSAGE
    # ==================================================

    msg = MIMEMultipart("related")

    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg["Cc"] = ", ".join(cc_emails)
    msg["Subject"] = subject

    alt_part = MIMEMultipart("alternative")
    alt_part.attach(MIMEText(body, "html", "utf-8"))
    msg.attach(alt_part)

    # ==================================================
    # FOOTER IMAGE
    # ==================================================
    if os.path.exists(footer_path):

        with open(
            footer_path,
            "rb"
        ) as img:

            footer_img = MIMEImage(
                img.read()
            )

            footer_img.add_header(
                "Content-ID",
                "<Footer_Email_Lintasarta>"
            )

            footer_img.add_header(
                "Content-Disposition",
                'inline; filename="Footer_Email_Lintasarta.png"'
            )

            footer_img.add_header(
                "X-Attachment-Id",
                "Footer_Email_Lintasarta"
            )

            msg.attach(
                footer_img
            )

    # ==================================================
    # PDF ATTACHMENT
    # ==================================================

    if not pdf_path: raise ValueError("The PDF path (location) is empty or not found.")

    log_file_status("PDF", pdf_path)

    if not os.path.exists(pdf_path): raise FileNotFoundError(f"PDF file not found : {pdf_path}")

    with open(
        pdf_path,
        "rb"
    ) as pdf_file:

        attachment = MIMEApplication(
            pdf_file.read(),
            _subtype="pdf"
        )
        attachment.add_header(
            "Content-Disposition",
            "attachment",
            filename=os.path.basename(
                pdf_path
            )
        )
        msg.attach(
            attachment
        )
        
    print(f"PDF file attached : {os.path.basename(pdf_path)}")
    print("=" * 100)
    print("Sending an email to request last-minute leave")
    print("TO :", recipient_email)
    print("PDF :", pdf_path)
    print("SUBJECT :", subject)
    print("=" * 100)

    # ==================================================
    # SMTP
    # ==================================================

    try:

        with smtplib.SMTP(
            "smtp.mail.yahoo.com",
            587,
            timeout=30
        ) as server:

            server.ehlo()
            print("EHLO OK")

            server.starttls()
            server.ehlo()
            print("TLS OK")

            server.login(
                sender_email,
                sender_password
            )
            print("LOGIN OK")

            all_recipients = ([recipient_email] + cc_emails + bcc_emails)

            server.sendmail(
                sender_email,
                all_recipients,
                msg.as_string()
            )

            print("Send Mail.. OK")
            print("Email Successfully Sent")

    except Exception as e:

        print("=" * 100)
        print("ERROR EMAIL PENGAJUAN CUTI")
        print(type(e))
        print(e)
        print("=" * 100)

        raise

# ======================================================================================================
# FORM PENGAJUAN
# ======================================================================================================
@form_pengajuan_cuti.route("/form-pengajuan-cuti", methods=["GET", "POST"])
def form_pengajuan():
    
    # ==========================================
    # POST
    # ==========================================

    if request.method == "POST":

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        def require_value(value, message):
            if value is None or not str(value).strip():
                raise ValueError(message)

        def fetch_single_value(query, params, error_message, column_name):
            cursor.execute(query, params)
            row = cursor.fetchone()
            if row is None:
                raise ValueError(error_message)
            return row[column_name]

        try:

            tim_personil = (
                request.form["tim_personil"]
                if "tim_personil" in request.form
                else None
            )
            email = request.form["email"] if "email" in request.form else None
            kategori_cuti = (
                request.form["kategori_cuti"]
                if "kategori_cuti" in request.form
                else None
            )
            periode_tanggal = (
                request.form["periode_tanggal"]
                if "periode_tanggal" in request.form
                else None
            )
            project_manager = (
                request.form["project_manager"]
                if "project_manager" in request.form
                else None
            )
            supervisor = (
                request.form["supervisor"]
                if "supervisor" in request.form
                else None
            )
            keterangan = (
                request.form["keterangan"]
                if "keterangan" in request.form
                else None
            )

            tim_map = {
                "HDTI": {
                    "nik": "nik_lintasarta_hdti",
                    "tim": "tim_hdti",
                    "personil": "personil_hdti",
                    "backup": "personil_backup_hdti",
                },
                "HDBS": {
                    "nik": "nik_lintasarta_hdbs",
                    "tim": "tim_hdbs",
                    "personil": "personil_hdbs",
                    "backup": "personil_backup_hdbs",
                },
            }

            if tim_personil not in tim_map:
                raise ValueError("Tim personil belum dipilih.")

            selected_tim = tim_map[tim_personil]
            nik_key = selected_tim["nik"]
            nik_lintasarta = request.form[nik_key] if nik_key in request.form else None
            tim_key = selected_tim["tim"]
            tim_id = request.form[tim_key] if tim_key in request.form else None
            personil_key = selected_tim["personil"]
            personil_id = request.form[personil_key] if personil_key in request.form else None
            backup_key = selected_tim["backup"]
            backup_id = request.form[backup_key] if backup_key in request.form else None

            require_value(tim_id, "Tim belum dipilih.")
            require_value(personil_id, "Personil belum dipilih.")
            require_value(supervisor, "Supervisor belum dipilih.")

            # ==========================================
            # DATA PERSONIL
            # ==========================================

            cursor.execute(
                """
                SELECT
                    pegawai_nama,
                    COALESCE(sisa_cuti, 0) AS sisa_cuti
                FROM pegawai
                WHERE pegawai_id = %s
                """,
                (personil_id,)
            )

            pegawai_data = cast(dict[str, Any] | None, cursor.fetchone())

            if not pegawai_data:
                raise ValueError("Data pegawai tidak ditemukan.")

            nama_personil = pegawai_data["pegawai_nama"]
            sisa_cuti_awal = int(pegawai_data["sisa_cuti"])

            # ==========================================
            # VALIDASI BACKUP
            # ==========================================

            require_value(
                backup_id,
                "Personil backup belum dipilih."
            )

            if personil_id == backup_id:
                raise ValueError(
                    "Personil backup tidak boleh sama dengan personil utama."
                )

            # ==========================================
            # DATA BACKUP
            # ==========================================

            cursor.execute(
                """
                SELECT
                    pegawai_nama
                FROM pegawai
                WHERE pegawai_id = %s
                """,
                (backup_id,)
            )

            backup_data = cast(dict[str, Any] | None, cursor.fetchone())

            if not backup_data:
                raise ValueError(
                    "Data backup tidak ditemukan."
                )

            nama_backup = backup_data["pegawai_nama"]

            # ==========================================
            # DATA SUPERVISOR
            # ==========================================

            nama_supervisor = fetch_single_value(
                """
                SELECT
                    nama
                FROM pengajuan_nama_supervisor_form
                WHERE id = %s
                """,
                (supervisor,),
                "Supervisor tidak ditemukan.",
                "nama"
            )

            # ==========================================
            # VALIDASI PERIODE
            # ==========================================

            tanggal_mulai, tanggal_akhir, jumlah_hari = (
                parse_periode_tanggal(
                    periode_tanggal
                )
            )

            if jumlah_hari <= 0:
                raise ValueError(
                    "Periode yang dipilih hanya berisi hari Sabtu, Minggu atau Hari Libur Nasional."
                )

            # ==================================================
            # CEK CUTI BENTROK
            # ==================================================
            cursor.execute("""
            SELECT
                Id,
                Personil_Id,
                Status_Pengajuan,
                Tanggal_Mulai,
                Tanggal_Akhir
            FROM pengajuan_cuti_new
            WHERE
                Personil_Id = %s
                AND Status_Pengajuan IN (
                    'Pending',
                    'Approved',
                    'Received'
                )
                AND (IsDelete = 0 OR IsDelete IS NULL)
                AND (
                    Tanggal_Mulai <= %s
                    AND Tanggal_Akhir >= %s
                )
            ORDER BY Id DESC
            """,
            (
                personil_id,
                tanggal_akhir,
                tanggal_mulai
            ))

            data_bentrok = cursor.fetchall()

            print("=" * 100)
            print("PERSONIL ID :", personil_id)
            print("TANGGAL MULAI :", tanggal_mulai)
            print("TANGGAL AKHIR :", tanggal_akhir)
            print("DATA BENTROK :")
            print(data_bentrok)
            print("=" * 100)

            if data_bentrok:
                raise ValueError(
                    "Sudah ada pengajuan cuti pada periode tersebut."
                )
            # ==================================================
            
            
            # ==================================================
            # VALIDASI KATEGORI CUTI & TANGGAL
            # ==================================================
            
            today = datetime.now().date()

            if tanggal_mulai is None: 
                raise ValueError("Tanggal mulai cuti wajib diisi.")

            kategori_normal = (kategori_cuti or "").strip().lower()
            tanggal_mulai_date = datetime.strptime(tanggal_mulai, "%Y-%m-%d").date()

            # Hanya Cuti Tahunan & Extra yang tidak boleh back date
            if (kategori_normal in ["cuti tahunan", "cuti extra"] and tanggal_mulai_date < today):
                raise ValueError("Tanggal cuti tidak boleh kurang dari hari ini.")
            if kategori_normal == "cuti mendadak":
                status_pengajuan = "Received"
            elif kategori_normal in ["cuti tahunan", "cuti extra"]:
                status_pengajuan = "Pending"
            else:
                raise ValueError(f"Kategori cuti tidak valid: {kategori_cuti}")

            # ==================================================
            # VALIDASI SALDO CUTI
            # ==================================================
            if kategori_normal in ["cuti tahunan", "cuti mendadak"]:

                if sisa_cuti_awal <= 0:
                    raise ValueError("Sisa cuti sudah habis (0 hari).")

                if jumlah_hari > sisa_cuti_awal:
                    raise ValueError(f"""
                        Pengajuan cuti tidak dapat diproses.
                        Sisa cuti yang tersedia : {sisa_cuti_awal} hari
                        Jumlah hari kerja yang diajukan : {jumlah_hari} hari
                        """)

                sisa_cuti_akhir = (sisa_cuti_awal - jumlah_hari)

            elif kategori_normal == "cuti extra":
                sisa_cuti_akhir = sisa_cuti_awal
            else:
                raise ValueError(f"Kategori cuti tidak valid: {kategori_cuti}")

            query_insert = """
            INSERT INTO pengajuan_cuti_new
            (
                Tim_Personil,
                Nik_Lintasarta,
                Alamat_Email,
                Tim_Id,
                Personil_Id,
                Backup_Id,
                Kategori_Cuti,
                Tanggal_Mulai,
                Tanggal_Akhir,
                Sisa_Cuti_Awal,
                Jumlah_Hari,
                Sisa_Cuti_Akhir,
                PimpinanOrSupervisor_Id,
                Project_Manager,
                Keterangan,
                Status_Pengajuan,
                Sisa_Cuti,
                IsActive,
                IsDelete,
                Submitted_Date
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                1,
                0,
                NOW()
            )
            """

            cursor.execute(
                query_insert,
                (
                    tim_personil,
                    nik_lintasarta,
                    email,
                    tim_id,
                    personil_id,
                    backup_id,
                    kategori_cuti,
                    tanggal_mulai,
                    tanggal_akhir,
                    sisa_cuti_awal,
                    jumlah_hari,
                    sisa_cuti_akhir,
                    supervisor,
                    project_manager,
                    keterangan,
                    status_pengajuan,
                    sisa_cuti_akhir,
                ),
            )

            pengajuan_id = cursor.lastrowid
            connection.commit()

            print("=" * 100)
            print("DEBUG DATA CUTI")
            print(f"PERSONIL        : {nama_personil}")
            print(f"BACKUP          : {nama_backup}")
            print(f"KATEGORI CUTI   : {kategori_cuti}")
            print(f"PERIODE         : {periode_tanggal}")
            print(f"TANGGAL MULAI   : {tanggal_mulai}")
            print(f"TANGGAL AKHIR   : {tanggal_akhir}")
            print(f"SISA CUTI AWAL  : {sisa_cuti_awal}")
            print(f"JUMLAH HARI     : {jumlah_hari}")
            print(f"SISA CUTI AKHIR : {sisa_cuti_akhir}")
            print(f"SUPERVISOR      : {nama_supervisor}")

            if kategori_normal == "cuti extra":
                print("MODE            : TIDAK MEMOTONG SALDO CUTI")
            else:
                print("MODE            : MEMOTONG SALDO CUTI")

            print("=" * 100)

            if kategori_normal == "cuti mendadak" and status_pengajuan == "Received":
                print("=" * 100)
                print("Process emails and PDF files for emergency leave requests.")
                print("PENGAJUAN ID :", pengajuan_id)
                print("=" * 100)

                pdf_path = create_pdf_fskct(pengajuan_id)

                print("=" * 100)
                print("PDF BERHASIL DIBUAT")
                print("PDF PATH :", pdf_path)
                print("=" * 100)

                try:
                    kirim_email_cuti_mendadak(
                        recipient_email=email,
                        nama_penerima=nama_personil,
                        nik_lintasarta=nik_lintasarta,
                        kategori_cuti=kategori_cuti,
                        pdf_path=pdf_path
                    )
                    print("=" * 100)
                    print("Email successfully sent")
                    print("=" * 100)
                except Exception as email_error:
                    print("=" * 100)
                    print("The email requesting emergency leave failed to send.")
                    print(type(email_error))
                    print(email_error)
                    print("=" * 100)

            return redirect(url_for("form_pengajuan_cuti.form_pengajuan", status="success"))

        except Exception as e:

            import traceback

            connection.rollback()

            print("=" * 100)
            print("ERROR MENYIMPAN CUTI")
            print("ERROR TYPE   : ", type(e))
            print("ERROR        : ", e)
            traceback.print_exc()
            print("=" * 100)

            return redirect(
                url_for(
                    "form_pengajuan_cuti.form_pengajuan",
                    status="error",
                    message=str(e),
                )
            )

        finally:
            cursor.close()
            connection.close()

    # ==========================================
    # GET
    # ==========================================

    connection = get_db_connection()

    cursor = connection.cursor(dictionary=True)

    data_tim_hdti = []
    data_personil_hdti = []
    data_tim_hdbs = []
    data_personil_hdbs = []
    data_project_manager = []
    data_supervisor = []

    try:

        # ==========================================
        # TIM HDTI
        # ==========================================

        cursor.execute("""
            SELECT
                pembagian3_id,
                pembagian3_nama
            FROM pembagian3
            WHERE LOWER(pembagian3_ket)
            LIKE '%hdti%'
            ORDER BY pembagian3_nama
        """)

        data_tim_hdti = cursor.fetchall()

        # ==========================================
        # PERSONIL HDTI
        # ==========================================

        cursor.execute("""
            SELECT
                p.pegawai_id,
                p.pegawai_nama,
                p.pegawai_nik_lintas AS pegawai_nik_lintas,
                COALESCE(p.sisa_cuti, 0) AS sisa_cuti
            FROM pegawai p
            INNER JOIN pembagian1 b1
                ON p.pembagian1_id = b1.pembagian1_id
            INNER JOIN pembagian3 b3
                ON p.pembagian3_id = b3.pembagian3_id
            WHERE LOWER(b1.pembagian1_nama)
                LIKE '%hdti%'
            AND LOWER(b3.pembagian3_ket)
                LIKE '%hdti%'
            ORDER BY p.pegawai_nama
        """)

        data_personil_hdti = cursor.fetchall()

        # ==========================================
        # TIM HDBS
        # ==========================================

        cursor.execute("""
            SELECT
                pembagian3_id,
                pembagian3_nama
            FROM pembagian3
            WHERE LOWER(pembagian3_ket)
                  LIKE '%hdbs%'
            ORDER BY pembagian3_nama
        """)

        data_tim_hdbs = cursor.fetchall()

        # ==========================================
        # PERSONIL HDBS
        # ==========================================

        cursor.execute("""
            SELECT
                p.pegawai_id,
                p.pegawai_nama,
                p.pegawai_nik_lintas AS pegawai_nik_lintas,
                COALESCE(p.sisa_cuti, 0) AS sisa_cuti
            FROM pegawai p
            INNER JOIN pembagian1 b1
                ON p.pembagian1_id = b1.pembagian1_id
            INNER JOIN pembagian3 b3
                ON p.pembagian3_id = b3.pembagian3_id
            WHERE LOWER(b1.pembagian1_nama)
                LIKE '%hdbs%'
            AND LOWER(b3.pembagian3_ket)
                LIKE '%hdbs%'
            ORDER BY p.pegawai_nama
        """)


        data_personil_hdbs = cursor.fetchall()

        # ==========================================
        # AMBIL NAMA PROJECT MANAGER
        # ==========================================

        cursor.execute("""
            SELECT
                pegawai_id,
                pegawai_nama
            FROM pegawai
            WHERE 
            pegawai_nik_lintas IN (098209384092834)
            AND pegawai_nama = 'MOHAMAD USMAN ANDI'
            AND pegawai_status = 1
            ORDER BY pegawai_nama
        """)

        data_project_manager = cursor.fetchall()
    
        # ==========================================
        # AMBIL NAMA SUPERVISOR
        # ==========================================

        cursor.execute("""
            SELECT
                id,
                nama
            FROM pengajuan_nama_supervisor_form
            ORDER BY nama
        """)

        data_supervisor = cursor.fetchall()

    except Exception as e:

        print(f"Error getting data: {e}")

    finally:

        cursor.close()
        connection.close()

    return render_template(
        "form-pengajuan-cuti.html",
        data_tim_hdti=data_tim_hdti,
        data_personil_hdti=data_personil_hdti,
        data_tim_hdbs=data_tim_hdbs,
        data_personil_hdbs=data_personil_hdbs,
        data_project_manager=data_project_manager,
        data_supervisor=data_supervisor,
    )


# ======================================================================================================
