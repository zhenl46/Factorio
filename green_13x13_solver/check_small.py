#!/usr/bin/env python3
"""Independent check (and picture) for the smaller-footprint searches (smaller.py units)."""
import argparse
import json

import cell
import smaller
import check_and_draw as cd

ap = argparse.ArgumentParser()
ap.add_argument('layout')
ap.add_argument('picture', nargs='?')
ap.add_argument('--beaconed-inserter-machine', action='store_true')
args = ap.parse_args()
sol = json.load(open(args.layout))
n = sol.get('n_sci') or sum(1 for mc in sol['machines'] if mc['type'] == 'Gs')
smaller.define_types(n, args.beaconed_inserter_machine)
cd.LANE_CAP = cell.LANE_CAP
cd.COUNTS = {'Gs': n, 'Is': 1, 'Fs': 1}
cd.PIN_FG = {'plate': 450, 'gear': 450}
cd.PIN_C = 300
cd.EXIT_GREEN = 420
cd.CHECK_POWER = False
cd.CHECK_INSERTER_RATE = False
cd.MAX_BEACONS = smaller.BEACONS_FOR[n] + 2
sol.setdefault('poles', [])
errs, lanes = cd.check(sol)
nb = len(sol['beacons'])
print('OK: no problems found' if not errs else '\n'.join(errs))
print(f"{sol['W']}x{sol['H']}: {n} science assemblers, {nb} beacons, {len(sol['inserters'])} inserters, "
      f"{len(sol['belts'])} belts, {len(sol['undergrounds'])} underground pairs; pins {sol['pins']}")
if args.picture:
    cd.draw(sol, lanes, args.picture)
