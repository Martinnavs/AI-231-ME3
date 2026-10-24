"""How long does Open-Meteo keep an idle HTTPS connection? (R1b, measurement)

For each wait N: one request to open the connection, N seconds idle, then a timed request.
A reused connection costs about one round trip plus the server (~0.5 s from here); a new one adds
TCP and TLS (~0.6 s more). Run twice per N.
"""

import asyncio
import time

import httpx

URL = "https://api.open-meteo.com/v1/forecast"
PARAMS = {"latitude": 14.65, "longitude": 121.05, "daily": "weather_code", "forecast_days": 1}


async def main() -> None:
    for wait in (1, 3, 5, 10, 20, 40, 60):
        for _rep in range(2):
            async with httpx.AsyncClient(timeout=15) as http:
                await http.get(URL, params=PARAMS)
                await asyncio.sleep(wait)
                t = time.perf_counter()
                await http.get(URL, params=PARAMS)
                print(f"idle {wait:>3} s -> next request {1000 * (time.perf_counter() - t):5.0f} ms",
                      flush=True)


asyncio.run(main())
