"""Sends push notifications via ntfy.sh."""
import requests

from app.config import NTFY_BASE_URL, NTFY_TOPIC


def _ascii_safe(text: str) -> str:
    """HTTP headers must be Latin-1/ASCII. Strip anything outside that range
    (e.g. stray emoji) so a header-encoding error can never crash a send.
    Emoji should go in `tags` instead, which ntfy renders as icons."""
    return text.encode("ascii", errors="ignore").decode("ascii").strip()


def send_notification(title: str, message: str, priority: str = "default", tags: list[str] = None) -> bool:
    """Send a push notification via ntfy.

    priority: one of "min", "low", "default", "high", "urgent"
    tags: ntfy emoji shortcodes, e.g. ["warning", "train"] - use these for
          emoji, not literal emoji characters in title/message headers.
    Returns True if the request succeeded, False otherwise (never raises,
    so a notification failure never crashes the scheduler).
    """
    if not NTFY_TOPIC:
        print("[notifications] NTFY_TOPIC is not set (check your .env file) - notification not sent.")
        return False

    headers = {
        "Title": _ascii_safe(title),
        "Priority": _ascii_safe(priority),
    }
    if tags:
        headers["Tags"] = ",".join(_ascii_safe(t) for t in tags)

    try:
        response = requests.post(
            f"{NTFY_BASE_URL}/{NTFY_TOPIC}",
            data=message.encode("utf-8"),  # body supports full UTF-8, emoji fine here
            headers=headers,
            timeout=10,
        )
        return response.status_code == 200
    except (requests.RequestException, UnicodeError) as e:
        print(f"[notifications] Failed to send ntfy notification: {e}")
        return False
