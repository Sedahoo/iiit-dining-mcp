"""MCP server for the IIIT Hyderabad Dining Portal (https://dining.iiit.ac.in/).

Exposes the mess dining portal as MCP tools: browse menus / timings / mess info
without auth, and (with an auth key) view and manage your own meal
registrations, feedback, bills and balance.

Auth
----
Generate an auth key at https://dining.iiit.ac.in/ -> Settings -> Auth Keys.
Provide it via the DINING_AUTH_KEY environment variable. The key is sent as
`Authorization: Bearer <key>` by default; override with DINING_AUTH_SCHEME if
your deployment expects a different scheme (see _auth_headers below). Run the
`dining_whoami` tool to confirm the key works.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any

import httpx

try:  # mcp SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server
except ModuleNotFoundError:  # mcp SDK 2.x renamed FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as _Server

def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a .env in CWD or the project root, without
    overriding variables already set in the real environment."""
    for base in (os.getcwd(), os.path.dirname(os.path.dirname(os.path.abspath(__file__)))):
        path = os.path.join(base, ".env")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip("'\""))
        except OSError:
            pass


_load_dotenv()

BASE_URL = os.environ.get("DINING_BASE_URL", "https://dining.iiit.ac.in").rstrip("/")
AUTH_KEY = os.environ.get("DINING_AUTH_KEY", "").strip()
# How to present the auth key to the API. The portal's "Auth Keys" feature is
# meant for third-party apps; the exact transport isn't publicly documented, so
# this is configurable and `dining_whoami` lets you find the one that works.
#   raw           -> Authorization: <key>          (default; confirmed scheme)
#   bearer        -> Authorization: Bearer <key>
#   token         -> Authorization: Token <key>
#   header:<Name> -> <Name>: <key>                 e.g. header:X-Api-Key
#   cookie:<name> -> Cookie: <name>=<key>          e.g. cookie:session
AUTH_SCHEME = os.environ.get("DINING_AUTH_SCHEME", "raw").strip()

TIMEOUT = httpx.Timeout(30.0)

mcp = _Server("iiit-dining")


# --------------------------------------------------------------------------- #
# HTTP plumbing
# --------------------------------------------------------------------------- #
def _auth_headers() -> dict[str, str]:
    """Build request headers carrying the auth key, per DINING_AUTH_SCHEME."""
    if not AUTH_KEY:
        return {}
    scheme = AUTH_SCHEME.lower()
    if scheme == "bearer":
        return {"Authorization": f"Bearer {AUTH_KEY}"}
    if scheme == "token":
        return {"Authorization": f"Token {AUTH_KEY}"}
    if scheme == "raw":
        return {"Authorization": AUTH_KEY}
    if scheme.startswith("header:"):
        return {AUTH_SCHEME.split(":", 1)[1]: AUTH_KEY}
    if scheme.startswith("cookie:"):
        return {"Cookie": f"{AUTH_SCHEME.split(':', 1)[1]}={AUTH_KEY}"}
    # Unknown scheme: fall back to raw rather than silently sending nothing.
    return {"Authorization": AUTH_KEY}


class DiningError(Exception):
    """A structured error returned by the dining API or the transport."""


def _request(method: str, path: str, *, auth: bool = False, **kwargs: Any) -> Any:
    """Call the dining API and return the parsed `data` payload.

    Raises DiningError with a human-readable message on any failure so the
    MCP tool surfaces something actionable instead of a raw stack trace.
    """
    if auth and not AUTH_KEY:
        raise DiningError(
            "This action needs an auth key. Set DINING_AUTH_KEY to a key from "
            "dining.iiit.ac.in -> Settings -> Auth Keys."
        )
    headers = {"accept": "application/json"}
    if auth:
        headers.update(_auth_headers())

    url = f"{BASE_URL}/api{path}"
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=False) as client:
            resp = client.request(method, url, headers=headers, **kwargs)
    except httpx.HTTPError as exc:  # network/timeout/etc.
        raise DiningError(f"Could not reach the dining portal: {exc}") from exc

    # Auth-protected endpoints answer 401 with a JSON error body.
    if resp.status_code == 401:
        raise DiningError(
            "Unauthorized (401). The auth key is missing, wrong, expired, or "
            "sent with the wrong DINING_AUTH_SCHEME. Current scheme: "
            f"{AUTH_SCHEME!r}. Run dining_whoami after fixing it."
        )

    # Some write endpoints (e.g. cancel) answer 204 No Content on success.
    if resp.status_code == 204 or not resp.content:
        return None

    try:
        body = resp.json()
    except ValueError:
        raise DiningError(
            f"Unexpected non-JSON response ({resp.status_code}) from {path}."
        )

    if not resp.is_success or (isinstance(body, dict) and body.get("error")):
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            raise DiningError(f"{err.get('code', 'error')}: {err.get('message', body)}")
        raise DiningError(f"Request to {path} failed ({resp.status_code}): {body}")

    return body.get("data") if isinstance(body, dict) else body


