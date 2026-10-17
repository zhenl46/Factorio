"""Machine and beacon placement proposals (the 'floorplan' step inside a block).

A small CP-SAT model places machines and beacons so that beacon counts hold, nothing
overlaps, and every machine keeps some free tiles around it for inserters, belts and
pipes. The full block model then routes each proposal with the machines fixed.
"""
import random

from ortools.sat.python import cp_model

import gamedata as gd


def _in_range(a, s, b, R):
    return a[0] <= b[0] + 2 + R and a[0] + s - 1 >= b[0] - R and a[1] <= b[1] + 2 + R and a[1] + s - 1 >= b[1] - R


def propose(prob, seed=0, banned=(), seconds=10, workers=8, access=None):
    W, H = prob.W, prob.H
    tiles = [(x, y) for y in range(H) for x in range(W) if (x, y) not in prob.blocked]
    tileset = set(tiles)
    m = cp_model.CpModel()
    rng = random.Random(seed)
    R = gd.BEACON['reach']
    var = {}                                   # ('g', gi) or ('b',) -> {anchor: var}
    for gi, g in enumerate(prob.groups):
        s = g.size
        d = {}
        for y in range(H - s + 1):
            for x in range(W - s + 1):
                if all((x + i, y + j) in tileset for j in range(s) for i in range(s)):
                    d[x, y] = m.NewBoolVar('')
        m.Add(sum(d.values()) == g.count)
        var['g', gi] = d
    bd = {}
    if prob.max_beacons:
        for y in range(H - 2):
            for x in range(W - 2):
                if all((x + i, y + j) in tileset for j in range(3) for i in range(3)):
                    bd[x, y] = m.NewBoolVar('')
        m.Add(sum(bd.values()) <= prob.max_beacons)
    var['b'] = bd
    sizes = {('g', gi): g.size for gi, g in enumerate(prob.groups)}
    sizes['b'] = 3
    occ = {}
    for t in tiles:
        cov = [v for key, d in var.items() for a, v in d.items()
               if a[0] <= t[0] < a[0] + sizes[key] and a[1] <= t[1] < a[1] + sizes[key]]
        o = m.NewBoolVar('')
        m.Add(o == sum(cov))
        occ[t] = o
    free = {t: occ[t].Not() for t in tiles}
    # beacons
    for gi, g in enumerate(prob.groups):
        for a, v in var['g', gi].items():
            near = [b for p, b in bd.items() if _in_range(a, g.size, p, R)]
            if g.min_beacons:
                m.Add(sum(near) >= g.min_beacons).OnlyEnforceIf(v)
            if g.no_beacon:
                for b in near:
                    m.AddBoolOr([v.Not(), b.Not()])
    # access around machines
    score = []
    for gi, g in enumerate(prob.groups):
        s = g.size
        n_items = len(g.items_in) + len(g.items_out)
        n_fluid = len(g.fluid_in) + len(g.fluid_out) if g.ports_active else 0
        need = (access or {}).get(g.name, min(4, max(1, n_items)))
        for a, v in var['g', gi].items():
            slots, ring = [], []
            for i in range(s):
                for (px, py), (qx, qy) in (((a[0] + i, a[1] - 1), (a[0] + i, a[1] - 2)),
                                           ((a[0] + i, a[1] + s), (a[0] + i, a[1] + s + 1)),
                                           ((a[0] - 1, a[1] + i), (a[0] - 2, a[1] + i)),
                                           ((a[0] + s, a[1] + i), (a[0] + s + 1, a[1] + i))):
                    if (px, py) in free:
                        ring.append(free[px, py])
                        if (qx, qy) in free:
                            sl = m.NewBoolVar('')
                            m.AddImplication(sl, free[px, py])
                            m.AddImplication(sl, free[qx, qy])
                            slots.append(sl)
            if n_items:
                m.Add(sum(slots) >= need).OnlyEnforceIf(v)
            if n_fluid:
                m.Add(sum(ring) >= need + n_fluid).OnlyEnforceIf(v)
            score.append(sum(slots))
    # pins keep their tiles free
    for p in prob.pins:
        cands = [t for t in p.positions(W, H) if t in tileset]
        m.AddBoolOr([free[t] for t in cands])
    for key in banned:
        lits = [var[k][a] for k, a in key]
        m.AddBoolOr([l.Not() for l in lits])
    m.Maximize(sum(score) * 4 + sum(rng.randint(0, 3) * v for d in var.values() for v in d.values())
               - 20 * sum(bd.values()))
    sv = cp_model.CpSolver()
    sv.parameters.max_time_in_seconds = seconds
    sv.parameters.num_workers = workers
    sv.parameters.random_seed = seed
    st = sv.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, sv.StatusName(st)
    key = tuple((k, a) for k, d in var.items() for a, v in d.items() if sv.Value(v))
    lay = dict(machines=[dict(group=prob.groups[k[1]].name, x=a[0], y=a[1], rot=0)
                         for k, a in key if k != 'b'],
               beacons=[dict(x=a[0], y=a[1]) for k, a in key if k == 'b'])
    return (key, lay), sv.StatusName(st)
