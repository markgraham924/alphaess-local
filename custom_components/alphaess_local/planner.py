"""Fail-closed validation of Predbat's independently published health heartbeat."""
from datetime import datetime

def authorized(action, mode, read_only, status, attributes, now, timeout):
    allowed_modes = ("Control charge", "Control charge & discharge") if action == "charge" else ("Control charge & discharge",)
    if mode not in allowed_modes or read_only != "off" or status in (None, "unknown", "unavailable"):
        return False
    # Missing error flag is not evidence of a healthy planner.
    if attributes.get("error") is not False:
        return False
    stamp = attributes.get("last_updated")
    if not isinstance(stamp, str):
        return False
    try:
        timestamp = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return False
        age = (now - timestamp).total_seconds()
    except (ValueError, TypeError):
        return False
    return 0 <= age <= timeout
