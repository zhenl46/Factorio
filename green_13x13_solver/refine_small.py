#!/usr/bin/env python3
"""Refine a smaller.py layout: everything free, warm-started from it."""
import json, sys
import cell, smaller

src, out, sec = sys.argv[1], sys.argv[2], float(sys.argv[3])
s0 = json.load(open(src))
n = s0.get('n_sci') or sum(1 for mc in s0['machines'] if mc['type'] == 'Gs')
nb = len(s0['beacons'])
smaller.define_types(n, True)
c = smaller.block(s0['W'], s0['H'], n, nb)
c.build()
cell.hint_entities(c, s0)
sol = c.solve(sec, 8)
print(c.status, round(c.solve_time), getattr(c, 'objective', None))
if sol:
    sol['blueprint'] = cell.to_blueprint(sol)
    sol['n_sci'] = n
    json.dump(sol, open(out, 'w'), indent=1)
