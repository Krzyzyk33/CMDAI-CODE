import os
import sys
import threading
import subprocess


def _send_windows_toast(title: str, message: str) -> None:
    safe_title = title.replace("'", "''").replace("\n", " ")
    safe_msg = message.replace("'", "''").replace("\n", " ")

    ps_script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null; "
        "$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$nodes = $template.GetElementsByTagName('text'); "
        f"$nodes.Item(0).AppendChild($template.CreateTextNode('{safe_title}')) > $null; "
        f"$nodes.Item(1).AppendChild($template.CreateTextNode('{safe_msg}')) > $null; "
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($template); "
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('CMDAI CODE').Show($toast);"
    )

    try:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        p = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True,
            timeout=3.0,
            creationflags=flags,
        )
        if p.returncode == 0:
            return
    except Exception:
        pass

    try:
        balloon_script = (
            "[reflection.assembly]::loadwithpartialname('System.Windows.Forms') > $null; "
            "$notify = new-object system.windows.forms.notifyicon; "
            "$notify.icon = [system.drawing.systemicons]::Information; "
            "$notify.visible = $true; "
            f"$notify.showballoontip(4000, '{safe_title}', '{safe_msg}', [system.windows.forms.tooltipicon]::Info);"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", balloon_script],
            capture_output=True,
            timeout=2.0,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except Exception:
        pass


def _send_macos_notification(title: str, message: str) -> None:
    safe_title = title.replace('"', '\\"').replace("\n", " ")
    safe_msg = message.replace('"', '\\"').replace("\n", " ")
    try:
        subprocess.run(
            ["osascript", "-e", f'display notification "{safe_msg}" with title "{safe_title}"'],
            capture_output=True,
            timeout=2.0,
        )
    except Exception:
        pass


def _send_linux_notification(title: str, message: str) -> None:
    try:
        subprocess.run(
            ["notify-send", title, message],
            capture_output=True,
            timeout=2.0,
        )
    except Exception:
        pass


def send_desktop_notification(title: str, message: str) -> None:
    def _worker():
        try:
            if sys.platform == "win32":
                _send_windows_toast(title, message)
            elif sys.platform == "darwin":
                _send_macos_notification(title, message)
            else:
                _send_linux_notification(title, message)
        except Exception:
            pass

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
