# ======================================================================================================
# IMPORT LIBRARY
# ======================================================================================================
import os
import smtplib
import requests
import traceback
import datetime
import utils.db

from flask import (Blueprint, render_template, request, redirect, url_for)
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
from email.mime.image import MIMEImage

from PIL import Image as PILImage
from reportlab.lib.units import cm
from reportlab.platypus import Image as ReportLabImage, Paragraph
from reportlab.lib import colors

from dotenv import load_dotenv
load_dotenv()
# ======================================================================================================

# ======================================================================================================
# Blueprint
# ======================================================================================================
form_pengajuan_tidak_hadir = Blueprint('form_pengajuan_tidakhadir', __name__)

# ======================================================================================================
# HELPER : 
# ======================================================================================================
BULAN_INDONESIA = {
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

def parse_tanggal(value):

    if not value:
        return None

    for fmt in ("%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.datetime.strptime(value.strip(), fmt)
        except ValueError:
            continue
    return None

def format_tanggal_indonesia(tanggal):
    
    if not tanggal:
        return "-"

    return (
        f"{tanggal.day:02d} "
        f"{BULAN_INDONESIA[tanggal.month]} "
        f"{tanggal.year}"
    )

def parse_periode_tanggal(periode_tanggal):
    
    if not periode_tanggal: return None, None, 0

    separator = (
        ' s/d '
        if ' s/d ' in periode_tanggal
        else ' - '
    )

    tanggal = periode_tanggal.split(separator, 1)

    raw_mulai = tanggal[0].strip()

    raw_akhir = (tanggal[1].strip()
        if len(tanggal) > 1
        else raw_mulai
    )

    dt_mulai = None
    dt_akhir = None

    for fmt in ("%d-%m-%Y", "%Y-%m-%d"):
        try:
            dt_mulai = datetime.datetime.strptime(raw_mulai, fmt)
            dt_akhir = datetime.datetime.strptime(raw_akhir, fmt)
            break
        except ValueError:
            continue

    if not dt_mulai or not dt_akhir:
        return None, None, 0

    if dt_akhir < dt_mulai:
        return None, None, 0

    jumlah_hari = (dt_akhir - dt_mulai).days + 1

    return (
        dt_mulai.strftime("%Y-%m-%d"),
        dt_akhir.strftime("%Y-%m-%d"),
        jumlah_hari
    )

def format_periode_fsk(periode):
    
    if not periode:
        return "-"

    if " s/d " in periode:
        awal_raw, akhir_raw = periode.split(" s/d ", 1)
    elif " - " in periode:
        awal_raw, akhir_raw = periode.split(" - ", 1)
    else:
        awal_raw, akhir_raw = periode, None

    tgl_awal = parse_tanggal(awal_raw)

    if not tgl_awal:
        return periode

    if not akhir_raw:
        return format_tanggal_indonesia(tgl_awal)

    tgl_akhir = parse_tanggal(akhir_raw)
    if not tgl_akhir or tgl_awal.date() == tgl_akhir.date():
        return format_tanggal_indonesia(tgl_awal)

    return f"{format_tanggal_indonesia(tgl_awal)} s/d {format_tanggal_indonesia(tgl_akhir)}"

def print_separator():
    print("=" * 100)

def report_email_send_failure(error):
    """Report an email delivery failure without interrupting the submission."""
    print_separator()
    print("Failed to send email to the user.")
    print(error)
    print_separator()

def close_db_resources(cursor, connection):
    """Close the database cursor and connection when they are available."""
    if cursor is not None:
        cursor.close()
    if connection is not None:
        connection.close()

def build_pdf_document(doc, elements):
    """Build the PDF from the supplied document elements."""
    doc.build(elements)

def create_fsk_pdf_document(doc, elements):
    """Create the FSK PDF document from its elements."""
    build_pdf_document(doc, elements)

def report_fsk_pdf_created(pdf_path):
    """Report details about the generated FSK PDF."""
    print_separator()
    print("PDF CREATED")
    print("PATH :", pdf_path)
    print("SIZE :", os.path.getsize(pdf_path), "bytes")
    print("=" * 100)

def build_fsk_pdf(doc, elements, pdf_path):
    try:
        create_fsk_pdf_document(doc, elements)
        report_fsk_pdf_created(pdf_path)

    except Exception as e:
        print("=" * 100)
        print("An error occurred during the PDF file creation process.")
        print("=" * 100)

        traceback.print_exc()

        print("Error Message :", e)
        print("=" * 100)

        raise

def get_jumlah_hari_fsk(periode_tanggal):
    try:
        _, _, total_hari = parse_periode_tanggal(periode_tanggal)
        return f" selama ({total_hari} Hari)" if total_hari > 0 else ""
    except Exception as e:
        print("Miscalculations : ", e)
        return ""

def _log_submission_error(connection):
    if connection is not None:
        connection.rollback()
    print("=" * 100)
    print("An error occurred during the data submission process.")
    traceback.print_exc()
    print("=" * 100)

def get_dropdown_master_data(cursor):
    result = _empty_dropdown_master_data()
    try:
        _populate_hdti_data(result, cursor)
        _populate_hdbs_data(result, cursor)
        
        result["data_project_manager"] = _fetch_dropdown_data(cursor, """
            SELECT 
					-- *,
                pegawai_id,
                pegawai_nama
            FROM pegawai
            WHERE pegawai_status = 1            
            AND pegawai_nik_lintas = 098209384092834 
				OR pegawai_nip = 2834
				-- AND pegawai_nama = 'MOHAMAD USMAN ANDI'
            ORDER BY pegawai_nama
        """)
        
        result["data_supervisor"] = _fetch_dropdown_data(cursor, """
            SELECT
                -- id AS Id,
                id,
				nama
            FROM pengajuan_nama_supervisor_form
            -- WHERE Id IN (2, 3)
            ORDER BY nama
        """)

        return result
    except Exception as e:
        _log_dropdown_master_data_error(e)
        return _empty_dropdown_master_data()
    finally:
        print("The process of retrieving the dropdown master data has been completed.")

def empty_logo(styles):
    return Paragraph("", styles["Normal"])

def _load_fsk_logo(project_root, styles):
    logo_candidates = [
        os.path.join(project_root, "images", filename)
        for filename in (
            "Logo_Bank_Indonesia.png",
            "logo_bank_indonesia.png",
            "logo_bi.png",
        )
    ]

    logo_path = next((path for path in logo_candidates if os.path.exists(path)), None)
    if not logo_path: return empty_logo(styles)

    try:
        with PILImage.open(logo_path) as image: width, height = image.size
        return ReportLabImage(
            logo_path,
            width=4 * cm,
            height=4 * cm * height / width
        )
    except Exception as error:
        print("Error loading image:", error)
        return empty_logo(styles)

def _build_fsk_checklist(selected_field, alasan, checklist_style, Table, TableStyle, Paragraph):
    descriptions = (
        ("izin", "1. Ijin tidak masuk dengan keterangan, Sakit : {}"),
        ("dinas", "2. Dinas luar / dalam kota karena kegiatan, {}"),
        ("tp", "3. Bekerja Tidak Penuh (TP) karena, {}"),
        ("terlambat", "4. Terlambat karena, {}"),
    )
    rows = [
        [
            "[ √ ]" if field == selected_field else "[   ]",
            Paragraph(description.format(alasan if field == selected_field else ""), checklist_style),
        ]
        for field, description in descriptions
    ]
    table = Table(rows, colWidths=[1.2 * cm, 13.8 * cm])
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0, colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return table

# ======================================================================================================
# CREATE PDF FSK : FORM SURAT KETIDAKHADIRAN (FSK PDF FILE)
# ======================================================================================================
def create_pdf_fsk(
    nik_lintasarta,
    nama,
    supervisor_nama,
    project_manager_nama,
    periode_tanggal,
    status_absensi,
    alasan,
    output_folder="."
):

    from xml.sax.saxutils import escape
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.enums import TA_CENTER, TA_RIGHT
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

    # Escape values rendered through Paragraph because ReportLab treats their
    # contents as a small HTML-like markup language.
    nama = escape(str(nama or "-"))
    supervisor_nama = escape(str(supervisor_nama or "-"))
    project_manager_nama = escape(str(project_manager_nama or "-"))
    alasan = escape(str(alasan or ""))
    status_absensi = str(status_absensi or "")
    nik_lintasarta = str(nik_lintasarta or "-")

    os.makedirs(output_folder, exist_ok=True)

    safe_nik_lintasarta = "".join(
        character
        for character in nik_lintasarta
        if character.isalnum() or character in ("-", "_")
    ) or "unknown"

    nomor_surat = f"FSK_{safe_nik_lintasarta}_{datetime.datetime.now():%d%m%Y_%H%M%S}"
    pdf_path = os.path.join(output_folder, f"{nomor_surat}.pdf")

    # ==================================================
    # CHECK VALUE √
    # ==================================================

    status = status_absensi.strip().lower()
    selected_field = {
        "sakit": "izin",
        "tidak hadir": "izin",
        "dinas": "dinas",
        "tidak penuh": "tp",
        "terlambat": "terlambat",
    }.get(status)

    # ==================================================
    # STYLE
    # ==================================================

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        rightMargin=30,
        leftMargin=30,
        topMargin=20,
        bottomMargin=20
    )

    styles = getSampleStyleSheet()
    normal_style = styles["Normal"]
    
    style_normal = ParagraphStyle(
        "fsk_normal", 
        parent=normal_style, 
        fontSize=10, 
        leading=18
    )
    
    checklist_style = ParagraphStyle(
        "fsk_checklist", 
        parent=normal_style, 
        fontSize=10, 
        leading=14, 
        wordWrap="LTR"
    )
    
    keterangan_style = ParagraphStyle(
        "keterangan_style",
        parent=styles["Normal"],
        fontSize=8,
        leading=9
    )
    
    ttd_label_style = ParagraphStyle(
        "fsk_ttd_label",
        parent=normal_style,
        alignment=TA_CENTER,
        fontSize=10,
        leading=12
    )
    
    ttd_nama_style = ParagraphStyle(
        "fsk_ttd_name",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontSize=10,
        leading=11
    )

    # ==================================================
    # TANGGAL SURAT
    # ==================================================
    now = datetime.datetime.now()
    tanggal_surat = (
        f"{now.day:02d} "
        f"{BULAN_INDONESIA[now.month]} "
        f"{now.year}"
    )

    # ==================================================
    # LOGO BI
    # ==================================================
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    logo_img = _load_fsk_logo(project_root, styles)

    # ==================================================
    # HEADER
    # ==================================================
    
    header_title_style = ParagraphStyle(
        "header_title_style",
        parent=styles["Normal"],
        alignment=TA_CENTER,
        fontName="Helvetica",
        fontSize=12,
        leading=14,
        spaceBefore=8
    )

    header_date_style = ParagraphStyle(
        "header_date_style",
        parent=styles["Normal"],
        alignment=TA_RIGHT,
        fontSize=10,
        leading=11
    )

    header_table = Table(
        [[
            logo_img,
            Paragraph("FORM KETERANGAN STATUS KEHADIRAN", header_title_style),
            Paragraph(tanggal_surat, header_date_style)
        ]],
        colWidths=[
            2.6 * cm,
            10.2 * cm,
            3.8 * cm
        ]
    )

    header_table.setStyle(
        TableStyle(
        [
            ('GRID', (0,0), (-1,-1), 0, colors.white),

            # RATA BAWAH SEMUA ISI ROW
            ('VALIGN', (0,0), (-1,-1), 'BOTTOM'),

            # alignment
            ('ALIGN', (0,0), (0,0), 'LEFT'),
            ('ALIGN', (1,0), (1,0), 'CENTER'),
            ('ALIGN', (2,0), (2,0), 'RIGHT'),

            # ============================
            # LOGO
            # ============================
            ('LEFTPADDING', (0,0), (0,0), -30),
            ('RIGHTPADDING', (0,0), (0,0), 0),

            # ============================
            # JUDUL
            # ============================
            ('LEFTPADDING', (1,0), (1,0), 35),
            ('RIGHTPADDING', (1,0), (1,0), 0),
            ('TOPPADDING', (1,0), (1,0), 56),

            # ============================
            # TANGGAL
            # ============================
            ('LEFTPADDING', (2,0), (2,0), 0),
            ('RIGHTPADDING', (2,0), (2,0), -25),
            ('TOPPADDING', (2,0), (2,0), 48),

            # ============================
            # GLOBAL
            # ============================
            ('BOTTOMPADDING', (0,0), (-1,-1), 0)
        ])
    )

    elements = [
        header_table,
        Spacer(1, 15),
        Spacer(1, 10),
        Spacer(1, 30),
        Spacer(1, 23)
    ]

    # ==================================================
    # ISI
    # ==================================================

    periode_fsk = format_periode_fsk(periode_tanggal)
    jumlah_hari = get_jumlah_hari_fsk(periode_tanggal)

    elements.extend([
        Paragraph("Yang bertandatangan di bawah ini :", style_normal),
        Spacer(1, 10)
    ])

    # ==================================================
    # IDENTITAS
    # ==================================================

    identitas_table = Table(
        [
            ["", "Nama", ":", nama],
            ["", "NIK Lintasarta", ":", escape(nik_lintasarta)]
        ],
        colWidths=[
            1.8 * cm,
            3.0 * cm,
            0.5 * cm,
            7.5 * cm
        ]
    )

    identitas_table.setStyle(
        TableStyle([
            ('GRID', (0,0), (-1,-1), 0, colors.white),
            ('RIGHTPADDING', (1,0), (1,-1), 8),
            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 3),

        ])
    )

    identitas_table.hAlign = 'LEFT'

    elements.extend([identitas_table, Spacer(1, 8)])

    # ==========================================
    # KETERANGAN
    # ==========================================

    elements.extend([
        Paragraph(
            (
                f"dengan ini memberitahukan bahwa pada tanggal "
                f"{periode_fsk}{jumlah_hari}, "
                f"saya tidak mencatatkan kehadiran, "
                f"karena (beri tanda √ pada alasan yang sesuai) :"
            ),
            style_normal
        ),
        Spacer(1, 10)
    ])

    # ==========================================
    # CHECKLIST
    # ==========================================

    checklist_table = _build_fsk_checklist(
        selected_field, alasan, checklist_style, Table, TableStyle, Paragraph
    )

    elements.extend([checklist_table, Spacer(1, 8)])

    # ==========================================
    # PENUTUP
    # ==========================================

    elements.extend([
        Paragraph(
            "Demikian keterangan ini saya buat, untuk dapat dipergunakan sebagaimana mestinya.",
            style_normal
        ),
        Spacer(1, 65)
    ])

    # ==========================================
    # TTD
    # ==========================================

    ttd_labels = (
        "Menyetujui,<br/>Project Manager",
        "Mengetahui,<br/>Pimpinan",
        "Hormat saya,",
    )
    ttd_names = (project_manager_nama, supervisor_nama, nama)
    ttd_table = Table(
        [
            [Paragraph(label, ttd_label_style) for label in ttd_labels],
            [
                Paragraph(f"<u>({person or '-'})</u>", ttd_nama_style)
                for person in ttd_names
            ],
        ],
        colWidths=[
            5.8 * cm,
            5.0 * cm,
            6.5 * cm
        ],
        rowHeights=[
            3.3 * cm,
            0.5 * cm
        ]
    )
    ttd_table.setStyle(
        TableStyle([

            ('GRID', (0,0), (-1,-1), 0, colors.white),

            ('ALIGN', (0,0), (-1,-1), 'CENTER'),
            ('VALIGN', (0,0), (-1,-1), 'TOP'),

            ('LEFTPADDING', (0,0), (-1,-1), 0),
            ('RIGHTPADDING', (0,0), (-1,-1), 0),

            ('TOPPADDING', (0,0), (-1,-1), 0),
            ('BOTTOMPADDING', (0,0), (-1,-1), 0),

        ])
    )

    elements.extend([ttd_table, Spacer(1, 65)])
    
    # ==========================================
    # KETERANGAN BAWAH
    # ==========================================

    keterangan_list = [
        "Keterangan :",
        "1). Checklist di'isi otomatis oleh sistem (sesuai pilihan jenis pengajuan).",
        "2). Alasan ditulis pada saat melengkapi form pengajuan."
    ]

    elements.extend(
        [Paragraph(item, keterangan_style) for item in keterangan_list]
    )

    elements.extend([
        Spacer(1, 20),
        Paragraph(
            nomor_surat,
            ParagraphStyle(
                "footer",
                parent=styles["Normal"],
                fontSize=8,
                leading=9
            )
        )
    ])

    build_fsk_pdf(doc, elements, pdf_path)
    return pdf_path

