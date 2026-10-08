from flask import Blueprint, render_template, request, redirect, url_for, session
from utils.db import get_db_connection
import pandas as pd
from datetime import datetime
import calendar
import re

jadwal_bulanan_bp = Blueprint('jadwal_bulanan', __name__)

@jadwal_bulanan_bp.route('/jadwal-bulanan', methods=['GET'])
def jadwal_bulanan():
    if 'loggedin' not in session:
        return redirect(url_for('auth.auth_login'))

    # Get month and year from request, default to current month/year
    today = datetime.today()
    
    # Check if form was submitted (has arguments)
    is_submitted = len(request.args) > 0
    
    selected_month = int(request.args.get('month', today.month))
    selected_year = int(request.args.get('year', today.year))
    selected_divisi = request.args.get('divisi', '')

    # Calculate number of days in the selected month
    _, num_days = calendar.monthrange(selected_year, selected_month)
    
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    
    # Fetch all divisions for the dropdown
    cursor.execute("SELECT pembagian1_id, pembagian1_nama FROM pembagian1 ORDER BY pembagian1_nama ASC")
    divisi_list = cursor.fetchall()
    
    table_html = ""
    results = []
    
    if is_submitted:
        # Query database only if submitted
        query = """
            SELECT 
                p.pegawai_nip, 
                p.pegawai_nama, 
                sr.tgl_shift, 
                jk.jk_name
            FROM pegawai p
            JOIN shift_result_new sr ON p.pegawai_id = sr.pegawai_id
            JOIN jam_kerja jk ON sr.jk_id = jk.jk_id
            WHERE MONTH(sr.tgl_shift) = %s AND YEAR(sr.tgl_shift) = %s
        """
        params = [selected_month, selected_year]
        
        if selected_divisi:
            query += " AND p.pembagian1_id = %s"
            params.append(selected_divisi)
            
        query += " ORDER BY p.pegawai_nama ASC, sr.tgl_shift ASC"
        
        cursor.execute(query, tuple(params))
        results = cursor.fetchall()
    
    cursor.close()
    conn.close()

    # Process names (remove time if any)
    def clean_shift_name(name):
        if not name:
            return ""
        clean_name = name.split('(')[0].strip()
        clean_name = re.sub(r'\d{2}:\d{2}.*', '', clean_name).replace('-', '').strip()
        return clean_name
        
    for row in results:
        row['jk_name'] = clean_shift_name(row['jk_name'])
        row['tgl_shift'] = pd.to_datetime(row['tgl_shift']).day
        
    df = pd.DataFrame(results)
    
    if not df.empty:
        # Pivot the dataframe
        pivot_df = df.pivot_table(
            index=['pegawai_nip', 'pegawai_nama'], 
            columns='tgl_shift', 
            values='jk_name', 
            aggfunc='first'
        ).reset_index()
        
        # Remove the extra 'tgl_shift' header name
        pivot_df.columns.name = None
        
        # Ensure all days 1 to num_days are present in columns
        for day in range(1, num_days + 1):
            if day not in pivot_df.columns:
                pivot_df[day] = ""
                
        # Reorder columns: nip, nama, 1, 2, 3 ... num_days
        cols = ['pegawai_nip', 'pegawai_nama'] + list(range(1, num_days + 1))
        pivot_df = pivot_df[cols]
        
        # Fill NaN with empty string
        pivot_df = pivot_df.fillna("")
        
        # Sort by nama A-Z
        pivot_df = pivot_df.sort_values('pegawai_nama').reset_index(drop=True)
        
        # Rename NIP and Nama for the HTML table header
        pivot_df.rename(columns={
            'pegawai_nip': 'NIP',
            'pegawai_nama': 'NAMA'
        }, inplace=True)
        
        # Convert to HTML
        table_html = pivot_df.to_html(
            index=False,
            classes="min-w-max w-full table-auto border-collapse text-sm text-left",
            border=0
        )
    
    return render_template('jadwal-bulanan.html', 
                           table_html=table_html, 
                           selected_month=selected_month, 
                           selected_year=selected_year,
                           selected_divisi=selected_divisi,
                           divisi_list=divisi_list,
                           is_submitted=is_submitted,
                           num_days=num_days)

@jadwal_bulanan_bp.route('/api/reset-jadwal', methods=['POST'])
def reset_jadwal():
    if 'loggedin' not in session or session.get('role') != 'admin':
        return {"status": "error", "message": "Unauthorized"}, 401

    data = request.json.get('data', [])
    if not data:
        return {"status": "error", "message": "Tidak ada data yang dikirim"}, 400

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        updated_count = 0
        for item in data:
            nip = item.get('nip')
            date_str = item.get('date')
            
            if not nip or not date_str:
                continue

            query = """
                UPDATE shift_result_new 
                SET jk_id = 0 
                WHERE tgl_shift = %s 
                AND pegawai_id = (SELECT pegawai_id FROM pegawai WHERE pegawai_nip = %s)
            """
            cursor.execute(query, (date_str, nip))
            updated_count += cursor.rowcount

        conn.commit()
        return {"status": "success", "message": f"{len(data)} jadwal berhasil di-reset menjadi kosong."}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Terjadi kesalahan: {str(e)}"}, 500
    finally:
        cursor.close()
        conn.close()

