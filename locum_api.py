"""HTTP client for the Locum ED HJ Booking Google Apps Script API."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from typing import Any

# Suppress urllib3/charset version warning on newer Python builds before importing requests
warnings.filterwarnings("ignore", message=".*urllib3.*doesn't match a supported version.*")

import requests

API_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbyNYwYmMMiMmFdML640Hm9BqtB1SQUC9YgH40atqa6h3HvwqxwOGrfprFRYhYNMDoOX/exec"
)

ROLE_TO_KEY = {
    "MO": "mo",
    "AMO": "amo",
    "NURSE": "sn",
}


class ApiError(Exception):
    """Raised when the booking API returns an error or cannot be reached."""

    def __init__(self, message: str, expired: bool = False) -> None:
        super().__init__(message)
        self.expired = expired


@dataclass
class SessionInfo:
    token: str
    profile: dict[str, Any]

    @property
    def role_key(self) -> str:
        role = self.profile.get("role", "MO")
        return ROLE_TO_KEY.get(role, "mo")


class LocumClient:
    """Thread-safe / persistent-session API client for Locum ED HJ Booking."""

    def __init__(self, api_url: str = API_URL, timeout: float = 25.0) -> None:
        self.api_url = api_url
        self.timeout = timeout
        self.http = requests.Session()
        self.http.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
                )
            }
        )
        self.session: SessionInfo | None = None
        self._ic: str | None = None
        self._last4: str | None = None

    def _request(self, action: str, data: dict[str, Any] | None = None, use_get: bool = False) -> dict[str, Any]:
        try:
            if use_get:
                resp = self.http.get(f"{self.api_url}?action={action}", timeout=self.timeout)
            else:
                payload = {"action": action}
                if data:
                    payload.update(data)
                resp = self.http.post(
                    self.api_url,
                    data=json.dumps(payload),
                    headers={"Content-Type": "text/plain;charset=utf-8"},
                    timeout=self.timeout,
                )
            resp.raise_for_status()
            body = resp.json()
        except requests.Timeout as exc:
            raise ApiError("Connection timed out.") from exc
        except requests.RequestException as exc:
            raise ApiError(f"Network error: {exc}") from exc
        except ValueError as exc:
            raise ApiError("Invalid JSON response from server.") from exc

        if not body.get("ok"):
            err_msg = str(body.get("error") or "Unknown API error.")
            is_expired = "session" in err_msg.lower()
            raise ApiError(err_msg, expired=is_expired)
        return body

    def check_ic(self, ic: str) -> bool:
        res = self._request("check", {"ic": ic})
        return bool(res.get("exists"))

    def login(self, ic: str, last4: str) -> SessionInfo:
        res = self._request("login", {"ic": ic, "last4": last4})
        self._ic = ic
        self._last4 = last4
        self.session = SessionInfo(token=res["token"], profile=res["profile"])
        return self.session

    def _call_authed(self, action: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.session:
            raise ApiError("Not logged in.", expired=True)
        payload = dict(data or {})
        payload["token"] = self.session.token
        try:
            return self._request(action, payload)
        except ApiError as exc:
            if exc.expired and self._ic and self._last4:
                self.login(self._ic, self._last4)
                payload["token"] = self.session.token
                return self._request(action, payload)
            raise

    def get_availability(self) -> list[dict[str, Any]]:
        res = self._request("availability", use_get=True)
        return list(res.get("days") or [])

    def get_my_bookings(self) -> tuple[list[dict[str, Any]], int]:
        res = self._call_authed("mine")
        return list(res.get("bookings") or []), int(res.get("cutoff") or 24)

    def book_slot(self, date_str: str, slot_key: str) -> dict[str, Any]:
        res = self._call_authed("book", {"date": date_str, "slot": slot_key})
        return dict(res.get("booking") or {})

    def cancel_booking(self, booking_id: str) -> dict[str, Any]:
        return self._call_authed("cancel", {"id": booking_id})
