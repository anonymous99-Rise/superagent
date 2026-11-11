from __future__ import annotations

import base64
import html
import subprocess
import sys
import time
from pathlib import Path

from . import util

PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType=WindowsRuntime] | Out-Null
$template = {template}
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
try {{ [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.Windows.PowerShell').Show($toast); 'OK' }}
catch {{ Write-Output ('ERR: ' + $_.Exception.Message) }}
"""


class Notifier:
    def __init__(self, store, cfg):
        self.store = store
        self.cfg = cfg
        self._last = time.time() - 10_000

    def enabled(self) -> bool:
        return bool(self.cfg.get("notify.desktop", True))

    def send(self, title: str, body: str, *, force: bool = False, key: str = "") -> bool:
        if not force:
            cooldown = int(self.cfg.get("notify.cooldown_sec", 300))
            if time.time() - self._last < cooldown:
                self.store.log("debug", "notify.cooldown", title)
                return False
        self._last = time.time()
        self.store.log("info", "notify", f"{title} — {body[:160]}", actor="notifier")
        if not self.enabled() or sys.platform != "win32":
            return False
        return self._toast(title, body)

    def _toast(self, title: str, body: str) -> bool:
        t = html.escape(title[:120], quote=True)
        b = html.escape(body[:300], quote=True)
        template = (
            "<toast><visual><binding template='ToastGeneric'>"
            f"<text>{t}</text><text>{b}</text>"
            "</binding></visual></toast>"
        )
        # single-quoted PowerShell literal; double single quotes to escape
        ps_literal = "'" + template.replace("'", "''") + "'"
        script = PS_TEMPLATE.format(template=ps_literal)
        encoded = base64.b64encode(script.encode("utf-16-le")).decode()
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                capture_output=True, text=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            ok = "OK" in (p.stdout or "")
            if not ok:
                self.store.log("warn", "notify.failed", util.truncate(p.stdout + p.stderr, 300))
            return ok
        except Exception as e:
            self.store.log("warn", "notify.error", str(e))
            return False

    def notify_approval(self, approval: dict) -> bool:
        title = "需要你确认：" + (approval.get("title") or "一项操作")
        body = (approval.get("summary") or "请到 SuperAgent 看板处理")[:280]
        key = approval.get("id", "")
        return self.send(title, body, force=True, key=key)
