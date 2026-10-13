"""Find people in the WhatsApp desktop app's own contact list and send them a message."""

import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.parse import quote


CONTACTS_DB = Path.home() / "Library/Group Containers/group.net.whatsapp.WhatsApp.shared/ContactsV2.sqlite"
SEND_DELAY_SECONDS = 2.0


def find_contacts(name: str) -> list[dict]:

    """Return WhatsApp contacts whose name contains the given text, as name and phone pairs."""

    name = name.strip()

    if not name or not CONTACTS_DB.exists():
        return []

    with sqlite3.connect(f"file:{CONTACTS_DB}?mode=ro", uri=True) as db:
        rows = db.execute(
            "SELECT ZFULLNAME, ZWHATSAPPID FROM ZWAADDRESSBOOKCONTACT "
            "WHERE ZFULLNAME LIKE ? AND ZWHATSAPPID LIKE '%@s.whatsapp.net' "
            "ORDER BY ZFULLNAME",
            (f"%{name}%",),
            ).fetchall()

    contacts = []
    seen = set()

    for full_name, jid in rows:
        phone = jid.split("@")[0]

        if phone in seen:
            continue

        seen.add(phone)
        contacts.append({"name": full_name, "phone": phone})

    return contacts


def send(
    phone: str,
    message: str
    ) -> bool:

    """Open the chat in WhatsApp with the message typed in, then press Enter to send it."""

    url = f"whatsapp://send?phone={phone}&text={quote(message)}"

    if subprocess.run(["open", url]).returncode != 0:
        return False

    time.sleep(SEND_DELAY_SECONDS)

    script = (
        'tell application "WhatsApp" to activate\n'
        "delay 0.3\n"
        'tell application "System Events" to keystroke return'
        )
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)

    if result.returncode != 0:
        print(f"  could not press Enter: {result.stderr.strip()}")

    return result.returncode == 0
