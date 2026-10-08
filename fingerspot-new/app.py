import os
from flask import Flask, send_from_directory
from datetime import timedelta
from contextlib import suppress
import locale

# 1. Imports
from routes.auth_routes import auth_bp
from routes.main_routes import main_bp
from routes.upload_jadwal_shift import upload_jadwal_shift_bp
from routes.upload_mesin_lama import upload_mesin_lama_bp
from routes.data_absen_fingerspot import data_absen_fingerspot_bp
from routes.dcap import dcap_bp
from routes.pegawai import pegawai_bp
from routes.dashboard_pegawai import dashboard_pegawai_bp

from routes.view_data_pengajuan_tidak_hadir import view_data_pengajuan_tidak_hadir_bp
from routes.view_data_pengajuan_cuti import view_data_pengajuan_cuti_bp
from routes.view_kalender_ketersediaan_cuti import view_kalender_ketersediaan_cuti_bp
from routes.form_pengajuan_tidak_hadir import form_pengajuan_tidak_hadir
from routes.form_pengajuan_cuti import form_pengajuan_cuti

from routes.ito import ito_bp
from routes.jadwal_bulanan import jadwal_bulanan_bp
from routes.jam_kerja_khusus import jam_kerja_khusus_bp
from routes.aturan_puasa import aturan_puasa_bp
from routes.pengaturan_shift import pengaturan_shift_bp
from routes.pengaturan_organisasi import pengaturan_organisasi_bp
from routes.admin_tukar_shift import admin_tukar_shift_bp

# 2. Configuration & Initialization
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY') or os.urandom(32)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=12)
app.config['UPLOAD_FOLDER'] = 'uploads'

with suppress(locale.Error):
    locale.setlocale(locale.LC_TIME, 'indonesian_indonesia')

# 3. Register Blueprints
app.register_blueprint(auth_bp)
app.register_blueprint(main_bp)
app.register_blueprint(upload_jadwal_shift_bp)
app.register_blueprint(upload_mesin_lama_bp)
app.register_blueprint(data_absen_fingerspot_bp)
app.register_blueprint(dcap_bp)
app.register_blueprint(pegawai_bp)
app.register_blueprint(dashboard_pegawai_bp)
app.register_blueprint(view_data_pengajuan_tidak_hadir_bp)
app.register_blueprint(view_data_pengajuan_cuti_bp)
app.register_blueprint(view_kalender_ketersediaan_cuti_bp)
app.register_blueprint(form_pengajuan_tidak_hadir)
app.register_blueprint(form_pengajuan_cuti)

app.register_blueprint(ito_bp)
app.register_blueprint(jadwal_bulanan_bp)
app.register_blueprint(jam_kerja_khusus_bp)
app.register_blueprint(aturan_puasa_bp)
app.register_blueprint(pengaturan_shift_bp)
app.register_blueprint(pengaturan_organisasi_bp)
app.register_blueprint(admin_tukar_shift_bp)

# 4. Global Routes
@app.route('/images/<path:filename>')
def serve_image(filename):
    return send_from_directory('images', filename)


# print("\n" + "=" * 100)
# print("DAFTAR ROUTE")
# print("=" * 100)
# for rule in app.url_map.iter_rules():
#     print(rule, "=>", rule.endpoint)
# print("=" * 100)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
