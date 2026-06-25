"""HTMLメール送信（週次レポートをアカウントごとに個別送信するための薄いラッパ）。

- SMTP(SSL) で1通を送る。CA は certifi を優先（macOS の SSL CERT_VERIFY_FAILED 回避。
  GitHub Actions/ubuntu でも有効）。certifi が無ければ既定コンテキストにフォールバック。
- MIME 構築（build_message）と送信（send_message）を分離し、送信は smtp_factory を
  注入できるようにしてテスト可能にする（実SMTPに繋がずに検証する）。
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger("mailer")


def _safe_header(value: str, field: str) -> str:
    """ヘッダ値に改行(CR/LF)が混ざるとヘッダ injection になり得るので拒否する。"""
    if "\r" in value or "\n" in value:
        raise ValueError(f"メールヘッダ {field} に改行は使えません: {value!r}")
    return value


def build_message(sender: str, to: str, subject: str, html: str,
                  attachment_bytes: bytes | None = None,
                  attachment_name: str | None = None) -> MIMEMultipart:
    """HTML本文（＋任意でHTML添付）のメールメッセージを作る。"""
    msg = MIMEMultipart("mixed")
    msg["Subject"] = _safe_header(subject, "Subject")
    msg["From"] = _safe_header(sender, "From")
    msg["To"] = _safe_header(to, "To")
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText("このメールはHTMLレポートです。HTML表示でご覧ください。", "plain", "utf-8"))
    alt.attach(MIMEText(html, "html", "utf-8"))
    msg.attach(alt)
    if attachment_bytes is not None:
        att = MIMEApplication(attachment_bytes, _subtype="html")
        att.add_header("Content-Disposition", "attachment",
                       filename=attachment_name or "report.html")
        msg.attach(att)
    return msg


def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 certifi 無し等は既定にフォールバック
        return ssl.create_default_context()


def send_message(user: str, password: str, msg: MIMEMultipart,
                 host: str = "smtp.gmail.com", port: int = 465, smtp_factory=None) -> None:
    """msg を SMTP(SSL) で送る。smtp_factory(host, port) を注入すればテストで差し替え可能。"""
    to = msg["To"]
    if smtp_factory is not None:
        client = smtp_factory(host, port)
        try:  # login/sendmail が例外でも必ず接続を閉じる（実SMTPの with と同じ保証）
            client.login(user, password)
            client.sendmail(user, [to], msg.as_string())
        finally:
            close = getattr(client, "quit", None) or getattr(client, "close", None)
            if close:
                close()
        return
    with smtplib.SMTP_SSL(host, port, context=_ssl_context()) as s:
        s.login(user, password)
        s.sendmail(user, [to], msg.as_string())


def send_html(user: str, password: str, sender: str, to: str, subject: str, html: str,
              attachment_name: str | None = None, host: str = "smtp.gmail.com",
              port: int = 465, smtp_factory=None) -> None:
    """HTML本文を本文＋同内容HTML添付で1通送る便利関数。"""
    msg = build_message(sender, to, subject, html,
                        attachment_bytes=html.encode("utf-8"), attachment_name=attachment_name)
    send_message(user, password, msg, host=host, port=port, smtp_factory=smtp_factory)
