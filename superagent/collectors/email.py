from __future__ import annotations

import email
import imaplib
import os
from datetime import datetime, timedelta, timezone
from email.header import decode_header

from ..models import Signal
from .base import Collector


class EmailCollector(Collector):
    name = "email"
    label = "邮箱 (IMAP)"
    description = "巡检 IMAP 收件箱未读邮件（需配置账号与应用专用密码）"

    def available(self) -> tuple[bool, str]:
        ok, reason = super().available()
        if not ok:
            return ok, reason
        conf = self.conf()
        if not conf.get("host") or not conf.get("username"):
            return False, "email host/username not configured"
        if not os.environ.get(conf.get("password_env", "SUPER_EMAIL_PASSWORD")):
            return False, f"env {conf.get('password_env', 'SUPER_EMAIL_PASSWORD')} not set"
        return True, ""

    def collect(self) -> list[Signal]:
        conf = self.conf()
        host = conf["host"]
        port = int(conf.get("port", 993))
        user = conf["username"]
        pwd = os.environ[conf.get("password_env", "SUPER_EMAIL_PASSWORD")]
        mailbox = conf.get("mailbox", "INBOX")
        limit = int(conf.get("limit", 20))
        days = int(conf.get("recency_days", 7))
        out: list[Signal] = []
        conn = None
        try:
            conn = imaplib.IMAP4_SSL(host, port)
            conn.login(user, pwd)
            conn.select(mailbox, readonly=True)
            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
            typ, data = conn.search(None, f'(UNSEEN SINCE {since})')
            ids = (data[0].split() if data and data[0] else [])[-limit:]
            for uid in reversed(ids):
                typ, msg_data = conn.fetch(uid, "(RFC822)")
                if typ != "OK" or not msg_data:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                subject = self._decode(msg.get("Subject", "(无主题)"))
                sender = self._decode(msg.get("From", ""))
                date = msg.get("Date", "")
                snippet = self._snippet(msg)
                out.append(Signal(
                    source=self.name, kind="email_unread", title=f"未读邮件：{subject[:100]}",
                    body=f"发件人：{sender}\n时间：{date}\n\n{snippet}",
                    url="", actor=sender, occurred_at=self._iso(date),
                    importance=0.6, raw={"subject": subject, "from": sender},
                ))
        except Exception:
            return out
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:
                    pass
        return out

    def _decode(self, value: str) -> str:
        try:
            parts = decode_header(value)
            return "".join(
                (b.decode(enc or "utf-8", "replace") if isinstance(b, bytes) else b) for b, enc in parts
            )
        except Exception:
            return value

    def _snippet(self, msg) -> str:
        try:
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        return part.get_payload(decode=True).decode("utf-8", "replace")[:800]
                return ""
            return (msg.get_payload(decode=True) or b"").decode("utf-8", "replace")[:800]
        except Exception:
            return ""

    def _iso(self, date_str: str) -> str:
        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        except Exception:
            return ""
