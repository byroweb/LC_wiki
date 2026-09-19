# Lost City Wiki, Knowledge Graph and Map Explorer

A generated wiki, knowledge graph and minimap-style map explorer for the
[Lost City](https://lostcity.rs) server (RuneScape, revision 254, September 7 2004),
built from the `LostCityRS/Content` repository.

```
LC_wiki/
  source/          cloned LostCityRS repos (Content, Engine-TS and Client-TS on the same revision branch (currently 254)) -- not in this repo, see below
  build/           Python generator (run this to rebuild)
  site/            generated static site (open site/index.html)
```

## Getting the game data

`source/` is not part of this repository: it holds three LostCityRS checkouts that
are git repositories of their own. Clone them on the revision branch before building:

```bash
git clone -b 254 https://github.com/LostCityRS/Content   source/content
git clone -b 254 https://github.com/LostCityRS/Engine-TS source/engine
git clone -b 254 https://github.com/LostCityRS/Client-TS source/client
```

Only `source/content` is needed to rebuild the wiki and the tables; the engine
and client are used to render item and NPC icons (see `update.bat`).

## Rebuilding

Requirements: Python 3.10+ with Pillow (`pip install pillow`).

```bash
python build/build.py              # full build (renders map tiles the first time, ~20 s)
python build/build.py --skip-tiles # rebuild pages/data only, reuse existing tiles
python build/build.py --force-tiles # re-render every map tile
python build/build.py --tiles-only  # only render map tiles
```

To update the game data: `git -C source/content pull` and rebuild with `--force-tiles`.

### Building from another Content checkout, and the data tables

```bash
python build/build.py --content path/to/Content              # any Content checkout (revision stamped in every output)
python build/build.py --content path/to/Content --tables-only  # only regenerate site/data/tables/*.json (+ validate)
update.bat                                                       # pull the content and rebuild everything
```

`site/data/tables/` holds the machine-readable tables
(areas, recipes, item sources, NPC safety, landmarks, resource stands, chat
verdicts). See `site/data/tables/README.md`. `build/validate_tables.py` checks
every reference against the content and fails the build if anything dangles.

What a landmark "requires" is read from the script rather than from the words in
it. `tables.py` walks each `[oploc*]` handler and the labels and procs it
reaches, splits every if/else chain into branches, and keeps a condition only when
it would actually stop a player who has done nothing: varps read as 0, skills as 1,
`^constants` resolved from the `.constant` files, an empty inventory. A branch
counts either because it turns you away (a `return`, or a jump to a label that
never opens the door) or because it is the only route that opens the door or moves
you. So the Champions' Guild door asks for 32 quest points and the Zanaris door for
a dramen staff, while the Port Sarim jeweller and the Varrock east gate, which
mention quest varps but let you through regardless, ask for nothing.

### Item icons (optional, needs bun)

Item icons in `site/icons/items/<id>.png` are rendered with the web client's own
model renderer, so they match the in-game inventory sprites. To regenerate them:

```bash
cd source/engine && bun install && bun run tools/pack/Build.ts   # pack the cache once
cd ../client && bun install && bun run ../../build/icons.ts       # render 32x32 item icons
bun run ../../build/heads.ts                                      # render 48x48 NPC icons
cd ../.. && python build/build.py --skip-tiles                    # pick them up
```

NPC icons (`site/icons/npcs/<id>.png`) are the in-game chathead (framed like the
chat interface: zoom 796, pitch 40, yaw 166) when the NPC has head models, and a
front view of the body model otherwise. `kinds.json` in that folder says which.

(`source/client` is `LostCityRS/Client-TS` on the same branch as the content.)

## Viewing

Double-click `serve.bat` (or run `python -m http.server 8765 --directory site`)
and open <http://localhost:8765>. Opening `site/index.html` directly from disk
also works in most browsers because every data file is a plain `.js` file.

- `site/index.html` - wiki home, links to every index.
- `site/map.html` - map explorer. Yellow dots are NPC spawns, red dots are ground
  item spawns, icons are the in-game map functions (banks, shops, altars...).
  Click a dot to expand it: name, combat level, description, stats, the top
  drops with rates, and a link to the wiki page. Deep links:
  `map.html#x=3036&z=3695&level=0&zoom=6`, `map.html#npc=202`, `map.html#obj=1982`.
  Cyan squares are entrances (ladders, stairs, trapdoors, caves, shortcuts).
  Clicking one, or a dungeon `!` icon, moves the map to where it leads (e.g.
  into the dungeon underneath) and offers a Back button. Destinations are
  resolved by `build/portals.py`, which walks each entrance's `[oploc*]`
  script (`p_teleport`/`p_telejump`, `movecoord`, `switch_coord`,
  `~climb_ladder`, `loc_change`, coordinate constants...).
  Two-way ladders show stacked up/down buttons. Clicking a mining-site
  (pickaxe) icon lists the ores of the rocks within 16 tiles, with the level
  needed and how many rocks there are (from the `mining_table` db rows).
- `site/graph.html` - interactive ego-network view of the knowledge graph, with
  a Region filter (Wilderness, Kingdom of Kandarin, Underground...). Regions come
  from the map labels; the Wilderness is the coordinate box the content uses, and
  underground places carry both "Underground" and the region above them, so the
  Wilderness filter includes its dungeons. Dungeon spawns get derived areas such
  as "Edgeville (underground)".
- `site/chunks.html` - chunk picker. The world is split into the game's own 64x64
  map squares; click squares to unlock them and the page works out what a player
  who starts with **nothing** could actually do with that set. It is a fixpoint:
  ground spawns and NPC drops seed your items, coins let you buy from shops inside
  your chunks, and any recipe whose tool, ingredients and station are all reachable
  adds its product, which can in turn unlock more. So 13 iron rocks with no
  pickaxe in reach are reported as blocked ("needs one of Bronze pickaxe, Iron
  pickaxe, ..."), and unlocking a chunk that sells one puts them back on the menu.
  A dungeon belongs to the square above it, and every floor of a square counts.
  Skill levels are part of the reasoning: you start every skill at 1, and a skill
  only opens up if something in your chunks can be done at level 1 and gives xp.
  So gems sitting in a chunk with no level-1 crafting are reported as "needs
  crafting level 20, and nothing here trains crafting" rather than listed as a task,
  and iron rocks with no copper or tin nearby are the same for mining.
  The rare drop tables (`~ultrarare_getitem`, `~megararetable`), anything rarer than
  1/512, and random-event items and NPCs (`scripts/macro events/`) are left out: a
  run cannot be planned around them. Items and monsters have checkboxes so you can
  tick them off as you go (remembered in the browser), and no list is truncated.
  Tasks under "What you can do" have checkboxes as well.
  Content behind a barrier is separated out: an underground region whose every way
  in asks you to carry something (Zanaris behind the dramen staff, Gu'Tanoth behind
  the skavid map) becomes its own part of its chunk and only counts once the closure
  can supply the key. Steps the scripts define are recipes too, so chains work:
  cut a dramen branch at the tree with an axe, carve it with a knife at crafting 31,
  and Zanaris opens by itself. A step whose script checks a quest varp needs every
  chunk that quest's NPCs live in.
  A locked door shuts off exactly the room behind it, traced from the walls in the
  map files: flood out from each side of the door, keep the side that stays enclosed
  (doors anyone can open are left passable, so a building with a second way in is not
  treated as locked), and take in the wall line as well, because an upper floor is
  built out over it. That covers a whole guild whatever shape it is, and nothing
  outside it, so all three Ranging Guild shops sit behind the ranged door and Scavvo's
  rune shop above the Champions' Guild wall sits behind its quest-point door, while
  the shop next door to a quest room stays open.
  So the Heroes' Guild shop (and its dragon battleaxe) stays out of reach until the
  chunks holding the Hero's Quest NPCs are unlocked, and the Legends' Guild until
  Legends Quest. A door that checks a skill level locks its guild the same way, and
  opens only when that skill can be trained from level 1 inside your chunks: the
  Fishing Guild (fishing 68) stays shut until you hold a net and a level-1 fishing
  spot, and likewise the Wizards' (magic 66), Mining (60), Ranging (40), Crafting
  (40, plus a brown apron) and Cooks' (32, plus a chef's hat) guilds. A door that
  asks for quest points (the Champions' Guild wants 32) counts what your chunks can
  actually earn, summing the point value of every quest whose NPCs all live in
  unlocked chunks, and reports the running total.
  A best-in-slot table shows the best obtainable item for each equipment slot in
  each combat style, ranked on that style's attack bonus (plus strength for melee,
  ranged strength for ranged) and then total defence, all read from the item
  configs; it can be limited to items you have ticked off. A "free-to-play items
  only" toggle drops members items from the whole calculation, which matters
  because Zanaris sits in the underground band of the Lumbridge Swamp square, so
  that chunk otherwise hands you a dragon weapon shop. The unlocked set lives in the URL, so a run can be
  shared, and it is also kept in the browser so following a link off the page and coming back does not lose
  it. A shared link wins over your own run and only replaces it once you change something; Reset clears the
  set outright, spawn chunk included, and that empty state is what you come back to. Reset clears the ticks
  as well, so a new run does not start with the last one's items already crossed off; "clear ticks" still
  clears just those.
  Data: `build/chunks.py` -> `site/data/chunks.js`.
- `site/gear.html` - equipment builder. Click a slot to fill it, set your levels
  and prayers, and the page works the fight out with the server's own combat
  scripts: the equipment bonuses `~equip_get_bonuses` would add up, the attack
  style table the weapon's category opens (Chop/Slash/Lunge/Block, Accurate/
  Rapid/Longrange...), the attack rate in ticks (rapid takes one off), and the
  max hit from `~combat_maxhit`. Pick a monster and it works out the damage a
  second each way: a swing lands when `randominc(attack roll) > randominc(defence
  roll)`, which over two uniform rolls is `a/(2(d+1))` when `a <= d` and
  `(2a-d)/(2(a+1))` when it is not, and a hit that lands averages half the max.
  Each monster's attack profile comes from walking its `[ai_opplayer2]` /
  `[ai_applayer2]` handler and weighting the `random(n)` branches, so a dragon
  is 1/4 dragonfire and 3/4 melee and the King Black Dragon splits three ways.
  Dragonfire is modelled per dragon out of its own breath proc, including what
  the anti-dragon shield, Protect from Magic and an antifire potion each take off
  (green dragon: max 30, 50 when its roll beats your defence, 10 under the
  prayer, 5 behind the shield, and 15 off any of those with the potion), so the
  page can say 16.02 average damage a breath bare-headed and 2.50 behind the
  shield. Where a handler branches on something that is not a die roll the shares
  are split evenly and the monster is marked as such. "Until you drop" counts the
  hitpoints `[timer,health_regen]` puts back (1 every 100 ticks), so anything
  hitting for less than that never kills you. The whole set-up lives in the URL,
  so a build can be shared.
  The pickers leave out the trimmed, gold and charged copies of an item: a
  gold-trimmed rune platebody has exactly the stats of a plain one, so listing
  both only says the same thing twice. A copy is folded away only when its name
  carries a parenthetical *and* another item reads identically on every number
  these pages use, which keeps a silver sickle(b) (+5 prayer) and a bronze
  spear(p) (a different stab bonus), and keeps every item whose name is its own,
  so a Saradomin platebody stays listed beside the rune one it copies. Quest
  requirements are recorded but not checked; only level gates are.
  Data: `build/gear.py` -> `site/data/gear.js`.
