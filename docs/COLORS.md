# Haven Color Catalog

Extracted from the Haven Lighting Android app (v2.5.67) Hermes bundle.
Every color, white temperature, and animated pattern is an entry with an
id, name, and hex color — and **all of them ride the same `lightColorId`
command field**. The app picks a list based on the controller's
capability level (`standardColorList` vs `extendColorsList`, switched by
`ColorVersion.Extended`); ids are stable across both.

Solid colors and white temperatures surface as `hs_color` in Home
Assistant; animated patterns surface as effects.

## White temperatures

| id | name | hex |
|---|---|---|
| 1 | 2700K | `#F8E96C` |
| 2 | 3000K | `#F6F08E` |
| 3 | 3500K | `#F4F4AC` |
| 4 | 3700K | `#F2F4C2` |
| 5 | 4000K | `#ECF5DA` |
| 6 | 4100K | `#E3F3E9` |
| 7 | 4700K | `#DDF1F2` |
| 8 | 5000K | `#D6EFF6` |

## Solid colors

| id | name | hex |
|---|---|---|
| 11 | RED | `#FF0000` |
| 12 | FIRE | `#FF1500` |
| 13 | PUMPKIN | `#FF3F04` |
| 14 | AMBER | `#FFA65E` |
| 15 | TANGERINE | `#FDCB5A` |
| 16 | MARIGOLD | `#FF9C00` |
| 17 | SUNSET | `#FF7800` |
| 18 | YELLOW | `#FFEA00` |
| 19 | LIME | `#BBFF00` |
| 20 | LIGHT GREEN | `#8CFF00` |
| 21 | GREEN | `#20A301` |
| 22 | SEA FOAM | `#04F57B` |
| 23 | TURQUOISE | `#00C7F3` |
| 24 | OCEAN | `#1E98F3` |
| 25 | DEEP BLUE | `#2160FF` |
| 26 | VIOLET | `#BC63F4` |
| 27 | PURPLE | `#A66AFF` |
| 28 | LAVENDER | `#C26AFF` |
| 29 | PINK | `#FF00EA` |
| 30 | HOT PINK | `#FF008A` |
| 39 | ORANGE | `#FF5E00` |
| 40 | LEMON | `#FFC000` |
| 41 | PALE GREEN | `#59ED02` |
| 42 | EMERALD | `#23BD00` |
| 44 | ARCTIC | `#17E57D` |
| 45 | SKY | `#00FCFF` |
| 46 | WATER | `#00CCFF` |
| 47 | SAPPHIRE | `#217FFF` |
| 48 | LIGHT BLUE | `#2E8FFD` |
| 50 | ORCHID | `#967FFE` |
| 51 | LILAC | `#EC6AFF` |
| 52 | BUBBLEGUM | `#FF00BB` |
| 53 | FLAMINGO | `#ED2693` |
| 54 | DEEP PINK | `#FF3D8F` |
| 55 | AQUA | `#17E5A8` |
| 56 | APPLE GREEN | `#27D600` |
| 57 | ROYAL BLUE | `#6141FF` |

## Animated patterns

| id | name | hex |
|---|---|---|
| 33 | WARM COLORS | `#F8E96C` |
| 34 | COOL COLORS | `#2E8FFD` |
| 35 | ALL COLORS | `#FF0000` |
| 79 | 2 Wave | `#FFFFFF` |
| 80 | Sparkle | `#FFFFFF` |
| 81 | 1 Wave | `#FFFFFF` |
| 83 | Cascade | `#FFFFFF` |
| 84 | 3 Wave | `#FFFFFF` |
| 88 | USA | `#FFFFFF` |

## Special

| id | name | hex |
|---|---|---|
| 4294967295 | OFF | `#515151` |

The app also exposes custom-color creation
(`Color/CustomColorDefinition`), which assigns ids in a
separate range per location; the static catalog above is
what ships in the bundle.