def _today() -> str:
    return date.today().isoformat()


def _valid_date(value: str) -> str:
    """Validate/normalise a YYYY-MM-DD date string, raising DiningError if bad."""
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError:
        raise DiningError(f"Invalid date {value!r}; expected format YYYY-MM-DD.")


MEALS = ("breakfast", "lunch", "snacks", "dinner")


def _valid_meal(meal: str) -> str:
    m = meal.strip().lower()
    if m not in MEALS:
        raise DiningError(f"Invalid meal {meal!r}; expected one of {', '.join(MEALS)}.")
    return m


# --------------------------------------------------------------------------- #
# Public (no auth) tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def list_messes() -> list[dict]:
    """List all messes (dining halls) with their id, name, tags and rating.

    No authentication required. Use the returned `id` (e.g. "north",
    "kadamba-veg", "yuktahar") wherever a mess id is needed.
    """
    return _request("GET", "/mess/info")


@mcp.tool()
def get_menu(on: str = "", mess: str = "") -> Any:
    """Get the mess menu for a given day, broken down by mess and meal.

    Args:
        on: Date as YYYY-MM-DD. Defaults to today.
        mess: Optional mess id (e.g. "yuktahar") to return only that mess.

    No authentication required.
    """
    on = _valid_date(on) if on else _today()
    data = _request("GET", "/mess/menus", params={"on": on})
    if mess:
        data = [m for m in data if m.get("mess") == mess]
        if not data:
            raise DiningError(f"No menu for mess {mess!r} on {on}.")
    return {"date": on, "menus": data}


@mcp.tool()
def get_meal_timings(on: str = "") -> Any:
    """Get meal start/end times per mess for a given day.

    Args:
        on: Date as YYYY-MM-DD. Defaults to today.

    No authentication required.
    """
    on = _valid_date(on) if on else _today()
    return {"date": on, "timings": _request("GET", "/config/meal-timings", params={"on": on})}


@mcp.tool()
def get_mess_rates(meal: str, on: str = "") -> Any:
    """Get per-meal pricing (student and guest rates) for each mess.

    Args:
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD. Defaults to today.

    Requires an auth key.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on) if on else _today()
    return _request("GET", "/mess/rates", auth=True, params={"meal": meal, "on": on})


@mcp.tool()
def get_mess_capacities(meal: str, on: str = "") -> Any:
    """Get remaining booking capacity per mess for a meal on a day.

    Args:
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD. Defaults to today.

    Requires an auth key.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on) if on else _today()
    return _request("GET", "/mess/capacities", auth=True, params={"meal": meal, "on": on})


# --------------------------------------------------------------------------- #
# Account / read (auth) tools
# --------------------------------------------------------------------------- #
@mcp.tool()
def dining_whoami() -> Any:
    """Return the authenticated user's profile. Use this to verify the auth key.

    Requires an auth key. If this fails with a 401, the key or
    DINING_AUTH_SCHEME is wrong.
    """
    return _request("GET", "/auth/me", auth=True)


@mcp.tool()
def get_preferences() -> Any:
    """Get the user's saved dining preferences. Requires an auth key."""
    return _request("GET", "/preferences", auth=True)


@mcp.tool()
def get_registration(on: str = "") -> Any:
    """Get your meal registrations for a single day.

    Args:
        on: Date as YYYY-MM-DD. Defaults to today.

    Requires an auth key.
    """
    on = _valid_date(on) if on else _today()
    return {"date": on, "registration": _request("GET", "/registration", auth=True, params={"date": on})}


