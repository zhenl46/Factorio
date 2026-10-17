# block_solver: entity-level layout for any Factorio sub-block

A general version of `green_13x13_solver/`. You describe a sub-block in a JSON spec: its size, the machines and recipes inside, modules and beacons, and its pins. The solver places every machine, beacon, inserter, belt, underground, pipe and pipe-to-ground, then exports a blueprint string. An independent checker re-derives the whole design from Factorio's rules and reports anything wrong.

```
pip install ortools matplotlib
python3 solve.py search specs/red.json --budget 600 --out red          # propose placements, route each
python3 solve.py route  specs/red.json --placement specs/red_placement.json --soft --seconds 600 --out red
python3 solve.py check  specs/green_13x13.json --blueprint tests/blueprints/green_13x13_tested.txt --out g
python3 -m pytest -q tests        # or: python3 tests/test_block_solver.py
```

Each run writes `OUT.json` (layout), `OUT.txt` (blueprint string) and `OUT.png` (picture), and prints the checker's report.

## What it models

| Mechanic | How |
|---|---|
| Items on belts | Two lanes per tile, one item per lane, integer flows conserved tile by tile. |
| Belt joins | Straight, curve (a side feeder that is the tile's only input) or side-load (both feeder lanes onto the near lane), as in game. A dummy belt behind a tile forces a side-load. |
| Underground belts, weaving | Any mix of tiers (`transport`, `fast`, `express`, `turbo`; reach 5/7/9/11). An entrance pairs with the nearest exit of its own tier; only same-tier undergrounds on the same axis block it, so different tiers weave. |
| Inserters | Any kind in the spec; `long-handed-inserter` reaches 2 tiles. Pick from belt lanes, underground ends or machines. Drop on the far lane, into machines, or directly machine to machine. Never onto curves or along a belt. |
| Pipes | Every pipe connects to every neighbouring pipe, so touching pipes must carry the same fluid. A connectivity flow proves every used fluid box reaches a source (pin or producing machine), and every output box reaches a sink. |
| Pipe-to-ground | One open face plus an underground link to the nearest pipe-to-ground on the axis (reach 10). Passes under blocked tiles and belts. |
| Machine rotation | Machines whose recipe uses fluids can face any of 4 directions; fluid ports move with them. Recipe fluids go to boxes in prototype order (with the game's explicit overrides). Unused boxes must not touch pipes, and two machines' ports must not face each other. |
| Rates | Crafting speed and productivity from FactorioLab data, with modules and beacons (2.0 diminishing beacon profile). Each machine gets a crafting-rate variable up to its maximum; inputs and outputs follow the recipe. Machines may idle partly, so input pins are "up to" and output pins "at least". |
| Beacons | Minimum per machine group (`beacons`), or kept out of range (`no_beacon`). |
| Blocked tiles | `blocked` tiles hold nothing, but undergrounds and pipe-to-ground pass under them. |

Geometry (fluid ports, reaches) comes from `data/entities.json`, extracted from `wube/factorio-data`. Recipes, machines and modules come from `data/recipes.json`, trimmed from FactorioLab's Space Age dataset.

## Spec format

See `specs/red.json` and `specs/green_13x13.json`. Coordinates: x east, y south, (0, 0) top-left. A pin has a `side` (`top`, `bottom`, `left`, `right`) and `at` (index along that side, a `[lo, hi]` range, or omitted to let the solver choose). Belt pins carry one or two items (one per lane; `lanes` fixes the order). Pipe pins carry one fluid. `fixed` pins machines in place, as used by tests.

Your floorplanner uses 1-based coordinates with y = 1 at the bottom, so a pin at floorplanner `(x, y)` on a block of height H becomes `side: bottom/top`, `at: x - 1`; internally the tile is y = H - y_floorplanner.

## How to get good results

- **Route a sensible placement** (`route --placement`). Searching placements and routing everything at once is far harder. On the 2-core machine this was built on, a well-chosen placement routed in minutes, while blind search mostly failed.
- **Use `--soft` for hard blocks.** Output targets become heavily penalised shortfalls, so the solver always has a layout and climbs towards the full rate. The checker reports any remaining shortfall.
- **Continue a run.** Pass `route --layout earlier.json` to keep machines fixed and warm-start from an earlier routing.
- **More cores help a lot.** CP-SAT runs a portfolio of search strategies in parallel; `--workers 16` on a desktop CPU should do much better than 2 cores.

## Not modelled yet

- Splitters. One belt line cannot branch, so it must snake past every consumer. This is the main limit on blocks with many consumers of the same items.
- Pumps, inserter throughput (an option, off by default), power poles, non-square machines (recycler, crusher), entity mirroring, and fluid throughput (2.0 pipe segments carry far more than these blocks need).
- Conservative rules: no dropping along a belt (lane uncertain); no same-tier undergrounds between a pair, whatever their direction; no side-loading onto underground entrances; every pipe-to-ground paired.

## Results so far

- **Tests** (`tests/`):
  - Your in-game 13x13 and 13x14 green blueprints are accepted unchanged.
  - Pipes, pipe-to-ground, long-handed inserters and weaving each have a test that forces the mechanic.
  - Weaving with a single tier is proven impossible.
  - The checker catches mixed fluids and pipes touching unused ports.
- **Red block** (`specs/red.json`, 13x16, pins where the floorplanner puts them): fully solved in `results/` (layout, picture, blueprint string).
  - Delivers 3.3 red science/s plus 3.6 gears/s and 3.6 copper/s on the side belt.
  - Uses 4 science assemblers (4x prod 3, two beacons each), one gear foundry and one copper foundry, each foundry seeing a beacon.
  - 12 inserters (one long-handed), 10 belts, 9 turbo/express underground pairs, 3 pipes.
  - Found with `route --soft` from `specs/red_placement.json`: about 9 minutes reached 2.65/s, and a 9-minute continuation from that result reached the full rate.
  - The gear/copper rates are placeholders; edit the spec for the real numbers.
- **Mechanics tests**: pictures and blueprints in `results/tests/`.
