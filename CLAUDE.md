# ha-chmi — notes for future sessions

Home Assistant custom integration for ČHMÚ open data. Repo: `xripper/ha-chmi`
(public). Installed through HACS as a custom repository. Domain `chmi`,
everything lives in `custom_components/chmi/`.

`README.md` / `README.cs.md` describe the product; this file records what cost
time to find out.

## Working environment

- Test venv: `/tmp/chmi-venv` (Python 3.12). Recreate with
  `/opt/homebrew/bin/python3.12 -m venv /tmp/chmi-venv && /tmp/chmi-venv/bin/pip install -r requirements-test.txt defusedxml pyfive ruff`.
  The system `python3` is 3.9 and cannot even parse this code (`type` aliases,
  `slots=True`).
- `pytest-homeassistant-custom-component` pins **HA 2025.1.4** on Python 3.12.
  That is why the platforms import `AddEntitiesCallback` and not
  `AddConfigEntryEntitiesCallback` (2025.2+). Keep it that way unless the test
  harness moves to a newer HA.
- `aiodns` in that venv is broken (`Channel.getaddrinfo()` signature). Any
  script doing **real** network calls must pass
  `aiohttp.TCPConnector(resolver=aiohttp.ThreadedResolver())`.
- `pyfive`/`defusedxml` are declared in the manifest but not pulled in by the
  test harness, so they are installed into the venv by hand.
- `conftest.py` starts the pycares helper thread up front; without it the HA
  harness reports it as a leaked thread in whichever test runs first.

## Release flow

HACS installs from the newest **GitHub release**, so a fix that is only on
`main` does not reach users: bump `version` in `manifest.json`, commit, tag
`vX.Y.Z`, push the tag, then `gh release create`. `gh` is installed and
authenticated as `xripper`.

Brand images live in `custom_components/chmi/brand/` (HA 2026.3+ serves them
from `/api/brands/integration/chmi/`; the brands repo no longer accepts PRs for
custom integrations). No `@2x` variants: the source artwork is only 230 px.
HACS's own panel does not render inline brand icons yet
(hacs/integration#5171, #5223), HA's UI does.

## Data source facts (all verified against the live service)

- **Station files** `climate/now/data/{10m,1h}-{WSI}-{YYYYMMDD}.json` are
  rewritten **once per hour** (~HH:02 UTC, newest sample ≈ HH−1:50). Polling
  faster only yields 304s. Missing values are `null` **or an empty string**.
- Element availability is uneven: 475 stations publish data, 432 precipitation,
  296 temperature, 208 wind, 84 pressure, 34 cloud cover (`N`, oktas), 35
  present weather (`ww`, BUFR 0 20 003: 0-99 manned, 100-199 automatic,
  508+ "nothing to report"), 25 global radiation. Never assume an element.
- **Radar PNG** is 680×460 but only the lower-left **598×378** is data
  (`RADAR_CROP`), bbox E 11.267–19.624 / N 48.047–51.458, EPSG:3857,
  1555.68 m/px. The top 16 rows of the data area carry a burned-in ČHMÚ
  caption. Palette indices 181–195 map to 60→4 dBZ in 4 dBZ steps; 242 is grey
  "outside coverage"; the masked product adds desaturated shades for
  precipitation that does not reach the ground.
- **merge1h** (radar + gauges, kriging with external drift) is the product to
  use for a point: `composite/merge1h/hdf5/T_PASV23_C_OKPR_<UTC>.hdf`, ~35 kB,
  `quantity=ACRR`, `gain=0.1`, same 598×378 grid. Read with `pyfive` (pure
  Python). Published **every 10 minutes** but each file is a **60 minute
  window**, timestamp = window **end**, available ~20 min later. Overlapping
  windows must never be summed — only whole hours.
  **The date/time attributes inside the file are UTC**; reading them through the
  local zone shifted every window by two hours (caught by comparing the
  declared window with the requested one — keep that check).
  Accuracy measured over 267 station-hour pairs: mean |error| 0.006 mm at gauge
  locations.
- **Warnings**: `https://vystrahy-cr.chmi.cz/data/XOCZ50_OKPR.xml` (CAP 1.2,
  1.5 MB, ETag). The feed always carries ~36 `<info>` blocks, most of them
  permanent "Žádná výstraha" placeholders (severity Minor + certainty
  Unlikely). Each warning appears once per language (`cs`, `en-GB`) — filter to
  one or it is counted twice. Regions come from the CISORP geocodes, whose
  first two digits are the region (11 Praha … 81 Moravskoslezský); the
  `areaDesc` wording is unreliable ("Aglomerace Brno", "Třinecko", "kraje
  Karlovarský a Ústecký").
- **Text forecast**: `weather/forecast/now/web_pCK{0..4,n}tx_R{region}_DDHHMM.json`
  is forecaster prose (GeoJSON + `data[]` sections), a few times a day. The
  directory index has to be parsed for the newest file per day.
- **No numeric forecast exists** in a usable form: ALADIN is GRIB2 only,
  12–30 MB per variable (CZ 1 km) and 60–100 MB (Lambert), ~120 MB per run.
  Do not revisit this without a new upstream product.
- `climate/recent/data/daily/` (official daily totals, climatological day
  07–07) is updated roughly **monthly** — useless for a "today" sensor, which
  is why daily precipitation is summed from the station's own samples.

## Architecture decisions worth keeping

- **Every coordinator belongs to one config entry.** HA's
  `DataUpdateCoordinator` registers `async_shutdown` on the entry that created
  it, so a coordinator shared between entries is killed for good when that
  entry reloads (any options change) — radar, warnings and forecast froze
  silently. Only `ChmiClient` and `ChmiCatalog` are shared.
- **Entity `unique_id` and the device identifier are the `entry_id`**, never the
  station. The station can be changed through `async_step_reconfigure`, which
  keeps history and rejects a station owned by another entry.
- Home location (`hass.config.latitude/longitude`) is read **on every update**,
  not captured at setup; when it moves, the merged accumulator is cleared.
  Home-location sensors: merged precipitation, radar rain rate, region for
  warnings and forecast. Station-location: everything else, including the radar
  component of the weather condition.
- The HTTP cache is an LRU capped at 16 entries: station and metadata URLs
  contain the date, so an unbounded cache grows forever. Per-frame radar and
  merge URLs are fetched with `cache=False`.
- Values are dropped rather than repeated when stale: observations older than
  3 h, radar frames older than 30 min.
- Weather condition order: station `ww` → precipitation/radar → visibility →
  cloud cover → sunshine → global radiation → `None`. Never guess a condition
  the station cannot support (that is why it stays unknown at night on most
  stations).

## Conventions

- Czech and English translations must stay key-identical
  (`strings.json` == `translations/en.json`, same keys in `cs.json`).
- Fixtures under `tests/fixtures/` are **real captured responses**; trim them
  rather than hand-writing payloads. Station fixtures are dated 2026-09-09, so
  tests patch `dt_util.utcnow` (the 3 h staleness rule would otherwise drop
  everything).
- `ruff check .` must be clean; the config lives in `pyproject.toml`.
- Author work in one pass, review in another: findings from a separate review
  or verification lane (not self-approval) are what caught the coordinator
  shutdown bug, the inflated 3×3 radar sampling and the UTC timestamp bug.
