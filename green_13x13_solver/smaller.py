#!/usr/bin/env python3
"""Search for a green block smaller than 13x14 using more beacons and fewer science assemblers.

With 4x productivity module 3 per assembler, n beacons (2x speed module 3 each) give
  n=1: 6 assemblers   n=2: 5 assemblers   n=3: 4 assemblers.
Flow: a small CP-SAT placer proposes machine and beacon positions that satisfy the beacon
counts and leave every machine some free face tiles; the full entity model then routes
each proposal (inserters, belts, undergrounds, side-loads) with machines fixed.
"""
import argparse
import json
import random
import sys
import time

from ortools.sat.python import cp_model

import cell

U = (3.3 / 1.4) / 300              # items/s per unit: 300 units = 2.357 inserters/s
cell.BELT_TIER = 'turbo'
cell.UG_MAX = 11
cell.LANE_CAP = int(30 / U)
cell.INS_CAP = cell.LANE_CAP       # inserter throughput ignored
R = cell.BEACON_RANGE

BEACONS_FOR = {6: 1, 5: 2, 4: 3}


def define_types(n_sci, beaconed_inserter_machine):
    cell.MACHINE_TYPES['Gs'] = dict(size=3, entity='assembling-machine-3', recipe='logistic-science-pack',
                                    needs={'inserter': 300 // n_sci, 'belt': 300 // n_sci}, out=('green', 420 // n_sci),
                                    modules={'productivity-module-3': 4}, min_beacons=BEACONS_FOR[n_sci])
    cell.MACHINE_TYPES['Is'] = dict(size=3, entity='assembling-machine-3', recipe='inserter',
                                    needs={'plate': 300, 'gear': 300, 'circuit': 300}, out=('inserter', 300),
                                    modules={'efficiency-module-2': 2}, no_beacon=not beaconed_inserter_machine)
    cell.MACHINE_TYPES['Fs'] = dict(size=5, entity='foundry', recipe='transport-belt',
                                    needs={'plate': 150, 'gear': 150}, out=('belt', 300),
                                    modules={'efficiency-module-2': 2})


def block(W, H, n_sci, n_beacons, pins=None):
    fg, circ, out = pins or (None, None, None)
    return cell.Cell(W, H, {'Gs': n_sci, 'Is': 1, 'Fs': 1},
                     in_pins=[dict(name='fg', items={'plate': 450, 'gear': 450}, x=fg),
                              dict(name='circuit', items={'circuit': 300}, x=circ)],
                     out_pins=[dict(name='science', items={'green': 420}, x=out)],
                     max_beacons=n_beacons, poles=False)


def in_range(a, s, b):
    return a[0] <= b[0] + 2 + R and a[0] + s - 1 >= b[0] - R and a[1] <= b[1] + 2 + R and a[1] + s - 1 >= b[1] - R


def placer(W, H, n_sci, n_beacons, beaconed_inserter_machine, seed, banned, seconds=15):
    """Machine/beacon placement with per-machine free-face requirements."""
    m = cp_model.CpModel()
    rng = random.Random(seed)
    tiles = [(x, y) for y in range(H) for x in range(W)]
    kinds = {'G': (3, n_sci), 'B': (3, n_beacons), 'I': (3, 1), 'F': (5, 1)}
    v = {}
    for k, (s, n) in kinds.items():
        v[k] = {(x, y): m.NewBoolVar('') for y in range(H - s + 1) for x in range(W - s + 1)}
        m.Add(sum(v[k].values()) == n)
    occ = {}
    for t in tiles:
        cov = [b for k, (s, _) in kinds.items() for a, b in v[k].items()
               if a[0] <= t[0] < a[0] + s and a[1] <= t[1] < a[1] + s]
        o = m.NewBoolVar('')
        m.Add(o == sum(cov))
        occ[t] = o
    free = {t: occ[t].Not() for t in tiles}
    # beacons per science assembler, and the inserter machine out of range if required
    for a, g in v['G'].items():
        m.Add(sum(b for p, b in v['B'].items() if in_range(a, 3, p)) >= BEACONS_FOR[n_sci]).OnlyEnforceIf(g)
    if not beaconed_inserter_machine:
        for a, g in v['I'].items():
            for p, b in v['B'].items():
                if in_range(a, 3, p):
                    m.AddBoolOr([g.Not(), b.Not()])
    # access: each machine needs `need` face slots: a free tile next to it with a free tile beyond
    need = {'G': 2, 'I': 3, 'F': 2}
    score = []
    for k, n_need in need.items():
        s = kinds[k][0]
        for a, g in v[k].items():
            slots = []
            for i in range(s):
                for (px, py), (qx, qy) in (((a[0] + i, a[1] - 1), (a[0] + i, a[1] - 2)),
                                           ((a[0] + i, a[1] + s), (a[0] + i, a[1] + s + 1)),
                                           ((a[0] - 1, a[1] + i), (a[0] - 2, a[1] + i)),
                                           ((a[0] + s, a[1] + i), (a[0] + s + 1, a[1] + i))):
                    if (px, py) in free and (qx, qy) in free:
                        sl = m.NewBoolVar('')
                        m.AddImplication(sl, free[px, py])
                        m.AddImplication(sl, free[qx, qy])
                        slots.append(sl)
            m.Add(sum(slots) >= n_need).OnlyEnforceIf(g)
            score.append(sum(slots))
    # one top tile and two bottom tiles free for the pins
    m.Add(sum(free[x, 0] for x in range(W)) >= 1)
    m.Add(sum(free[x, H - 1] for x in range(W)) >= 2)
    # no repeats of placements already tried
    for pl in banned:
        lits = [v[k][a] for k, a in pl]
        m.AddBoolOr([l.Not() for l in lits])
    # prefer roomier placements, with a random tilt for variety
    m.Maximize(sum(score) + sum(rng.randint(0, 3) * b for d in v.values() for b in d.values()))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = seconds
    s.parameters.num_workers = 8
    s.parameters.random_seed = seed
    st = s.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, s.StatusName(st)
    key = [(k, a) for k, d in v.items() for a, b in d.items() if s.Value(b)]
    name = {'G': 'Gs', 'I': 'Is', 'F': 'Fs'}
    layout = dict(machines=[dict(type=name[k], x=a[0], y=a[1]) for k, a in key if k != 'B'],
                  beacons=[dict(x=a[0], y=a[1]) for k, a in key if k == 'B'],
                  belts=[], undergrounds=[], inserters=[])
    return (key, layout), s.StatusName(st)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--width', type=int, default=13)
    ap.add_argument('--height', type=int, default=13)
    ap.add_argument('--sci', type=int, default=5, choices=[4, 5, 6])
    ap.add_argument('--extra-beacons', type=int, default=0, help='beacons beyond the minimum')
    ap.add_argument('--beaconed-inserter-machine', action='store_true')
    ap.add_argument('--tries', type=int, default=4)
    ap.add_argument('--route-seconds', type=float, default=120)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--layout', help='route this placement instead of generating one')
    ap.add_argument('--out', default='smaller.json')
    ap.add_argument('--budget', type=float, default=560, help='stop after this many seconds')
    args = ap.parse_args()
    W, H, N = args.width, args.height, args.sci
    nb = BEACONS_FOR[N] + args.extra_beacons
    define_types(N, args.beaconed_inserter_machine)
    banned = []
    t_start = time.time()
    unknown = []
    for k in range(args.tries):
        if time.time() - t_start > args.budget - args.route_seconds - 10:
            break
        if args.layout:
            layout = json.load(open(args.layout))
            key = None
        else:
            res, st = placer(W, H, N, nb, args.beaconed_inserter_machine, args.seed + k, banned)
            if res is None:
                print(f'placer: {st}')
                return 1
            key, layout = res
            banned.append(key)
        print(f'try {k}: machines {[(mc["type"], mc["x"], mc["y"]) for mc in layout["machines"]]}, '
              f'beacons {[(b["x"], b["y"]) for b in layout["beacons"]]}', flush=True)
        c = block(W, H, N, nb)
        c.build()
        cell.fix_machines(c, layout)
        sol = c.solve(args.route_seconds, 8)
        print(f'  route: {c.status} in {c.solve_time:.0f}s' + (f', cost {c.objective:.0f}' if sol else ''), flush=True)
        if sol is not None:
            sol['blueprint'] = cell.to_blueprint(sol)
            sol['n_sci'] = N
            json.dump(sol, open(args.out, 'w'), indent=1)
            print('wrote', args.out)
            return 0
        if c.status == 'UNKNOWN':
            unknown.append(layout)
        if args.layout:
            return 1
    json.dump(unknown, open(args.out + '.unknown', 'w'))
    print(f'{len(unknown)} placements unresolved')
    return 1


if __name__ == '__main__':
    sys.exit(main())