# ======================================================================================================
# KIRIM EMAIL
# ======================================================================================================
def attach_inline_footer(msg, footer_path):

    if not os.path.isfile(footer_path):
        print(f"The footer image was not found at this location. : {footer_path}")
        return

    with open(footer_path, "rb") as img:
        footer_img = MIMEImage(img.read(), _subtype="png")

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
    
    msg.attach(footer_img)

    print(f"The footer image is located here. : {footer_path}")

def kirim_email_pengajuan(
    recipient_email,
    nama_penerima,
    nik_lintasarta,
    pdf_path
):

    sender_email = os.getenv("SMTP_EMAIL")
    sender_password = os.getenv("SMTP_PASSWORD")

    recipient_email = (recipient_email or "").strip()
    nama_penerima = nama_penerima or "User"
    nik_lintasarta = nik_lintasarta or "-"

    print("=" * 100)
    print("SMTP_EMAIL :", sender_email)
    print("SMTP_PASSWORD :", "***" if sender_password else "NOT SET")
    print("=" * 100)

    # ==================================================
    # VALIDATION
    # ==================================================

    if not sender_email:
        raise ValueError("SMTP email has not been configured.")

    if not sender_password:
        raise ValueError("SMTP_PASSWORD has not been set.")

    if not recipient_email:
        raise ValueError("The recipient's email is empty.")

    if "@" not in recipient_email:
        raise ValueError(f"Invalid email : {recipient_email} ")

    if not pdf_path:
        raise ValueError("Empty PDF path (location)")

    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"The PDF file was not found at this location : {pdf_path}")

    # ==================================================
    # FOOTER IMAGE
    # ==================================================

    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    footer_path = os.path.join(project_root, "images", "Footer_Email_Lintasarta.png")

    print("=" * 100)
    print("FOOTER EXISTS :", os.path.exists(footer_path))
    print("FOOTER PATH   :", footer_path)
    print("=" * 100)

    # ==================================================
    # SUBJECT
    # ==================================================
    subject = (
        f"[RPA] Form Pegajuan Ketidakhadiran "
        f"{nik_lintasarta} - "
        f"{datetime.datetime.now().strftime('%d/%m/%Y %H:%M:%S')}"
    )

    # ==================================================
    # CC & BCC
    # ==================================================
    cc_emails = ["demo@example.com"]
    bcc_emails = ["demo@example.com"]
    
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
    # FOOTER IMAGE INLINE
    # ==================================================
    attach_inline_footer(msg, footer_path)

    # ==================================================
    # PDF ATTACHMENT
    # ==================================================
    with open(pdf_path, "rb") as pdf_file:
    
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
            msg.attach(attachment)
            print(f"PDF file found, location : {pdf_path}")
    
    # ==================================================
    # SMTP
    # ==================================================
    try:

        print_separator()
        print("CONNECTING TO SMTP SERVER")
        print_separator()

        with smtplib.SMTP("smtp.mail.yahoo.com", 587, timeout=30) as server:

            server.ehlo()
            print("EH... LO, OK..!")

            server.starttls()
            server.ehlo()
            print("TLS OK")
            
            server.login(sender_email, sender_password)
            print("Login OK")
            
            all_recipients = ([recipient_email] + cc_emails + bcc_emails)
            server.sendmail(sender_email,all_recipients, msg.as_string())

            print("=" * 100)
            print("Email successfully sent")
            print("TO  :", recipient_email)
            print("CC  :", cc_emails)
            print("BCC :", bcc_emails)
            print("=" * 100)

    except Exception as e:

        print("=" * 100)
        print("An error occurred during the email sending process.")
        print("=" * 100)

        traceback.print_exc()

        print("Error Type :", type(e).__name__)
        print("Error Message :", e)
        print("=" * 100)

        raise

