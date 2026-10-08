FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=id_ID.UTF-8 \
    LC_ALL=id_ID.UTF-8 \
    TZ=Asia/Jakarta \
    QT_QPA_PLATFORM=offscreen

RUN apt-get update && apt-get install -y --no-install-recommends \
    locales tzdata libreoffice-writer wkhtmltopdf fonts-dejavu-core \
    && sed -i 's/^# id_ID.UTF-8 UTF-8/id_ID.UTF-8 UTF-8/' /etc/locale.gen \
    && locale-gen \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY app.py ./
COPY templates ./templates
COPY static ./static
COPY files ./files
COPY config/shift_mappings.json config/mapping_nip_dcap_to_real_nip.json config/mapping_nip_old_absen_to_real_nip.json ./
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p uploads reports \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "--timeout", "180", "--access-logfile", "-", "--error-logfile", "-", "app:app"]