- `site/compare.html` - weapon comparison. Two weapons, a style each, one shared
  kit, and the damage a second both deal to every monster in the game, sortable
  and filterable. The head-to-head splits the answer into the two things that
  decide it and usually pull opposite ways: **throughput**, the max-hit ratio
  over the speed ratio, which is what a slower, harder-hitting weapon gives up
  before accuracy is counted, and **accuracy**, the attack-roll ratio, which is
  what it has to win back and which pays less the closer you already are to
  hitting every swing. It then binary-searches the defence roll where the lead
  changes hands. So a dragon longsword's strength bonus is worth exactly its
  speed penalty - `(71+64)/(44+64)` is `135/108`, which is `1.25`, which is `5/4`
  - leaving accuracy as its whole advantage over a rune scimitar, and the lead
  flips on soft targets at high attack. A rune sword on stab only beats a rune
  scimitar on slash when the monster's slash defence exceeds its stab defence by
  about 6, which is most of the dragons; and since no item outside the weapon
  slot has a different stab, slash or crush attack bonus, no kit can tilt any of
  that. Both pages share their arithmetic through `build/static/combat.js` ->
  `site/assets/combat.js` and read the same `site/data/gear.js`.
- `site/data/graph.json` - the raw knowledge graph (nodes + typed edges).