# ======================================================================================================
# GET NAMA PERSONIL
# ======================================================================================================
def get_nama_personil(cursor, personil_id):
    
    try:
        cursor.execute("""
            SELECT
                pegawai_nama
            FROM pegawai
            WHERE pegawai_id = %s
        """, (personil_id,))

        data = cursor.fetchone()

        return (data["pegawai_nama"]
            if data
            else "-"
        )
        
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the person lookup process.")
        print(e)
        print("=" * 100)
        return "-"
    
    finally:
        pass

# ======================================================================================================
# GET NAMA SUPERVISOR
# ======================================================================================================
def get_nama_supervisor(cursor, supervisor_id):
    
    try:
        
        cursor.execute("""
            SELECT
                nama
            FROM pengajuan_nama_supervisor_form
            WHERE id = %s
        """, (supervisor_id,))

        data = cursor.fetchone()

        return (data["nama"]
            if data
            else "-"
        )
    except Exception as e:

        print("=" * 100)
        print("An error occurred during the supervisor lookup process.")
        print(e)
        print("=" * 100)

        return "-"
    finally:
        pass

# ======================================================================================================
# GET NAMA PROJECT MANAGER
# ======================================================================================================
def get_nama_project_manager(cursor, pm_id):
    try:
        cursor.execute("""
            SELECT
                pegawai_nama
            FROM pegawai
            WHERE pegawai_id = %s
        """, (pm_id,))

        data = cursor.fetchone()

        return (data["pegawai_nama"]
            if data
            else "-"
        )
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the project manager lookup process.")
        print(e)
        print("=" * 100)

        return "-"
    finally:
        pass
    
