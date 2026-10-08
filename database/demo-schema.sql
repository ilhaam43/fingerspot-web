-- Local demo schema inferred from the root Flask app, not a production migration.
CREATE TABLE IF NOT EXISTS login_web (
  id_akun INT PRIMARY KEY AUTO_INCREMENT,
  username VARCHAR(100) NOT NULL UNIQUE,
  password VARCHAR(255) NOT NULL
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS pembagian1 (
  pembagian1_id INT PRIMARY KEY AUTO_INCREMENT,
  pembagian1_nama VARCHAR(100) NOT NULL UNIQUE,
  pembagian1_ket VARCHAR(255) DEFAULT ''
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pembagian2 (
  pembagian2_id INT PRIMARY KEY AUTO_INCREMENT,
  pembagian2_nama VARCHAR(100) NOT NULL UNIQUE,
  pembagian2_ket VARCHAR(255) DEFAULT ''
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pembagian3 (
  pembagian3_id INT PRIMARY KEY AUTO_INCREMENT,
  pembagian3_nama VARCHAR(100) NOT NULL UNIQUE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pegawai (
  pegawai_id INT PRIMARY KEY AUTO_INCREMENT,
  pegawai_pin VARCHAR(30) NOT NULL UNIQUE,
  pegawai_nip VARCHAR(30) NOT NULL UNIQUE,
  pegawai_nama VARCHAR(150) NOT NULL,
  pegawai_alias VARCHAR(100) DEFAULT '',
  pegawai_nik_lintas VARCHAR(30) DEFAULT '',
  pegawai_pwd VARCHAR(255) NOT NULL,
  pegawai_status INT NOT NULL DEFAULT 1,
  pembagian1_id INT,
  pembagian2_id INT,
  pembagian3_id INT,
  sisa_cuti DECIMAL(6,2) NOT NULL DEFAULT 12,
  FOREIGN KEY (pembagian1_id) REFERENCES pembagian1(pembagian1_id),
  FOREIGN KEY (pembagian2_id) REFERENCES pembagian2(pembagian2_id),
  FOREIGN KEY (pembagian3_id) REFERENCES pembagian3(pembagian3_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS jam_kerja (
  jk_id INT PRIMARY KEY AUTO_INCREMENT,
  jk_name VARCHAR(100) NOT NULL,
  jk_bcin TIME,
  jk_ecout TIME
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS jam_kerja_khusus (
  id INT PRIMARY KEY AUTO_INCREMENT,
  tanggal DATE NOT NULL,
  jk_id INT NOT NULL,
  jam_masuk_khusus TIME,
  jam_pulang_khusus TIME,
  UNIQUE KEY special_date_shift (tanggal, jk_id),
  FOREIGN KEY (jk_id) REFERENCES jam_kerja(jk_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS shift_result_new (
  id INT PRIMARY KEY AUTO_INCREMENT,
  pegawai_id INT NOT NULL,
  tgl_shift DATE NOT NULL,
  jk_id INT,
  scan_in DATETIME,
  scan_out DATETIME,
  notes TEXT,
  khusus_lembur INT NOT NULL DEFAULT 0,
  khusus_extra INT NOT NULL DEFAULT 0,
  temp_id_auto INT NOT NULL DEFAULT 0,
  jdw_kerja_m_id INT NOT NULL DEFAULT 0,
  UNIQUE KEY employee_date (pegawai_id, tgl_shift),
  FOREIGN KEY (pegawai_id) REFERENCES pegawai(pegawai_id),
  FOREIGN KEY (jk_id) REFERENCES jam_kerja(jk_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS att_log (
  id BIGINT PRIMARY KEY AUTO_INCREMENT,
  sn VARCHAR(100) NOT NULL,
  scan_date DATETIME NOT NULL,
  pin VARCHAR(30) NOT NULL,
  verifymode INT NOT NULL DEFAULT 1,
  inoutmode INT NOT NULL DEFAULT 0,
  reserved INT NOT NULL DEFAULT 0,
  work_code INT NOT NULL DEFAULT 0,
  att_id INT NOT NULL DEFAULT 0,
  UNIQUE KEY device_scan (sn, scan_date, pin),
  INDEX scan_time (scan_date)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS tim (
  id INT PRIMARY KEY AUTO_INCREMENT,
  pembagian1_nama VARCHAR(100) NOT NULL,
  tim_nama VARCHAR(100) NOT NULL,
  UNIQUE KEY division_team (pembagian1_nama, tim_nama)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ref_nama_lembur (
  id INT PRIMARY KEY AUTO_INCREMENT,
  nama VARCHAR(150) NOT NULL UNIQUE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS ref_lokasi_kerja (
  id INT PRIMARY KEY AUTO_INCREMENT,
  lokasi VARCHAR(150) NOT NULL UNIQUE
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS cuti_libur_nasional (
  id INT PRIMARY KEY AUTO_INCREMENT,
  tanggal DATE NOT NULL UNIQUE,
  keterangan VARCHAR(255) NOT NULL,
  jenis VARCHAR(50) NOT NULL DEFAULT 'Libur Nasional'
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS pengajuan_lembur (
  id_lembur INT PRIMARY KEY AUTO_INCREMENT,
  pegawai_id INT NOT NULL,
  pegawai_nip VARCHAR(30),
  penerima_tugas VARCHAR(150),
  nomor_surat VARCHAR(150) NOT NULL UNIQUE,
  tanggal_lembur DATE NOT NULL,
  lokasi_kerja VARCHAR(150),
  jam_lembur_awal TIME,
  jam_lembur_akhir TIME,
  keterangan_lembur TEXT,
  nama_mengetahui VARCHAR(150),
  nama_menyetujui VARCHAR(150),
  pemberi_tugas VARCHAR(150),
  status VARCHAR(30) NOT NULL DEFAULT 'Menunggu',
  tanggal_pengajuan DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (pegawai_id) REFERENCES pegawai(pegawai_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pengajuan_cuti (
  id_cuti INT PRIMARY KEY AUTO_INCREMENT,
  pegawai_id INT NOT NULL,
  pegawai_nip VARCHAR(30),
  nomor_surat VARCHAR(150) NOT NULL UNIQUE,
  kategori_cuti VARCHAR(30) NOT NULL DEFAULT 'CT',
  tim VARCHAR(100),
  personil_backup VARCHAR(150),
  alasan TEXT,
  nama_mengetahui VARCHAR(150),
  nama_menyetujui VARCHAR(150),
  tanggal_cuti_mulai DATE NOT NULL,
  tanggal_cuti_akhir DATE NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'Menunggu',
  tanggal_pengajuan DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (pegawai_id) REFERENCES pegawai(pegawai_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS status_kehadiran (
  id_status_kehadiran INT PRIMARY KEY AUTO_INCREMENT,
  pegawai_id INT NOT NULL,
  pegawai_nip VARCHAR(30),
  nomor_surat VARCHAR(150) NOT NULL UNIQUE,
  nomor_id_bi VARCHAR(50),
  kategori_alasan VARCHAR(50),
  tim VARCHAR(100),
  alasan_detail TEXT,
  nama_mengetahui VARCHAR(150),
  nama_menyetujui VARCHAR(150),
  tanggal_ketidakhadiran DATE NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'Menunggu',
  tanggal_pengajuan DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (pegawai_id) REFERENCES pegawai(pegawai_id)
) ENGINE=InnoDB;
CREATE TABLE IF NOT EXISTS pengajuan_tukar_shift (
  id_tukar_shift INT PRIMARY KEY AUTO_INCREMENT,
  pegawai_id INT NOT NULL,
  pegawai_nip VARCHAR(30),
  pegawai_nik_lintas VARCHAR(30),
  nomor_surat VARCHAR(150) NOT NULL UNIQUE,
  tim VARCHAR(100),
  tanggal_mulai DATE NOT NULL,
  tanggal_akhir DATE NOT NULL,
  shift_awal_id INT,
  shift_awal_nama VARCHAR(100),
  shift_baru_id INT,
  shift_baru_nama VARCHAR(100),
  pegawai_tukar_id INT,
  pegawai_tukar_nip VARCHAR(30),
  pegawai_tukar_nama VARCHAR(150),
  alasan_tukar_shift TEXT,
  nama_mengetahui VARCHAR(150),
  nama_menyetujui VARCHAR(150),
  status VARCHAR(30) NOT NULL DEFAULT 'Menunggu',
  tanggal_pengajuan DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (pegawai_id) REFERENCES pegawai(pegawai_id),
  FOREIGN KEY (pegawai_tukar_id) REFERENCES pegawai(pegawai_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS absensi (
  id INT PRIMARY KEY AUTO_INCREMENT,
  nama_pegawai VARCHAR(150),
  masuk TIME,
  pulang TIME,
  masuk_real TIME,
  pulang_real TIME,
  tl VARCHAR(50),
  tp VARCHAR(50),
  jumlah_jam_kerja VARCHAR(50),
  keterangan VARCHAR(100),
  tanggal_absen DATE,
  UNIQUE KEY name_date (nama_pegawai, tanggal_absen)
) ENGINE=InnoDB;
