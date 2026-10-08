import os
import smtplib

from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders


def kirim_email_yahoo(
    penerima,
    subject,
    body,
    file_attachment
):

    yahoo_email = os.environ.get('SMTP_EMAIL', '')

    # GANTI DENGAN APP PASSWORD YAHOO
    yahoo_password = os.environ.get('SMTP_PASSWORD', '')

    msg = MIMEMultipart()

    msg["From"] = yahoo_email
    msg["To"] = penerima
    msg["Subject"] = subject

    msg.attach(
        MIMEText(
            body,
            "html"
        )
    )

    if file_attachment:

        with open(
            file_attachment,
            "rb"
        ) as attachment:

            part = MIMEBase(
                "application",
                "octet-stream"
            )

            part.set_payload(
                attachment.read()
            )

        encoders.encode_base64(part)

        filename = (
            file_attachment
            .split("/")[-1]
        )

        part.add_header(
            "Content-Disposition",
            f'attachment; filename="{filename}"'
        )

        msg.attach(part)

    server = smtplib.SMTP(
        "smtp.mail.yahoo.com",
        587
    )

    server.starttls()

    server.login(
        yahoo_email,
        yahoo_password
    )

    server.send_message(msg)

    server.quit()

    return True