# ======================================================================================================
# SIMPAN PENGAJUAN
# ======================================================================================================
def simpan_pengajuan(
    cursor,
    tim_personil,
    nik_lintasarta,
    email,
    tim_id,
    personil_id,
    status_absensi,
    project_manager,
    tanggal_mulai,
    tanggal_akhir,
    jumlah_hari,
    supervisor,
    keterangan
):

    try:
        query = """
        INSERT INTO pengajuan_ketidakhadiran
        (
            Tim_Personil,
            Nik_Lintasarta,
            Alamat_Email,
            Tim_Id,
            Personil_Id,
            Status_Absensi,
            Project_Manager,
            Tanggal_Mulai,
            Tanggal_Akhir,
            Jumlah_Hari,
            PimpinanOrSupervisor_Id,
            Keterangan,
            Status_Pengajuan,
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
            1,
            0,
            NOW()
        )
        """

        cursor.execute(
            query,
            (
                tim_personil,
                nik_lintasarta,
                email,
                tim_id,
                personil_id,
                status_absensi,
                project_manager,
                tanggal_mulai,
                tanggal_akhir,
                jumlah_hari,
                supervisor,
                keterangan,
                "Tidak Hadir"
            )
        )
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the data insertion process.")
        print(e)
        print("=" * 100)
        raise
    finally:
        pass

