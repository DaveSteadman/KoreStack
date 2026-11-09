# ====================================================================================================
# MARK: OVERVIEW
# ====================================================================================================
# CSV-backed hourly availability monitor for the KoreData gateway UI.
#
# One target URL is retained in settings.csv. Each hourly probe is recorded in
# hourly.csv, which keeps the monitor portable and small enough to inspect or
# archive without a database.
# ====================================================================================================

from __future__ import annotations

import asyncio
from calendar import monthrange
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import threading
from typing import Any
from urllib.parse import urlsplit

import httpx


_SETTINGS_COLUMNS = ("url",)
_HOURLY_COLUMNS   = ("hour_utc", "url", "succeeded", "status_code", "error")


def _hour_start(value: datetime) -> datetime:
    """Return the UTC hour containing value."""
    return value.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _parse_hour(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return _hour_start(parsed)


def _validated_url(value: object) -> str:
    """Return a usable HTTP(S) target, accepting an empty value to disable it."""
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Uptime URL must be a complete http:// or https:// URL")
    return url


class UptimeMonitor:
    """Persist a single hourly HTTP probe stream and shape it for the Uptime UI."""

    def __init__(self, directory: Path) -> None:
        self.directory     = directory
        self.settings_file = directory / "settings.csv"
        self.hourly_file   = directory / "hourly.csv"
        self._lock         = threading.RLock()
        self._probe_lock   = asyncio.Lock()

    def _read_rows(self, path: Path) -> list[dict[str, str]]:
        if not path.exists():
            return []
        try:
            with path.open("r", newline="", encoding="utf-8") as handle:
                return list(csv.DictReader(handle))
        except (OSError, csv.Error):
            return []

    def _write_rows(self, path: Path, fieldnames: tuple[str, ...], rows: list[dict[str, object]]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)

    def target_url(self) -> str:
        with self._lock:
            rows = self._read_rows(self.settings_file)
        return str(rows[0].get("url") or "").strip() if rows else ""

    def save_target_url(self, value: object) -> str:
        url = _validated_url(value)
        with self._lock:
            self._write_rows(self.settings_file, _SETTINGS_COLUMNS, [{"url": url}])
        return url

    def _records_for_target(self, target_url: str) -> dict[datetime, dict[str, str]]:
        with self._lock:
            rows = self._read_rows(self.hourly_file)
        records: dict[datetime, dict[str, str]] = {}
        for row in rows:
            hour = _parse_hour(row.get("hour_utc"))
            if hour is not None and row.get("url") == target_url:
                records[hour] = row
        return records

    def _record_exists(self, target_url: str, hour: datetime) -> bool:
        return hour in self._records_for_target(target_url)

    def _save_record(
        self,
        target_url: str,
        hour: datetime,
        succeeded: bool,
        status_code: int | None,
        error: str,
    ) -> None:
        with self._lock:
            rows = self._read_rows(self.hourly_file)
            filtered = [
                row for row in rows
                if not (row.get("url") == target_url and _parse_hour(row.get("hour_utc")) == hour)
            ]
            filtered.append(
                {
                    "hour_utc":    hour.isoformat(),
                    "url":         target_url,
                    "succeeded":   "1" if succeeded else "0",
                    "status_code": str(status_code or ""),
                    "error":       error[:500],
                }
            )
            earliest = hour - timedelta(days=400)
            retained = [
                row for row in filtered
                if (_parse_hour(row.get("hour_utc")) or earliest) >= earliest
            ]
            self._write_rows(self.hourly_file, _HOURLY_COLUMNS, retained)

    async def record_current_hour(self) -> dict[str, Any] | None:
        """Probe the target once during the current UTC hour and persist the result."""
        async with self._probe_lock:
            target_url = self.target_url()
            if not target_url:
                return None

            hour = _hour_start(datetime.now(timezone.utc))
            if self._record_exists(target_url, hour):
                return None

            succeeded   = False
            status_code = None
            error        = ""
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=15.0) as client:
                    response    = await client.get(target_url)
                    status_code = response.status_code
                    succeeded   = 200 <= status_code < 400
                    if not succeeded:
                        error = f"HTTP {status_code}"
            except httpx.HTTPError as exc:
                error = str(exc) or type(exc).__name__

            self._save_record(target_url, hour, succeeded, status_code, error)
            return {
                "hour_utc":    hour.isoformat(),
                "url":         target_url,
                "succeeded":   succeeded,
                "status_code": status_code,
                "error":       error,
            }

    async def run_scheduler(self, stop_event: asyncio.Event) -> None:
        """Ensure a persisted probe exists for each UTC hour while the gateway runs."""
        while not stop_event.is_set():
            try:
                await self.record_current_hour()
            except Exception:
                # The next minute can safely retry a transient monitor failure.
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60.0)
            except TimeoutError:
                pass

    @staticmethod
    def _hour_cell(hour: datetime, now: datetime, record: dict[str, str] | None) -> dict[str, str]:
        label = hour.strftime("%a %d %b, %H:%M UTC")
        if hour > _hour_start(now):
            return {"state": "future", "label": f"{label}: future"}
        if record is None:
            return {"state": "failure", "label": f"{label}: no result"}
        if record.get("succeeded") == "1":
            status = record.get("status_code") or "success"
            return {"state": "success", "label": f"{label}: HTTP {status}"}
        detail = record.get("error") or "failed"
        return {"state": "failure", "label": f"{label}: {detail}"}

    @staticmethod
    def _daily_cell(day: date, now: datetime, records: dict[datetime, dict[str, str]]) -> dict[str, str]:
        today = now.date()
        label = day.strftime("%a %d %b %Y")
        if day > today:
            return {"state": "future", "label": f"{label}: future"}

        last_hour = now.hour if day == today else 23
        slots = [
            records.get(datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc))
            for hour in range(last_hour + 1)
        ]
        successes = sum(record is not None and record.get("succeeded") == "1" for record in slots)
        if successes == len(slots):
            state = "success"
            text  = "all hourly checks succeeded"
        elif successes == 0:
            state = "failure"
            text  = "full outage or no hourly results"
        else:
            state = "partial"
            text  = f"{successes}/{len(slots)} hourly checks succeeded"
        return {"state": state, "label": f"{label}: {text}"}

    def view(self, now: datetime | None = None) -> dict[str, Any]:
        """Build rolling seven-day hourly and twelve-month daily timelines."""
        current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        target  = self.target_url()
        records = self._records_for_target(target) if target else {}
        today   = current.date()

        hourly_days = []
        for offset in range(6, -1, -1):
            day   = today - timedelta(days=offset)
            cells = []
            for hour in range(24):
                slot = datetime(day.year, day.month, day.day, hour, tzinfo=timezone.utc)
                cells.append(self._hour_cell(slot, current, records.get(slot)))
            hourly_days.append({"label": day.strftime("%a %d %b"), "cells": cells})

        daily_rows = []
        for offset in range(11, -1, -1):
            year, month_index = divmod(today.year * 12 + today.month - 1 - offset, 12)
            month = month_index + 1
            month_start = date(year, month, 1)
            days_in_month = monthrange(year, month)[1]
            cells = [
                self._daily_cell(date(year, month, day), current, records)
                if day <= days_in_month else {"state": "empty", "label": ""}
                for day in range(1, 32)
            ]
            daily_rows.append(
                {
                    "label": month_start.strftime("%b %Y"),
                    "month": month_start.strftime("%Y-%m"),
                    "cells": cells,
                    "row":   len(daily_rows) + 1,
                }
            )

        return {
            "target_url":  target,
            "hourly_days": hourly_days,
            "daily_rows":  daily_rows,
        }
