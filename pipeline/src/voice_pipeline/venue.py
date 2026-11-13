"""Where the demo is: the venue's place name and, when it can be found, its barangay.

Where the venue is, most trusted first: `$VOICE_AGENT_HOME_COORDS` ("lat,lon", copied from a map
pin; the answer says "in"), then the machine's public IP looked up once at boot (a Philippine
result only; city-level at best, so the answer says "around"; `VOICE_AGENT_GEOIP=0` turns it off),
then the built-in default, the University of the Philippines Cebu in Lahug, Cebu City ("in"). The
barangay is looked up once at boot (OpenStreetMap's Nominatim reverse search, about 1 s) from those
coordinates and kept; every later question is answered from memory, offline. A failed lookup keeps
the default, which already names its barangay. An IP result within 25 km of the default is
ignored: it is city-level, so it cannot tell Lahug from Calamba and would only make the answer
worse.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

import httpx

HOME_PLACE = os.environ.get("VOICE_AGENT_HOME_PLACE", "Cebu City")  # same default as web_info
# the default venue (the University of the Philippines Cebu, in Barangay Lahug; same point as
# web_info; the demo's location), so nothing has
# to be looked up for the answer to be right when the network is down
DEFAULT_COORDS = (10.3225319, 123.8988885)  # University of the Philippines Cebu, Gorordo Avenue
DEFAULT_LANDMARK = "the University of the Philippines Cebu"  # the demo's venue
DEFAULT_BARANGAY = "Lahug"
GEOIP_URLS = ("https://ipwho.is/", "https://ipapi.co/json/")  # free, no key, HTTPS
GEOIP_SAME_AREA_KM = 25.0  # an IP lookup is city-level: inside this it cannot beat the default pin
GEOIP_COUNTRY = "PH"  # the prompt says Philippines; a result from elsewhere is a VPN or a bad guess
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
    exact: bool = False  # the coordinates were given (a pin or the default), not found by IP
    lat: float | None = None
    lon: float | None = None
    landmark: str | None = None  # a named building the venue is at (the default venue only)


def default_venue() -> Venue:
    """The venue when nothing better is known: Lahug when the place is the default city."""
    if HOME_PLACE.lower() == "cebu city":
        return Venue(HOME_PLACE, DEFAULT_BARANGAY, True, *DEFAULT_COORDS, DEFAULT_LANDMARK)
    return Venue(HOME_PLACE)


current = default_venue()


def geoip_enabled(env: dict[str, str] | os._Environ[str] | None = None) -> bool:
    return (os.environ if env is None else env).get("VOICE_AGENT_GEOIP", "1").strip() != "0"


def coordinates(
    env: dict[str, str] | os._Environ[str] | None = None, place: str = HOME_PLACE
) -> tuple[float, float, bool] | None:
    """(lat, lon, exact) for the venue, or None when nothing is known: the pin in the environment,
    else the default venue's point when the place is the default city."""
    given = (os.environ if env is None else env).get("VOICE_AGENT_HOME_COORDS", "")
    try:
        lat, lon = (float(part) for part in given.split(","))
        return lat, lon, True
    except ValueError:
        pass
    return (*DEFAULT_COORDS, True) if place.lower() == "cebu city" else None


def coords_arg(env: Mapping[str, str] | None = None) -> str | None:
    """ "lat,lon" for the places server: the pin, else the venue found at boot, else None."""
    pin = (os.environ if env is None else env).get("VOICE_AGENT_HOME_COORDS", "").strip()
    if pin:
        return pin
    if current.lat is not None and current.lon is not None:
        return f"{current.lat},{current.lon}"
    return None


def km_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in km."""
    (la1, lo1), (la2, lo2) = (tuple(map(math.radians, p)) for p in (a, b))
    h = (
        math.sin((la2 - la1) / 2) ** 2
        + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    )
    return 12742 * math.asin(math.sqrt(h))


async def geoip(http: httpx.AsyncClient) -> tuple[float, float, str | None] | None:
    """(lat, lon, city) of this machine's public IP address in the Philippines, or None."""
    for url in GEOIP_URLS:
        try:
            r = await http.get(url)
            r.raise_for_status()
            body = r.json()
            if body.get("success") is False:
                continue
            country = str(body.get("country_code") or "").upper()
            lat, lon = float(body["latitude"]), float(body["longitude"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            continue
        if country == GEOIP_COUNTRY:
            return lat, lon, (str(body.get("city") or "").strip() or None)
    return None


def barangay_from(address: dict[str, str]) -> str | None:
    for key in BARANGAY_KEYS:
        if address.get(key):
            return address[key].strip()
    return None


async def resolve(client: httpx.AsyncClient | None = None) -> Venue:
    """Find the venue (pin, else IP address, else the default), look its barangay up and remember
    it in `current`; on any failure what was there stays."""
    global current
    own = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT})
    try:
        found = coordinates(place=current.place)
        city: str | None = None
        pinned = bool(os.environ.get("VOICE_AGENT_HOME_COORDS", "").strip()) and found is not None
        if not pinned and geoip_enabled() and (located := await geoip(http)):
            near_default = found is not None and (
                km_between(found[:2], located[:2]) < GEOIP_SAME_AREA_KM
            )
            if not near_default:  # a city-level guess does not replace a pin in the same city
                found, city = (located[0], located[1], False), located[2]
        if found is None:
            return current
        lat, lon, exact = found
        if found[:2] == (current.lat, current.lon) and current.barangay and not pinned:
            return current  # the default, already named: nothing to look up
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
            if city or pinned:  # the point is new but its barangay is not known: the city only
                current = Venue(city or current.place, None, exact, lat, lon)
            return current
        barangay = barangay_from(address)
        place = next((address[k] for k in CITY_KEYS if address.get(k)), city or current.place)
        if barangay or city or pinned:
            current = Venue(place, barangay, exact, lat, lon)
        return current
    finally:
        if own:
            await http.aclose()


def spoken(*, asked_barangay: bool = False) -> str:
    """The answer to "where are we"."""
    here = current
    if here.barangay and here.landmark and not asked_barangay:
        return f"We're at {here.landmark}, in Barangay {here.barangay}, {here.place}."
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
    at = f"at {here.landmark}, in" if here.landmark else near
    return (
        f"Location: the user is {at} Barangay {here.barangay}, {here.place}, Philippines. Use it "
        'for "where am I", "which barangay" and "near me"; never guess another place.'
    )