# ======================================================================================================
# AMBIL TANGGAL MERAH
# ======================================================================================================
def ambil_tanggal_merah():
    tanggal_merah = []
    try:
        tahun = datetime.datetime.now().year
        response = requests.get(
            f"https://api-hari-libur.vercel.app/api?year={tahun}",
            timeout=10
        )

        if response.status_code == 200:
            result = response.json()
            tanggal_merah = [item["date"] for item in result.get("data", [])]

        print(f"The national holiday dates have been successfully retrieved. : {len(tanggal_merah)}")
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the national holiday retrieval process.")
        print(e)
        print("=" * 100)
    return tanggal_merah

# ======================================================================================================
# GET DROPDOWN MASTER DATA
# ======================================================================================================
def _fetch_dropdown_data(cursor, query):
    
    try:
        cursor.execute(query)
        return cursor.fetchall()
    except Exception as e:
        print("=" * 100)
        print("An error occurred during the dropdown data retrieval process.")
        print("Error Message :", e)
        print("=" * 100)
        return []

def _log_dropdown_master_data_error(error):
    print("=" * 100)
    print("An error occurred during the master data retrieval process.")
    print("Error Message :", error)
    print("=" * 100)

def _empty_dropdown_master_data():
    return {
        "data_tim_hdti": [],
        "data_personil_hdti": [],
        "data_tim_hdbs": [],
        "data_personil_hdbs": [],
        "data_project_manager": [],
        "data_supervisor": []
    } 