@mcp.tool()
def list_registrations(frm: str, to: str) -> Any:
    """List your meal registrations over a date range (inclusive).

    Args:
        frm: Start date, YYYY-MM-DD.
        to: End date, YYYY-MM-DD.

    Requires an auth key.
    """
    frm, to = _valid_date(frm), _valid_date(to)
    return _request("GET", "/registrations", auth=True, params={"from": frm, "to": to})


@mcp.tool()
def get_bill(month: int, year: int) -> Any:
    """Get your mess bill for a given month.

    Args:
        month: Month number 1-12.
        year: Four-digit year, e.g. 2026.

    Requires an auth key.
    """
    if not 1 <= month <= 12:
        raise DiningError("month must be between 1 and 12.")
    return _request(
        "GET", "/registrations/bill", auth=True,
        params={"month": str(month), "year": str(year)},
    )


@mcp.tool()
def list_bills() -> Any:
    """List all of the user's finalised monthly bills. Requires an auth key."""
    return _request("GET", "/bills", auth=True)


# --------------------------------------------------------------------------- #
# Account / write (auth) tools  --  these MODIFY your real mess account
# --------------------------------------------------------------------------- #
@mcp.tool()
def register_meal(meal: str, on: str, mess: str, guests: int = 0) -> Any:
    """Register (book) a meal at a mess for a given day. MODIFIES your account.

    Args:
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD.
        mess: Mess id to register at (see list_messes).
        guests: Number of extra guest plates (default 0).

    Requires an auth key. Subject to the portal's registration window.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on)
    if guests < 0:
        raise DiningError("guests cannot be negative.")
    return _request(
        "POST", "/registrations", auth=True,
        json={"meal_mess": mess, "meal_type": meal, "meal_date": on, "guests": guests},
    )


@mcp.tool()
def set_cancel_meal(meal: str, on: str, cancel: bool = True) -> Any:
    """Cancel or un-cancel a registered meal. MODIFIES your account.

    Args:
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD.
        cancel: True to cancel, False to undo a cancellation.

    Requires an auth key. Subject to the cancellation window.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on)
    path = "/registrations/cancel" if cancel else "/registrations/uncancel"
    return _request("POST", path, auth=True, json={"meal_type": meal, "meal_date": on})


@mcp.tool()
def set_skip_meal(meal: str, on: str, mess: str, skipping: bool = True) -> Any:
    """Mark a meal as skipped / un-skipped (wastage reduction). MODIFIES your account.

    Args:
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD.
        mess: Mess id the meal is registered at.
        skipping: True to skip, False to un-skip.

    Requires an auth key. Subject to the skip window.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on)
    return _request(
        "POST", "/registrations/skipping", auth=True,
        json={"meal_type": meal, "meal_date": on, "meal_mess": mess, "skipping": skipping},
    )


@mcp.tool()
def submit_feedback(meal: str, on: str, rating: int, remarks: str = "") -> Any:
    """Submit anonymous feedback (1-5 stars) for a meal. MODIFIES your account.

    Args:
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD.
        rating: Integer 1-5.
        remarks: Optional free-text remarks.

    Requires an auth key. Subject to the feedback window.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on)
    rating = max(1, min(5, int(rating)))
    return _request(
        "POST", "/registrations/feedback", auth=True,
        json={"meal_type": meal, "meal_date": on, "rating": rating,
              "remarks": remarks or None},
    )


@mcp.tool()
def list_extras(meal: str, on: str) -> Any:
    """List extra (à la carte) items available for a meal on a day.

    Args:
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD.

    Requires an auth key.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on)
    return _request("GET", "/extras", auth=True, params={"meal": meal, "date": on})


@mcp.tool()
def book_extra(extra_id: str, meal: str, on: str, mess: str) -> Any:
    """Book an extra item for a meal. MODIFIES your account.

    Args:
        extra_id: The `extra` item id from list_extras.
        meal: One of breakfast, lunch, snacks, dinner.
        on: Date as YYYY-MM-DD.
        mess: Mess id.

    Requires an auth key.
    """
    meal = _valid_meal(meal)
    on = _valid_date(on)
    return _request(
        "POST", "/registrations/extras", auth=True,
        json={"extra": extra_id, "meal_date": on, "meal_type": meal, "meal_mess": mess},
    )


if __name__ == "__main__":
    mcp.run()