## What is in the knowledge graph

| Node type | Source |
|-----------|--------|
| npc       | `scripts/**/*.npc` configs (+ ids from `pack/npc.pack`) |
| item      | `scripts/**/*.obj` configs |
| area      | `maps/labels.txt` map labels |
| quest     | `scripts/quests/quest_*/` folders (name from the "Quest complete" log line) |
| shop      | `.inv` configs referenced by an NPC's `owned_shop` param |
| table     | shared drop procs (`~randomherb`, `~randomjewel`, ...) and `drop_table` db rows |
| category  | NPC `category=` values |

| Edge type   | Meaning |
|-------------|---------|
| drops       | NPC -> item, label is the rarity (e.g. `1/128`) |
| rolls       | NPC or table -> sub-table |
| contains    | table -> item |
| spawns_in   | NPC -> area (from the `.jm2` NPC sections) |
| found_in    | item -> area (ground spawns from the `.jm2` OBJ sections) |
| runs / sells| NPC -> shop, shop -> item |
| involves    | quest -> NPC / item (identifier references inside the quest scripts) |
| in_category | NPC -> category |

Drop tables are produced by interpreting the RuneScript death handlers
(`[ai_queue3,<npc>]` or `[ai_queue3,_<category>]`): `random(N)` plus
`if/else if` chains and `switch` statements become probabilities, `~proc`
values become linked sub-tables, `map_members` checks become "members"
notes, and `~trail_*cluedrop` calls become tertiary clue-scroll drops.

## Map rendering

`build/render.py` draws each 64x64 map square at 4 px per tile the way the
2004 client draws its minimap: blurred underlay floor colours, overlay shapes
using the client's 13 tile masks, walls in white (doors/interactive locs in
red), and the `mapscene` sprites for trees and rocks. Bridged tiles use the
level above, as in the original renderer.

## Known limitations

- Drop rates are exact for the common `random(128)` if-chain pattern. Branches
  guarded by non-random conditions keep the parent probability and carry an
  `if ...` note; loops are marked "in loop".
- NPCs that are only spawned by scripts (quest bosses, random events) have no
  map dots.
- Area assignment is "nearest map label", so some NPCs are attributed to the
  closest named place rather than an official region.