# ==================================================
# HDTI 
# ==================================================
def _fetch_tim_hdti_data(cursor):
    return _fetch_dropdown_data(cursor, """
        SELECT
            pembagian3_id,
            pembagian3_nama
        FROM pembagian3
        WHERE LOWER(pembagian3_ket)
            LIKE '%hdti%'
        ORDER BY pembagian3_nama
    """)

def _populate_tim_hdti_data(result, cursor):
    result["data_tim_hdti"] = _fetch_tim_hdti_data(cursor)

def _populate_hdti_data(result, cursor):
    _populate_tim_hdti_data(result, cursor)
    result["data_personil_hdti"] = _fetch_dropdown_data(cursor, """
        SELECT
            p.pegawai_id,
            p.pegawai_nama,
            p.pegawai_nik_lintas
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

# ==================================================
# HDBS 
# ==================================================
def _fetch_tim_hdbs_data(cursor):
    return _fetch_dropdown_data(cursor, """
        SELECT
            pembagian3_id,
            pembagian3_nama
        FROM pembagian3
        WHERE LOWER(pembagian3_ket)
            LIKE '%hdbs%'
        ORDER BY pembagian3_nama
    """)
    
def _populate_hdbs_data(result, cursor):
    result["data_tim_hdbs"] = _fetch_tim_hdbs_data(cursor)

    result["data_personil_hdbs"] = _fetch_dropdown_data(cursor, """
        SELECT
            p.pegawai_id,
            p.pegawai_nama,
            p.pegawai_nik_lintas
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

# ======================================================================================================
# FORM PENGAJUAN
# ======================================================================================================
def _get_form_pengajuan_values(suffix):
    """Read the team-dependent and shared submission fields from the request."""
    return {
        field: request.form[field] if field in request.form else None
        for field in ('status_absensi', 'project_manager', 'periode_tanggal','supervisor', 'keterangan')
    } | {
        field: request.form[f'{field}_{suffix}']
        if f'{field}_{suffix}' in request.form
        else None
        for field in ('tim', 'personil', 'nik_lintasarta', 'email')
    }

def _validate_form_pengajuan(values):
    """Validate submission fields and return the parsed date range."""
    for value, message in (
        (values['email'], "Email wajib diisi"),
        (values['personil'], "Personil wajib dipilih"),
    ):
        if not value:
            raise ValueError(message)

    tanggal_mulai, tanggal_akhir, jumlah_hari = parse_periode_tanggal(
        values['periode_tanggal']
    )
    if not tanggal_mulai or jumlah_hari <= 0:
        raise ValueError("Periode tanggal atau jumlah hari tidak valid")
    return tanggal_mulai, tanggal_akhir, jumlah_hari

def _process_form_pengajuan_request():

    # ==========================================
    # POST
    # ==========================================

    if request.method == 'POST':

        connection = None
        cursor = None

        try:

            connection = utils.db.get_db_connection()
            cursor = connection.cursor(dictionary=True)

            tim_personil = (
                request.form['tim_personil']
                if 'tim_personil' in request.form
                else None
            )
            form_suffix = {'HDTI': 'hdti', 'HDBS': 'hdbs'}.get(tim_personil or '')
            if form_suffix is None:
                raise ValueError(f"Invalid team personnel: {tim_personil}")

            values = _get_form_pengajuan_values(form_suffix)
            # ==========================================
            # VALIDASI
            # ==========================================
            tanggal_mulai, tanggal_akhir, jumlah_hari = _validate_form_pengajuan(values)

            # ==========================================
            # MASTER DATA
            # ==========================================
            nama_personil = get_nama_personil(cursor, values['personil'])

            if nama_personil == "-":
                raise ValueError("Personnel data not found.")

            nama_supervisor = get_nama_supervisor(cursor, values['supervisor'])
            nama_project_manager = get_nama_project_manager(cursor, values['project_manager'])

            # ==========================================
            # SIMPAN PENGAJUAN
            # ==========================================
            simpan_pengajuan(
                cursor,
                tim_personil,
                values['nik_lintasarta'],
                values['email'],
                values['tim'],
                values['personil'],
                values['status_absensi'],
                values['project_manager'],
                tanggal_mulai,
                tanggal_akhir,
                jumlah_hari,
                values['supervisor'],
                values['keterangan']
            )
            
            connection.commit()
            
            print("Successfully saved submission data.")

            # ==========================================
            # GENERATE PDF
            # ==========================================
            pdf_path = create_pdf_fsk(
                nik_lintasarta=values['nik_lintasarta'],
                nama=nama_personil,
                supervisor_nama=nama_supervisor,
                project_manager_nama=nama_project_manager,
                periode_tanggal=values['periode_tanggal'],
                status_absensi=values['status_absensi'],
                alasan=values['keterangan'],
                output_folder=os.path.join(
                    os.path.dirname(
                        os.path.abspath(__file__)
                    ),
                    "generated_pdf"
                )
            )

            print(f"The PDF file was successfully created, location file : {pdf_path}")

            # ==========================================
            # KIRIM EMAIL
            # ==========================================
            try:
                kirim_email_pengajuan(
                    recipient_email=values['email'],
                    nama_penerima=nama_personil,
                    nik_lintasarta=values['nik_lintasarta'],
                    pdf_path=pdf_path
                )
            except Exception as email_error:
                report_email_send_failure(email_error)
            return redirect(url_for('form_pengajuan_tidakhadir.form_pengajuan', status='success'))

        except Exception:
            _log_submission_error(connection)
            raise
        finally:
            close_db_resources(cursor, connection)

    # ==========================================
    # GET
    # ==========================================

    connection = None
    cursor = None
    master_data = {
        "data_tim_hdti": [],
        "data_personil_hdti": [],
        "data_tim_hdbs": [],
        "data_personil_hdbs": [],
        "data_project_manager": [],
        "data_supervisor": [],
    }
    tanggal_merah = []

    try:
        connection = utils.db.get_db_connection()
        cursor = connection.cursor(dictionary=True)

        tanggal_merah = ambil_tanggal_merah()
        master_data = get_dropdown_master_data(cursor)
    except Exception as e:
        _log_dropdown_master_data_error(e)
    finally:
        close_db_resources(cursor, connection)

        print("=" * 100)
        print("DATA TANGGAL MERAH")
        print(tanggal_merah)
        print("=" * 100)
    return render_template(
        'form-pengajuan-tidak-hadir.html',
        **master_data,
        tanggal_merah=tanggal_merah
    )

@form_pengajuan_tidak_hadir.route('/form-pengajuan-tidak-hadir', methods=['GET', 'POST'])
def form_pengajuan():
    """Render or process the absence-submission form."""
    return _process_form_pengajuan_request()