# Green science block, 13x13: entity-level CP-SAT layout

A logistic science block for Factorio 2.0 / Space Age (Vulcanus) in a 13x13 footprint. It makes 3.3 science/s from plates, gears and circuits:

- 5 assemblers with 4x productivity module 3 each, every one in range of 2 beacons (2x speed module 3);
- 1 inserter assembler and 1 foundry making the inserters and transport belts the science needs;
- fast inserters and turbo belts, with power poles left out.

`examples/blueprint_13x13.txt` is the version tested in game. `examples/blueprint_13x13_v2.txt` is a later re-route of the same placement with fewer entities (17 inserters, 13 belts, 6 underground pairs, against 15 / 22 / 10). It passes the checker but is not yet tested in game.

## How it works

`cell.py` is a CP-SAT model of one rectangular block on a tile grid. Every assembler, foundry, beacon, inserter, belt and underground belt is a placement decision. It models:

- **Lanes:** each belt lane holds one item type and carries an integer flow, conserved tile by tile, so every item an inserter picks up must come from an input pin or a machine.
- **Belt joins, as in Factorio:** straight feeds, curves (a side feeder that is the tile's only input) and side-loads (both of the feeder's lanes onto the near lane).
- **Undergrounds:** pairing with the nearest exit and the turbo reach (up to 10 tiles between the ends). Inserters can pick from and drop onto underground ends.
- **Inserters:** drops go onto the far lane, never onto curves or parallel belts. Direct insertion between machines is allowed.
- **Machines and beacons:** exact input/output rates for every machine, beacon reach (3 tiles past the beacon edge), a minimum beacon count per science assembler, and the inserter assembler optionally kept out of range.
- **Pins:** inputs on the bottom edge and the output on the top edge; an underground exit may be the output.

`smaller.py` runs the search. A small placement model proposes machine and beacon positions; the full model then routes each one (inserters, belts, undergrounds) with machines fixed. Placements that can't be routed are usually proven so in seconds.

`check_and_draw.py` / `check_small.py` independently re-check a layout. They rebuild belt connections, lanes and flows from the placed entities alone, check overlaps, beacons and rates, and draw a picture.

Rates are integers in units of (3.3 / 1.4) / 300 items/s.

## Usage

```
pip install -r requirements.txt

# route the 13x13 placement (machines fixed) and check it
python3 smaller.py --width 13 --height 13 --sci 5 --beaconed-inserter-machine \
    --layout examples/placement_13x13.json --route-seconds 120 --tries 1 --out out.json
python3 check_small.py out.json out.png --beaconed-inserter-machine

# refine a layout with everything free, warm-started from it
python3 refine_small.py out.json refined.json 270

# search for new placements, e.g. 13x12 with 4 assemblers and 3 beacons
python3 smaller.py --width 13 --height 12 --sci 4 --beaconed-inserter-machine --tries 40 --route-seconds 30
```

CP-SAT is randomised, so runs differ. Routing the same placement twice gave the two example layouts above. More cores help a lot (`num_workers` is 8).

## Notes

- The inserter assembler sits inside one beacon's range in this layout. That only speeds it up, at extra power cost.
- 13x12 was not found: about 65 generated placements were all proven unroutable, which does not prove it impossible.
- Inserter throughput and power are not modelled in this version.
