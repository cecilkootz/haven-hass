# How this API was recovered

## Method

1. **APK acquisition** — the official Haven Lighting Android app
   v2.5.67 (`com.havenhomelighting.havenprouserapp`) was pulled as an
   XAPK from an APK mirror and unpacked.
2. **Bundle extraction** — the app is React Native; its entire business
   logic lives in `assets/index.android.bundle`, a Hermes bytecode file
   (~7 MB).
3. **Disassembly** — the bundle was disassembled with
   [`hermes-dec`](https://github.com/P1sec/hermes-dec) to ~1.4M lines of
   pseudo-JS. The service layer survived minification with original
   names (`LocationsService`, `LightsService`, `ZonesService`,
   `ScenesService`, `HttpService`, `LightCommandBuilder`), which made
   the API layer directly readable.
4. **Live verification** — a real account was used (with the owner's
   permission) to authenticate and exercise endpoints against the
   production API, comparing actual responses with the bytecode-derived
   expectations.

## Verification status

| Endpoint | Source | Status |
|---|---|---|
| `POST /Auth/authenticate` | bytecode | ✅ live-verified (token issued) |
| `GET /user/GetUserInfo/` | bytecode | ✅ live-verified (`defaultLocationId`) |
| `GET /LightAndZones/OrderedList/{id}` | bytecode | ✅ live-verified — **shape differs from the app**: flat top-level list, `id` not `lightId` |
| `POST /v2/Light/CommandV1?locationId=` | bytecode | ✅ live-verified (on/off + brightness with state read-back, legacy host only) |
| `CommandV1` color / pattern fields | bytecode | fills the same live-verified payload keys the app's builder uses |
| `PUT /Light/UpdateName` | bytecode | unverified |
| `GET /Scene/UserScenesByLocation/…` | bytecode | unverified |
| `POST /Commands/Scene/Execute/?sceneId=` | bytecode | unverified |
| Zone CRUD (`v2/Zone/add`, `/Zone`, `Commands/assignZone`, …) | bytecode (routes only) | **unverified — payloads are saga-built and were not traced** |
| Groups, events, sequences, effects, firmware routes | bytecode (routes only) | unverified |

## Live findings that bytecode alone missed

These came from testing against production and would not have surfaced
from the disassembly:

1. **`stg-api` / `stg-legacy-api` are production.** The names suggest
   staging, but the app's PROD `EnvConfig` points at them.
2. **The live `LightAndZones/OrderedList` response is a flat list**, not
   the `{"lights": […], "zones": […]}` object the app's `extractLights`
   reads. The app build in the bundle and the deployed API have
   diverged — a good reminder that bytecode evidence can be stale.
3. **`CommandV1` 404s on the residential host.** The split between
   listing (residential) and commands (legacy) is only visible by
   noticing which services set an explicit `baseURL` and which use the
   default client.
4. **Response writes lag >3 s** in the ordered list.
5. **404s can carry empty bodies**, producing exceptions with empty
   messages — errors now fall back to `HTTP {status}`.

## Bugs this process caught (and fixed)

| Bug | Effect |
|---|---|
| Missing leading `/` on v2 paths | client requested `.../apiLightAndZones/...` → 404, empty error message |
| Pinning the client requirement at a branch ref | branch auto-deleted on merge → pip install fails, HA silently reuses a stale wheel |
| Pinning at a moving ref (`@main`) with an unchanged requirement string | pip skipped the reinstall, HA kept running the old build |
| `ColorMode.COLOR` | removed in HA 2026.11; platform crashed at import until switched to `ColorMode.HS` |

## Reproducing

```sh
# fetch the app
apkeep -a com.havenhomelighting.havenprouserapp -d apk-pure \
  -o 'acknowledge_dangers=true' .

# unpack the XAPK and the base APK
unzip com.havenhomelighting.havenprouserapp@*.xapk
unzip com.havenhomelighting.havenprouserapp.apk -d base

# disassemble the Hermes bundle
pip install hermes-dec
hbc-decompiler base/assets/index.android.bundle disasm.js
```

The interesting modules: the `EnvConfig` table (base URLs), each
service's `API_ROUTES` map, `HttpService` (interceptors — a
pass-through response interceptor), and the model classes
(`LightCommand`, `LightZone`, `Scene`).
