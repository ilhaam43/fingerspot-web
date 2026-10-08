import os

try:
    import mysql.connector
    Error: type[Exception] = mysql.connector.Error
except ImportError:
    mysql = None

    Error = Exception

# ==============================================================================
# KONFIGURASI DATABASE
# ==============================================================================
DB_CONFIG = {
    'user': os.environ.get('DB_USER', 'fingerspot'),
    'password': os.environ.get('DB_PASSWORD', ''),
    'host': os.environ.get('DB_HOST', 'localhost'),
    'database': os.environ.get('DB_NAME', 'fin_pro'),
    'port': int(os.environ.get('DB_PORT', '3306')),
    'charset': os.environ.get('DB_CHARSET', 'utf8')
}


# ==============================================================================
# GET CONNECTION
# ==============================================================================
def get_db_connection():

    if mysql is None:
        raise Error(
            "mysql-connector-python is not installed"
        )

    return mysql.connector.connect(
        **DB_CONFIG
    )
