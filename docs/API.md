# Haven Lighting Cloud API

Everything in this document was recovered by reverse-engineering the
official Haven Lighting Android app, **v2.5.67**
(`com.havenhomelighting.havenprouserapp`), and cross-checked against the
live API where noted. It is the reference the
[`havenlighting`](https://github.com/cecilkootz/haven-python) client and
this integration are built on.

See [REVERSE-ENGINEERING.md](REVERSE-ENGINEERING.md) for how this was
extracted and the verification status of each endpoint.

## Base URLs and environments

The app embeds an `EnvConfig` table; **the "stg" hostnames are what
production points at** (PROD is literally configured with them):

| Config key | Value in PROD | Used for |
|---|---|---|
| `API_URL` | `https://stg-legacy-api.havenlighting.com/api` | default axios client — **all light commands** |
| `RESIDENTIAL_API_URL` | `https://stg-api.havenlighting.com/api` | auth, user info, lights/zones/scenes listing, renames |
| `DEALERS_URL` | `https://stg-api.havenlighting.com/api` | explicit per-request base in most services |
| `ADD_DEVICE_URL` | `https://app-haven-residential.azurewebsites.net` | device onboarding |

DEV variants swap `stg-` for `dev-` (`https://dev-api.havenlighting.com/api`,
`https://dev-legacy-api.havenlighting.com/api`).

**The critical split** (learned the hard way, live-verified): the app's
default HTTP client uses `API_URL` (the *legacy* host), and
`LightsService.sendLightCommand` sets no per-request base — so
**`v2/Light/CommandV1` must be posted to the legacy host**. It returns
**404** on the residential host. Listing endpoints (`LightAndZones`,
`Scene/UserScenesByLocation`) use the residential host.

## Request shape

- All paths are appended to the base as `{base}{path}` — **paths must
  carry a leading slash** (`/LightAndZones/OrderedList/123`). Missing it
  silently produces `.../apiLightAndZones/...` → 404 with an empty body.
- Headers: `Authorization: Bearer {token}`,
  `app-haven-source: REACT_NATIVE`, `app-haven-version: 1.6`,
  `Accept: application/json`.
- Azure Front Door fronts the API and **blocks the TLS fingerprint of
  plain `requests`/`urllib3`** (403 "request is blocked") but passes
  browser fingerprints — the client uses `curl_cffi` with
  `impersonate="chrome"`.
- Success for mutating calls is signaled by `{"success": true}` in the
  body (the app's `validateResponseAPI`), not just a 200.

## Authentication

### `POST /Auth/authenticate` — residential base
Request:
```json
{"username": "user@example.com", "password": "..."}
```
Response:
```json
{"id": 123, "token": "jwt...", "refreshToken": "...", "userType": 1}
```

### `POST /Auth/refresh`
```json
{"refreshToken": "...", "userId": 123}
```
Same response shape. The app refreshes on 401 and retries once.

### `GET /user/GetUserInfo/` — residential base
Returns the user's profile, notably **`defaultLocationId`** plus
`firstName` / `lastName`. (The app's older multi-location endpoints are
decommissioned on this API.)

## Enum constants

| Constant | Value | Where |
|---|---|---|
| `lightOff` | `1` | `lightingStatusId` |
| `lightOn` | `2` | `lightingStatusId` |
| `keepSame` | `63` | `lightingStatusId`, `lightBrightnessId`, `lightColorId`, `patternSpeedId` |
| Scene type | `"BOTH"` / `"STATIC"` / `"PLAYGROUND"` | `sceneType` query param (string enum) |

Brightness is a **0–10** scale (`lightBrightnessId`); pattern speed is
**1–63**. See [COLORS.md](COLORS.md) for the full color/pattern catalog
(all of it rides `lightColorId`).

## Lights and zones

### `GET /LightAndZones/OrderedList/{locationId}` — residential base

Live-verified. Returns a **flat top-level list** — not the
`{"lights": [...], "zones": [...]}` object the app's `extractLights`
expects from `response.data` (the live API differs from the app build).
An account can return zone entries, individual lights, or both.

Item schema (live capture, 28 fields):

| Field | Type | Meaning |
|---|---|---|
| `id` | int | entity id (zones); individual lights use `lightId` |
| `name` | str | display name |
| `isZone` | bool | zone vs individual light |
| `zoneNumber` | int | zone index (0 = the "ALL ZONES" group) |
| `orderNumber` | int | UI ordering |
| `lightingStatusId` | int | 1 = off, 2 = on |
| `lightBrightnessId` | int | brightness 0–10 |
| `colorId` | int | current color/pattern (see COLORS.md) |
| `colorName` | str | color display name |
| `patternSpeed` | int | pattern animation speed 1–63 |
| `isHidden` | bool | hidden in the app's light list |
| `isConnected` / `connectionState` | bool/str | controller connectivity |
| `controllerName` / `controllerTypeName` / `macAddress` / `vendorName` | str | hardware info |
| `typeName` / `lightTypeId` / `lightTypeName` | — | product type ("FULL COLOR", "Classic White", …) |
| `enabled` | bool | channel enabled |
| `isOn` | bool | duplicate of lightingStatusId == 2 |
| `lastUpdated` | — | timestamp |
| `pinName` | — | pin/label |
| `serviceRequired` | bool | service flag |
| `sparkleDuration` / `whiteId` | — | pattern extras |
| `capabilities` | — | capability list |
| `changeReason` | str | last change reason |
| `locationId` | int | owning location |

**Write lag:** the ordered list reflects state changes with a lag of
several seconds (observed >3 s) after a command returns success.

### `POST /v2/Light/CommandV1?locationId={locationId}` — **legacy base**

The single command endpoint for on/off, brightness, color, and patterns.
Payload (from the app's `LightCommand` class / `LightCommandBuilder`):

```json
{
  "locationId": 123,
  "selectedLightIds": [456],
  "selectedZoneIds": [],
  "lightingStatusId": 2,
  "lightBrightnessId": 63,
  "lightColorId": 63,
  "patternSpeedId": 63,
  "whiteId": null,
  "sparkleDuration": null
}
```

- Lights go in `selectedLightIds`, zones in `selectedZoneIds` (the app's
  `withIds(ids, isZone)`).
- `2` = on, `1` = off, `63` = leave unchanged (per field).
- `lightColorId` carries **either** a solid color **or** a pattern from
  the catalog in COLORS.md.
- Response: `{"success": true}` — the only success signal.

Live-verified: on/off and brightness on a real zone, with state
read-back through the ordered list.

### `POST /v2/Light/CommandToAllLightsZones?locationId={id}` — legacy base
All-lights variant of the same payload (the "ALL ZONES" entity in the
ordered list also covers this per-location).

### `PUT /Light/UpdateName` — residential base
Rename an individual light (app: `LightsService.updateLight`):
```json
{"lightId": 456, "name": "New Name", "pinName": null}
```
Zone renames go through a different, saga-built call (see "Unverified"
below) — do not send a zone id as `lightId`.

## Scenes

### `GET /Scene/UserScenesByLocation/?locationId={id}&sceneType=BOTH` — residential base
`sceneType` is the string enum `"BOTH"` / `"STATIC"` / `"PLAYGROUND"`
(the app's `SceneType`). Item shape (app's `mapScene`):

| Field | Type |
|---|---|
| `id` (or `sceneId`) | int |
| `name` | str |
| `photoUrl` | str/null |
| `preferedScene` | bool (sic — Haven's spelling) |
| `isPlayGroundScene` | bool |

### `POST /Commands/Scene/Execute/?sceneId={id}` — residential base
Empty JSON body `{}`. Response: `{"success": true}`.

### Other scene routes (from the app, unverified)
- `POST /v2/Scene/CreateCurrentState/` — create a scene from the current
  light state (payload is saga-built; **not implemented** in the client)
- `POST /v2/Scene/Reset` — update scene config
- `DELETE /Scene` (route `DELETE_SCENE: "Scene"`)
- `GET /Scene/SceneDetails/{id}`

## Zones (management — routes only, unverified payloads)

From the app's `ZonesService`; all on the residential base:

| Operation | Route |
|---|---|
| Create zone | `POST /v2/Zone/add` |
| Create + assign lights | `POST /Zone/AddAndAssign` |
| Update/rename zone | `POST /Zone` |
| Assign lights to zone | `POST /Commands/assignZone` (payload includes `zoneId`) |
| Delete zone | `POST /v2/Zone/Delete/` |
| Hidden zones | `GET /Zone/Hidden` |
| Zone pin name | `POST /v2/Zone/SetZonePinName` |

Payload bodies are built in the app's redux sagas and were not traced —
they need live verification before the client implements zone CRUD.

## Locations

| Operation | Route |
|---|---|
| Default location | `GET /user/GetUserInfo/` (→ `defaultLocationId`) |
| Search locations | `GET /Location/OrderedLocation?page=&lat=&lon=` |
| Add-device locations | `GET /v2/Location/UserBasicInformationLocations?q=` |
| Set default | `POST /Location/SetDefaultLocation` |
| Add location | `POST /v2/Location/add` |
| Rename location | `POST /Location/Name` |
| Share/remove user | `POST /v2/Location/AddUserShare`, `DELETE /Location/LocationUser` |
| Transfer ownership | `POST /Location/TransferLocationUsingEmail` |
| Pro/non-pro switch | `POST /Location/SwitchProNonPro` |

## Groups

| Operation | Route |
|---|---|
| List groups | `GET /v2/Group/AllGroupsByLocation` / `GET /Group/AllGroupsByLocation` |
| Group command | `POST /v2/Group/ExecuteCommand` |

## Events / schedules

| Operation | Route |
|---|---|
| List events | `GET /Event/EventLightZones` |
| Create/update | `POST /v2/Event/EventLightsZones`, `POST /Event/EventLightsZones` |
| Event details | `GET /Event/LightZoneEventDetails` |
| Execute now | `POST /Commands/Event/Execute` |
| Enable/disable | `POST /Event/SetStatus/{id}` |
| Delete | `DELETE /Event/Delete` |
| Reorder | `POST /Event/ChangeOrder` |
| Executable by date | `GET /Event/EventsToExecuteByDate` |
| Event on/off states | `GET /Event/On`, `GET /Event/Off` |

Event model fields (from the app): `sceneId`, `status`, `timeType`,
`hour`, `minute`, `daysOfWeek`, `offsetMinutes`, `localExecutionTime`,
`isHighPrecedence`, `summary`, plus holiday date/offset fields.

## Sequences and effects (light shows)

| Operation | Route |
|---|---|
| Sequences | `GET/POST /Sequence`, execute `POST /Commands/Sequence/Execute`, resave `POST /Sequence/Resave/Location` |
| Effects list | `GET /Effect/ByLocation` |
| Effect by id | `GET /Effect/ById` |
| Save/share/publish | `POST /Effect/SaveToLocation`, `/Effect/ShareEffect`, `/Effect/PublishToStore` |
| Comets/patterns | `GET /Effect/Comets`, `GET /Effect/Pattern` |

## Devices and firmware

| Operation | Route |
|---|---|
| Location devices | `GET /v2/Device/LocationDevices` |
| Add device | `POST /Device/AddDevice` |
| Connection state | `GET /Device/ConnectionState` |
| Firmware versions | `GET /v2/Firmware/GetFirmwareVersions` |
| Schedule upgrade | `POST /v2/Firmware/ScheduleFirmwareUpgrade` |
| Blink controller | `POST /v2/Light/BlinkController` |
| Hard reboot | `POST /v2/device/HardRebootByLocationId` |
| Set Wi-Fi credentials | `POST /Commands/SetWifiCredentials` |
| Test clock sync / loops | `POST /v2/Device/ExecuteTestClockSyncByLocation`, `/v2/Device/LoopStartByLocationId`, `/v2/Device/LoopStopByLocationId` |

## Color catalog

See [COLORS.md](COLORS.md) — the full 55-entry id/name/hex table
extracted from the app bundle (white temperatures, solid colors, and
animated patterns all ride `lightColorId`).

## Known gotchas

1. **"stg" hostnames are production** — the app's PROD config points at
   `stg-api` / `stg-legacy-api`.
2. **Commands live on the legacy host**; listing lives on the
   residential host. CommandV1 404s on the residential host.
3. **Paths need a leading slash** — the client concatenates
   `base + path` with no separator.
4. **Empty error bodies exist** — a 404 can return an empty body, so
   error messages must fall back to `HTTP {status}`.
5. **State writes lag** several seconds in `LightAndZones/OrderedList`.
6. **The WAF fingerprints TLS** — plain Python HTTP libraries get 403.
