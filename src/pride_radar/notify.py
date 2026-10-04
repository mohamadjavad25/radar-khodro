"""Console and Windows desktop notifications."""

from __future__ import annotations

import subprocess
import sys
import time
from dataclasses import dataclass

from .market import Verdict
from .models import Listing
from .rules import MatchResult
from . import text as text_utils

_RESET = "\033[0m"
_COLORS = {
    "green": "\033[32m",
    "yellow": "\033[33m",
    "cyan": "\033[36m",
    "red": "\033[31m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}

_LABEL_EMOJI = {
    "خیلی زیر بازار": "🔥",
    "زیر بازار": "🟢",
    "منصفانه": "🔵",
    "کمی بالای بازار": "🟡",
    "بالای بازار": "🔴",
    "مشکوک به قیمت غیرواقعی": "⚠️",
    "نامشخص": "⚪",
}


def _supports_color() -> bool:
    if not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        return bool(sys.stdout.isatty())
    return True


@dataclass(slots=True)
class Notifier:
    console: bool = True
    desktop: bool = True
    color: bool = True
    output: object = None

    def _paint(self, text: str, *styles: str) -> str:
        if not self.color or not _supports_color():
            return text
        prefix = "".join(_COLORS.get(style, "") for style in styles)
        return f"{prefix}{text}{_RESET}" if prefix else text

    def _write(self, line: str = "") -> None:
        stream = self.output or sys.stdout
        print(line, file=stream, flush=True)

    def banner(self, message: str) -> None:
        self._write()
        self._write(self._paint("=" * 68, "cyan"))
        self._write(self._paint(f"  {message}", "bold", "cyan"))
        self._write(self._paint("=" * 68, "cyan"))

    def info(self, message: str) -> None:
        self._write(f"  {self._paint('·', 'dim')} {message}")

    def warn(self, message: str) -> None:
        self._write(f"  {self._paint('!', 'yellow')} {message}")

    def error(self, message: str) -> None:
        self._write(f"  {self._paint('x', 'red')} {message}")

    def ok(self, message: str) -> None:
        self._write(f"  {self._paint('+', 'green')} {message}")

    def hit(self, result: MatchResult) -> None:
        listing = result.listing
        verdict: Verdict = result.verdict
        emoji = _LABEL_EMOJI.get(verdict.label, "•")

        style = "green" if verdict.is_below_market else "yellow"
        year = listing.year or "؟"
        mileage = f"{listing.mileage_km:,} کیلومتر" if listing.mileage_km else "کارکرد نامشخص"

        self._write()
        self._write(
            f"  {emoji} {self._paint(listing.label, 'bold')} "
            f"{self._paint(f'({listing.gregorian_year or year})', 'dim')}  "
            f"{self._paint(text_utils.format_toman(listing.price.toman), style, 'bold')}"
        )
        self._write(f"      {listing.title[:70]}")
        self._write(
            f"      {self._paint('کارکرد:', 'dim')} {mileage}"
            f"   {self._paint('امتیاز:', 'dim')} {self._paint(f'{result.score:.0f} از ۱۰۰', style)}"
            f"   {self._paint('قانون:', 'dim')} {result.rule.name}"
        )
        if verdict.baseline_median and verdict.delta_toman is not None:
            self._write(
                f"      {self._paint('بازار:', 'dim')} میانهٔ بازار "
                f"{text_utils.format_toman(verdict.baseline_median)}"
                f"  ({verdict.label})"
            )
        if listing.url:
            self._write(f"      {self._paint(listing.url, 'cyan')}")
        if listing.district:
            self._write(f"      {self._paint(listing.district, 'dim')}")

        if self.desktop:
            _toast(
                f"{listing.label} - {text_utils.format_toman(listing.price.toman)}",
                listing.title[:80],
            )

    def summary(self, fetched: int, new_items: int, notified: int) -> None:
        self._write()
        self._write(
            self._paint(
                f"  دریافت {fetched} آگهی | جدید {self._paint(str(new_items), 'green')} | "
                f"اعلان {self._paint(str(notified), 'yellow')}",
                "bold",
            )
        )
        self._write()

    def listing_line(self, listing: Listing, suffix: str = "") -> str:
        year = listing.gregorian_year or listing.year or "؟"
        price = text_utils.format_toman(listing.price.toman)
        return f"{listing.label} {year} | {price}{suffix}"


def _toast(title: str, message: str) -> None:
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] > $null;"
        "$t=[Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent(1);"
        "$t.GetElementsByTagName('text')[0].AppendChild($t.CreateTextNode($args[0])) > $null;"
        "$t.GetElementsByTagName('text')[1].AppendChild($t.CreateTextNode($args[1])) > $null;"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Pride Radar').Show("
        "[Windows.UI.Notifications.ToastNotification]::new($t)) > $null"
    )
    try:
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
                title,
                message,
            ],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return


def beep() -> None:
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        sys.stdout.write("\a")
        sys.stdout.flush()


def wait(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)

