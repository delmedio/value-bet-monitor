"""
report.py — compatibilidade para gerar o report semanal.
"""

from alert import send_export, send_weekly_report


def send_report_email(days: int = 7) -> bool:
    # O report semanal central foi consolidado no alert.py.
    send_weekly_report()
    return True


if __name__ == "__main__":
    send_weekly_report()
