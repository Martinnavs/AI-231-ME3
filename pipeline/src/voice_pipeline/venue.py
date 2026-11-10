"""Where the demo is: the venue's place name and, when it can be found, its barangay.

The barangay is looked up once at boot (OpenStreetMap's Nominatim reverse search, about 1 s) from
the venue's coordinates and kept; every later question is answered from memory, offline. The
coordinates are `$VOICE_AGENT_HOME_COORDS` ("lat,lon", copied from a map pin: the answer then says
"in") or, failing that, the venue city's centre (the answer says "around": the centre of a city is
near the user, not at the user). No lookup, or a failed one: the answer names the place only.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

HOME_PLACE = os.environ.get("VOICE_AGENT_HOME_PLACE", "Cebu City")  # same default as web_info
# the default venue's centre, so no geocoder is needed at boot (same point as web_info)
CITY_CENTRES = {"cebu city": (10.3157, 123.8854)}
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "me3-voice-agent/0.1 (https://github.com/Martinnavs/AI-231-ME3; course project)"
TIMEOUT_S = 3.0
# an OSM address key that names a barangay, most likely first ("suburb" in Cebu City)
BARANGAY_KEYS = ("suburb", "village", "neighbourhood", "city_district", "quarter")
CITY_KEYS = ("city", "municipality", "town")


@dataclass
class Venue:
    place: str
    barangay: str | None = None
    exact: bool = False  # the coordinates were given, not the city's centre


current = Venue(HOME_PLACE)


def coordinates(
    env: dict[str, str] | os._Environ[str] | None = None, place: str = HOME_PLACE
) -> tuple[float, float, bool] | None:
    """(lat, lon, exact) for the venue, or None when nothing is known."""
    given = (os.environ if env is None else env).get("VOICE_AGENT_HOME_COORDS", "")
    try:
        lat, lon = (float(part) for part in given.split(","))
        return lat, lon, True
    except ValueError:
        pass
    centre = CITY_CENTRES.get(place.lower())
    return (*centre, False) if centre else None


def barangay_from(address: dict[str, str]) -> str | None:
    for key in BARANGAY_KEYS:
        if address.get(key):
            return address[key].strip()
    return None


async def resolve(client: httpx.AsyncClient | None = None) -> Venue:
    """Look the barangay up and remember it in `current`; on any failure nothing changes."""
    global current
    found = coordinates(place=current.place)
    if found is None:
        return current
    lat, lon, exact = found
    own = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    try:
        r = await http.get(
            NOMINATIM_REVERSE,
            params={
                "lat": lat,
                "lon": lon,
                "zoom": 14,
                "format": "jsonv2",
                "addressdetails": 1,
                "accept-language": "en",
            },
        )
        r.raise_for_status()
        address = r.json().get("address") or {}
    except (httpx.HTTPError, ValueError, AttributeError):
        return current
    finally:
        if own:
            await http.aclose()
    barangay = barangay_from(address)
    if barangay:
        city = next((address[k] for k in CITY_KEYS if address.get(k)), current.place)
        current = Venue(city, barangay, exact)
    return current


def spoken(*, asked_barangay: bool = False) -> str:
    """The answer to "where are we"."""
    here = current
    if here.barangay:
        verb = "in" if here.exact else "around"
        return f"We're {verb} Barangay {here.barangay}, {here.place}."
    if asked_barangay:
        return f"I don't know the barangay, but we're in {here.place}."
    return f"We're in {here.place}."


def prompt_line() -> str | None:
    """One line for the base prompt, or None when the barangay is not known."""
    here = current
    if not here.barangay:
        return None
    near = "in" if here.exact else "around"
    return (
        f"Location: the user is {near} Barangay {here.barangay}, {here.place}, Philippines. Use it "
        'for "where am I", "which barangay" and "near me"; never guess another place.'
    )
