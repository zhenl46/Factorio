"""Entity-level CP-SAT model of one Factorio sub-block.

Every crafting machine, beacon, inserter (any reach), belt, underground belt (any tier),
pipe and pipe-to-ground is a placement decision on a W x H tile grid.

Items: belts carry two lanes; a lane holds at most one item and an integer flow that is
conserved tile by tile. Belt joins follow Factorio: straight, curve (a side feeder that is
the tile's only input) or side-load (both feeder lanes onto the near lane). Underground
belts pair with the nearest exit of the same tier; only same-tier undergrounds on the same
axis block a pair, so different tiers can weave. Inserters pick from belt lanes, underground
ends or machines, and drop on the far lane, into machines, or never onto curves.

Fluids: pipes connect to every neighbouring pipe; pipe-to-ground connects on one face and
underground to the nearest partner. Each pipe tile carries one fluid, connected pipes must
carry the same fluid, and a flow argument proves every used machine fluid box reaches a
source (input pin or producing machine) or a sink of its fluid. Machines with fluid recipes
can rotate, which moves their ports; unused ports must not touch pipes.

Machines: each placed machine has a crafting-rate variable no larger than its maximum
(modules and beacons included); item inputs and outputs are that rate times the recipe.
"""
import json
import math
import time

from ortools.sat.python import cp_model

import gamedata as gd
from gamedata import DIRS, OPP

N_LANES = (0, 1)


def step(t, d, k=1):
    return (t[0] + DIRS[d][0] * k, t[1] + DIRS[d][1] * k)


def left_vec(d):
    dx, dy = DIRS[d]
    return (dy, -dx)


def far_lane(belt_dir, ins_dir):
    """Lane an inserter moving in ins_dir drops onto a belt flowing belt_dir (0 = left lane)."""
    return 0 if DIRS[ins_dir] == left_vec(belt_dir) else 1


def perpendicular(d):
    return [(d + 1) % 4, (d + 3) % 4]


class BlockModel:
    def __init__(self, prob, soft=False):
        """soft: output-pin targets may be missed at a large cost per unit, so an (idle) layout
        always exists and the solver climbs towards full rates instead of searching blind."""
        self.p = prob
        self.soft = soft
        self.shortfall = []
        self.W, self.H = prob.W, prob.H
        self.m = cp_model.CpModel()
        self.tiles = [(x, y) for y in range(self.H) for x in range(self.W)
                      if (x, y) not in prob.blocked]
        self.tileset = set(self.tiles)

    # ----------------------------------------------------------------- helpers
    def inside(self, t):
        return t in self.tileset

    def B(self):
        return self.m.NewBoolVar('')

    def AND(self, lits):
        """A new literal equal to the conjunction of `lits`."""
        v = self.B()
        for l in lits:
            self.m.AddImplication(v, l)
        self.m.AddBoolOr([v] + [l.Not() for l in lits])
        return v

    def footprint(self, a, s):
        return [(a[0] + i, a[1] + j) for j in range(s) for i in range(s)]

    # ----------------------------------------------------------------- build
    def build(self):
        self._machines()
        self._belts()
        self._pins()
        self._inserters()
        self._lane_conservation()
        self._machine_balance()
        self._beacon_rules()
        self._fluids()
        self._objective()
        return self

    # ---- machines and beacons
    def _machines(self):
        m, P = self.m, self.p
        self.mach = {}          # group index -> {(anchor, rot): var}
        self.used = {}          # (gi, anchor) -> var (machine placed there, any rotation)
        self.crate = {}         # (gi, anchor) -> crafting-rate var
        self.cov = {}           # gi -> {tile: var}
        for gi, g in enumerate(P.groups):
            opts = {}
            for y in range(self.H - g.size + 1):
                for x in range(self.W - g.size + 1):
                    a = (x, y)
                    if not all(self.inside(t) for t in self.footprint(a, g.size)):
                        continue
                    for r in g.rotations:
                        opts[a, r] = self.B()
            self.mach[gi] = opts
            m.Add(sum(opts.values()) == g.count)
            anchors = {a for (a, r) in opts}
            for a in anchors:
                u = self.B()
                m.Add(u == sum(opts[a, r] for r in g.rotations if (a, r) in opts))
                self.used[gi, a] = u
                c = m.NewIntVar(0, g.cmax, '')
                m.Add(c <= g.cmax * u)
                self.crate[gi, a] = c
            cov = {}
            by_tile = {t: [] for t in self.tiles}
            for a in anchors:
                for t in self.footprint(a, g.size):
                    by_tile[t].append(self.used[gi, a])
            for t in self.tiles:
                v = self.B()
                m.Add(v == sum(by_tile[t]))
                cov[t] = v
            self.cov[gi] = cov
        self.bcn = {}
        if P.max_beacons:
            for y in range(self.H - 2):
                for x in range(self.W - 2):
                    if all(self.inside(t) for t in self.footprint((x, y), 3)):
                        self.bcn[x, y] = self.B()
            m.Add(sum(self.bcn.values()) <= P.max_beacons)
        self.covB, self.covM = {}, {}
        for t in self.tiles:
            v = self.B()
            m.Add(v == sum(b for a, b in self.bcn.items()
                           if a[0] <= t[0] < a[0] + 3 and a[1] <= t[1] < a[1] + 3))
            self.covB[t] = v
            w = self.B()
            m.Add(w == sum(self.cov[gi][t] for gi in self.cov))
            self.covM[t] = w

    # ---- belts and undergrounds
    def _belts(self):
        m, P = self.m, self.p
        out_pin_tiles = set()
        for pin in P.pins:
            if pin.kind == 'belt' and pin.dir == 'out':
                out_pin_tiles |= {(t, pin.outward) for t in pin.positions(self.W, self.H)}
        self.belt = {}                      # (t, d) -> var       normal belts (tier P.belt)
        self.ugi, self.ugo = {}, {}         # (tier, t, d) -> var
        for t in self.tiles:
            for d in range(4):
                u = step(t, d)
                if self.inside(u) or (t, d) in out_pin_tiles:
                    self.belt[t, d] = self.B()
                for k in P.ug_tiers:
                    if self.inside(u) or (t, d) in out_pin_tiles:
                        self.ugo[k, t, d] = self.B()
                    if any(self.inside(step(t, d, j)) for j in range(1, gd.BELT_TIERS[k]['max_distance'] + 1)):
                        self.ugi[k, t, d] = self.B()
        # per-tile summaries
        self.beltAt, self.bl, self.dirAt, self.cap = {}, {}, {}, {}
        for t in self.tiles:
            b = self.B()
            m.Add(b == sum(self.belt.get((t, d), 0) for d in range(4)))
            ugs = [v for (k, tt, d), v in list(self.ugi.items()) + list(self.ugo.items()) if tt == t]
            bl = self.B()
            m.Add(bl == b + sum(ugs))
            self.beltAt[t], self.bl[t] = b, bl
            for d in range(4):
                terms = [self.belt.get((t, d))] + [self.ugi.get((k, t, d)) for k in P.ug_tiers] + \
                        [self.ugo.get((k, t, d)) for k in P.ug_tiers]
                terms = [x for x in terms if x is not None]
                if terms:
                    v = self.B()
                    m.Add(v == sum(terms))
                    self.dirAt[t, d] = v
        # occupancy is completed in _fluids/_inserters via self.occ
        self.occ = {t: [self.covM[t], self.covB[t], self.bl[t]] for t in self.tiles}

        # lanes
        K = P.items
        cap_b = gd.lane_units(P.belt)
        self.lane = {(t, l, k): self.B() for t in self.tiles for l in N_LANES for k in K}
        self.F = {(t, l): m.NewIntVar(0, P.lane_cap, '') for t in self.tiles for l in N_LANES}
        for t in self.tiles:
            capexpr = cap_b * self.beltAt[t] + sum(
                gd.lane_units(k) * v for (k, tt, d), v in list(self.ugi.items()) + list(self.ugo.items()) if tt == t)
            for l in N_LANES:
                ks = [self.lane[t, l, k] for k in K]
                m.Add(sum(ks) <= 1)
                for v in ks:
                    m.AddImplication(v, self.bl[t])
                m.Add(self.F[t, l] <= P.lane_cap * sum(ks))
                m.Add(self.F[t, l] <= capexpr)

        # connections: belts and exits feed forward; straight / curve / side-load
        self.edges = []          # (src, dst, act)
        self.ident, self.sides = {}, {}
        self.curves_into = {t: [] for t in self.tiles}
        self.into = {t: [] for t in self.tiles}
        self.out_of = {t: [] for t in self.tiles}
        side_lits, straight_lit, pending = {}, {}, []
        feeders = [((t, d), v) for (t, d), v in self.belt.items()] + \
                  [((t, d), v) for (k, t, d), v in self.ugo.items()]
        for (t, d), v in feeders:
            u = step(t, d)
            if not self.inside(u):
                continue
            if (u, OPP[d]) in self.belt:
                m.AddBoolOr([v.Not(), self.belt[u, OPP[d]].Not()])        # head-on
            for k in P.ug_tiers:
                for d2 in range(4):
                    if (k, u, d2) in self.ugo:
                        m.AddBoolOr([v.Not(), self.ugo[k, u, d2].Not()])  # into an exit
                    if d2 != d and (k, u, d2) in self.ugi:
                        m.AddBoolOr([v.Not(), self.ugi[k, u, d2].Not()])  # side/front of an entrance
            targets = [x for x in [self.belt.get((u, d))] + [self.ugi.get((k, u, d)) for k in P.ug_tiers]
                       if x is not None]
            st = self.B()
            m.AddImplication(st, v)
            if targets:
                m.Add(st <= sum(targets))
                for tg in targets:
                    m.AddBoolOr([v.Not(), tg.Not(), st])
            else:
                m.Add(st == 0)
            if (u, d) in self.belt:
                straight_lit.setdefault((u, d), []).append(self.AND([v, self.belt[u, d]]))
            sides = []
            for D in perpendicular(d):
                if (u, D) not in self.belt:
                    continue
                sd = self.AND([v, self.belt[u, D]])
                svec = (-DIRS[d][0], -DIRS[d][1])
                near = 0 if svec == left_vec(D) else 1
                side_lits.setdefault((u, D, svec), []).append(sd)
                sides.append((D, sd, near, svec))
            act = self.B()
            m.Add(act == st + sum(x[1] for x in sides))
            idx = len(self.edges)
            self.edges.append((t, u, act))
            self.into[u].append(idx)
            self.out_of[t].append(idx)
            pending.append((idx, u, st, sides))
        for idx, u, st, sides in pending:
            ident = [st]
            self.sides[idx] = []
            for D, sd, near, svec in sides:
                blockers = straight_lit.get((u, D), []) + side_lits.get((u, D, (-svec[0], -svec[1])), [])
                cv, sl = self.B(), self.B()
                m.Add(cv + sl == sd)
                for b in blockers:
                    m.AddImplication(b, cv.Not())
                m.AddBoolOr([sd.Not()] + blockers + [cv])
                ident.append(cv)
                self.curves_into[u].append(cv)
                self.sides[idx].append((D, sl, near))
            lit = self.B()
            m.Add(lit == sum(ident))
            self.ident[idx] = lit
        # underground pairs, per tier: only same-tier undergrounds on the same axis block
        self.pairs = {}
        for (k, t, d), v in self.ugi.items():
            opts = []
            for j in range(1, gd.BELT_TIERS[k]['max_distance'] + 1):
                u = step(t, d, j)
                if (k, u, d) not in self.ugo:
                    continue
                Pv = self.B()
                m.AddImplication(Pv, self.ugo[k, u, d])
                for i in range(1, j):
                    w = step(t, d, i)
                    for d2 in (d, OPP[d]):
                        for g in (self.ugi.get((k, w, d2)), self.ugo.get((k, w, d2))):
                            if g is not None:
                                m.AddBoolOr([Pv.Not(), g.Not()])
                self.pairs[k, t, d, j] = Pv
                opts.append(Pv)
                idx = len(self.edges)
                self.edges.append((t, u, Pv))
                self.into[u].append(idx)
                self.out_of[t].append(idx)
                self.ident[idx] = Pv
                self.sides[idx] = []
            m.Add(sum(opts) == v)
        ends = {}
        for (k, t, d, j), Pv in self.pairs.items():
            ends.setdefault((k, step(t, d, j), d), []).append(Pv)
        for key, v in self.ugo.items():
            m.Add(sum(ends.get(key, [])) == v)
        # lane contents follow the mapping
        self.Gi, self.Gs = {}, {}
        for idx, (t, u, act) in enumerate(self.edges):
            for l in N_LANES:
                for k in K:
                    m.AddBoolOr([self.ident[idx].Not(), self.lane[t, l, k].Not(), self.lane[u, l, k]])
                    for D, sl, near in self.sides[idx]:
                        m.AddBoolOr([sl.Not(), self.lane[t, l, k].Not(), self.lane[u, near, k]])
                g = m.NewIntVar(0, P.lane_cap, '')
                m.Add(g <= P.lane_cap * self.ident[idx])
                self.Gi[idx, l] = g
                for D, sl, near in self.sides[idx]:
                    g2 = m.NewIntVar(0, P.lane_cap, '')
                    m.Add(g2 <= P.lane_cap * sl)
                    self.Gs[idx, D, l] = (g2, near)

    # ---- pins
    def _pins(self):
        m, P = self.m, self.p
        self.supply = {}           # (t, l) -> [exprs]  belt input pins
        self.exF = {}              # (t, l, k, pin) -> var
        self.pin_at = {}
        self.pin_orient = {}
        out_vars = {}
        for pin in P.pins:
            if pin.kind != 'belt':
                continue
            cands = [t for t in pin.positions(self.W, self.H) if self.inside(t)]
            pos = {t: self.B() for t in cands}
            m.Add(sum(pos.values()) == 1)
            self.pin_at[pin.name] = pos
            if pin.dir == 'in':
                d_in = OPP[pin.outward]
                items = list(pin.items)
                orient = self.B() if len(items) == 2 else None
                self.pin_orient[pin.name] = orient
                if orient is not None and pin.lanes:
                    m.Add(orient == int(pin.lanes[0] != items[0]))
                for t, v in pos.items():
                    ents = [x for x in [self.belt.get((t, d_in))] + [self.ugi.get((k, t, d_in)) for k in P.ug_tiers]
                            if x is not None]
                    m.Add(sum(ents) == 1).OnlyEnforceIf(v)
                    for idx in self.into[t]:
                        m.AddBoolOr([v.Not(), self.edges[idx][2].Not()])
                    if orient is not None:
                        a, b = items
                        m.AddBoolOr([v.Not(), orient, self.lane[t, 0, a]])
                        m.AddBoolOr([v.Not(), orient, self.lane[t, 1, b]])
                        m.AddBoolOr([v.Not(), orient.Not(), self.lane[t, 0, b]])
                        m.AddBoolOr([v.Not(), orient.Not(), self.lane[t, 1, a]])
                        for l in N_LANES:
                            for k in items:
                                s = m.NewIntVar(0, pin.items[k], '')
                                m.Add(s <= pin.items[k] * v)
                                m.Add(s <= pin.items[k] * self.lane[t, l, k])
                                self.supply.setdefault((t, l), []).append(s)
                    else:
                        (k,) = items
                        tot = []
                        for l in N_LANES:
                            s = m.NewIntVar(0, pin.items[k], '')
                            m.Add(s <= pin.items[k] * self.lane[t, l, k])
                            m.Add(s <= pin.items[k] * v)
                            self.supply.setdefault((t, l), []).append(s)
                            tot.append(s)
                        m.Add(sum(tot) <= pin.items[k])
            else:
                d_out = pin.outward
                for t, v in pos.items():
                    out_vars.setdefault((t, d_out), []).append(v)
                    ents = [x for x in [self.belt.get((t, d_out))] + [self.ugo.get((k, t, d_out)) for k in P.ug_tiers]
                            if x is not None]
                    m.Add(sum(ents) == 1).OnlyEnforceIf(v)
                    for l in N_LANES:
                        for k in P.items:
                            if k not in pin.items:
                                m.AddBoolOr([v.Not(), self.lane[t, l, k].Not()])
                            else:
                                f = m.NewIntVar(0, P.lane_cap, '')
                                m.Add(f <= P.lane_cap * v)
                                m.Add(f <= P.lane_cap * self.lane[t, l, k])
                                self.exF[t, l, k, pin.name] = f
                for k, amt in pin.items.items():
                    short = m.NewIntVar(0, amt if self.soft else 0, '')
                    self.shortfall.append(short)
                    m.Add(sum(f for (t, l, k2, nm), f in self.exF.items() if k2 == k and nm == pin.name) + short >= amt)
        # belts may only leave the block at chosen output pins
        for t in self.tiles:
            for d in range(4):
                if self.inside(step(t, d)):
                    continue
                ents = [x for x in [self.belt.get((t, d))] + [self.ugo.get((k, t, d)) for k in P.ug_tiers]
                        if x is not None]
                if ents:
                    m.Add(sum(ents) == sum(out_vars.get((t, d), [])))

    # ---- inserters
    def _inserters(self):
        m, P = self.m, self.p
        G = P.groups
        self.ins = {}             # (kind, t, d) -> var
        for kind in P.inserters:
            r = gd.INSERTER_REACH[kind]
            for t in self.tiles:
                for d in range(4):
                    if self.inside(step(t, OPP[d], r)) and self.inside(step(t, d, r)):
                        self.ins[kind, t, d] = self.B()
        self.insAt = {}
        by_tile = {t: [] for t in self.tiles}
        for (kind, t, d), v in self.ins.items():
            by_tile[t].append(v)
        for t in self.tiles:
            v = self.B()
            m.Add(v == sum(by_tile[t]))
            self.insAt[t] = v
            self.occ[t].append(v)
        needs = {k: {} for k in P.items}
        for t in self.tiles:
            for k in P.items:
                needs[k][t] = sum(self.cov[gi][t] for gi, g in enumerate(G) if k in g.items_in)
        CAP = P.lane_cap
        self.pick = {}      # (ins key, l, k) -> var
        self.drop = {}      # (ins key, gi, k, D) -> (var, lane)
        self.direct = {}    # (ins key, gi, k) -> var
        self.drops_at = {(t, l): [] for t in self.tiles for l in N_LANES}
        self.picks_at = {(t, l): [] for t in self.tiles for l in N_LANES}
        self.ins_by_drop, self.ins_by_pick = {}, {}
        self.drop_by_key, self.direct_by_key = {}, {}
        for key, v in self.ins.items():
            kind, t, d = key
            r = gd.INSERTER_REACH[kind]
            p, q = step(t, OPP[d], r), step(t, d, r)
            self.ins_by_drop.setdefault(q, []).append(key)
            self.ins_by_pick.setdefault(p, []).append(key)
            m.AddBoolOr([v.Not(), self.bl[p], self.covM[p]])
            m.AddBoolOr([v.Not(), self.bl[q], self.covM[q]])
            m.AddBoolOr([v.Not(), self.bl[p].Not(), self.bl[q].Not()])
            for dd in (d, OPP[d]):
                if (q, dd) in self.dirAt:
                    m.AddBoolOr([v.Not(), self.dirAt[q, dd].Not()])          # no drops along the belt
            for gi, g in enumerate(G):
                for gj, g2 in enumerate(G):
                    if not set(g.items_out) & set(g2.items_in):
                        m.AddBoolOr([v.Not(), self.cov[gi][p].Not(), self.cov[gj][q].Not()])
            total = []
            for l in N_LANES:
                for k in P.items:
                    if not any(k in g.items_in for g in G):
                        continue
                    f = m.NewIntVar(0, CAP, '')
                    m.Add(f <= CAP * self.lane[p, l, k])
                    m.Add(f <= CAP * needs[k][q])
                    self.pick[key, l, k] = f
                    self.picks_at[p, l].append(f)
                    total.append(f)
            for gi, g in enumerate(G):
                for k in g.items_out:
                    for D in perpendicular(d):
                        if (q, D) not in self.dirAt:
                            continue
                        ln = far_lane(D, d)
                        f = m.NewIntVar(0, CAP, '')
                        m.Add(f <= CAP * self.cov[gi][p])
                        m.Add(f <= CAP * self.dirAt[q, D])
                        m.Add(f <= CAP * self.lane[q, ln, k])
                        self.drop[key, gi, k, D] = (f, ln)
                        self.drop_by_key.setdefault(key, []).append((gi, k, f))
                        self.drops_at[q, ln].append(f)
                        total.append(f)
                    if any(k in g2.items_in for g2 in G):
                        f = m.NewIntVar(0, CAP, '')
                        m.Add(f <= CAP * self.cov[gi][p])
                        m.Add(f <= CAP * needs[k][q])
                        self.direct[key, gi, k] = f
                        self.direct_by_key.setdefault(key, []).append((gi, k, f))
                        total.append(f)
            m.Add(sum(total) <= P.inserter_cap * v)
            m.Add(sum(total) >= v)                                       # no idle inserters
            for cv in self.curves_into[q]:
                m.AddBoolOr([v.Not(), self.covM[p].Not(), cv.Not()])      # no drops onto curves

    # ---- lane conservation
    def _lane_conservation(self):
        m = self.m
        inflow = {(t, l): [] for t in self.tiles for l in N_LANES}
        outflow = {(t, l): [] for t in self.tiles for l in N_LANES}
        for idx, (t, u, act) in enumerate(self.edges):
            for l in N_LANES:
                outflow[t, l].append(self.Gi[idx, l])
                inflow[u, l].append(self.Gi[idx, l])
                for D, sl, near in self.sides[idx]:
                    g, _ = self.Gs[idx, D, l]
                    outflow[t, l].append(g)
                    inflow[u, near].append(g)
        for (t, l, k, nm), f in self.exF.items():
            outflow[t, l].append(f)
        for t in self.tiles:
            for l in N_LANES:
                m.Add(sum(outflow[t, l]) == self.F[t, l])
                m.Add(sum(inflow[t, l]) + sum(self.supply.get((t, l), [])) + sum(self.drops_at[t, l])
                      == self.F[t, l] + sum(self.picks_at[t, l]))

    # ---- machine input / output balance
    def _machine_balance(self):
        m, P = self.m, self.p
        G = P.groups
        for (gi, a), u in self.used.items():
            g = G[gi]
            fp = set(self.footprint(a, g.size))
            ins_in = [key for q in fp for key in self.ins_by_drop.get(q, []) if key[1] not in fp]
            ins_out = [key for p in fp for key in self.ins_by_pick.get(p, []) if key[1] not in fp]
            c = self.crate[gi, a]
            for k, per in g.items_in.items():
                expr = []
                for key in ins_in:
                    expr += [self.pick[key, l, k] for l in N_LANES if (key, l, k) in self.pick]
                    expr += [f for (gj, k2, f) in self.direct_by_key.get(key, []) if k2 == k]
                m.Add(sum(expr) == per * c).OnlyEnforceIf(u)
            for k, per in g.items_out.items():
                expr = []
                for key in ins_out:
                    expr += [f for (gj, k2, f) in self.drop_by_key.get(key, []) if gj == gi and k2 == k]
                    expr += [f for (gj, k2, f) in self.direct_by_key.get(key, []) if gj == gi and k2 == k]
                m.Add(sum(expr) == per * c).OnlyEnforceIf(u)

    # ---- beacons
    def _beacon_rules(self):
        m, P = self.m, self.p
        R = gd.BEACON['reach']
        for gi, g in enumerate(P.groups):
            s = g.size
            for (gj, a), u in self.used.items():
                if gj != gi:
                    continue
                near = [b for (bx, by), b in self.bcn.items()
                        if a[0] <= bx + 2 + R and a[0] + s - 1 >= bx - R and a[1] <= by + 2 + R and a[1] + s - 1 >= by - R]
                if g.min_beacons:
                    m.Add(sum(near) >= g.min_beacons).OnlyEnforceIf(u)
                if g.no_beacon:
                    for b in near:
                        m.AddBoolOr([u.Not(), b.Not()])

    # ---- fluids
    def _fluids(self):
        m, P = self.m, self.p
        FL = P.fluids
        self.pipe, self.ptg, self.fl = {}, {}, {}
        any_fluid = bool(FL)
        for t in self.tiles:
            if any_fluid:
                self.pipe[t] = self.B()
                for d in range(4):
                    e = OPP[d]
                    if any(self.inside(step(t, e, j)) for j in range(1, gd.PTG_MAX + 1)):
                        self.ptg[t, d] = self.B()
            pa = self.B()
            m.Add(pa == self.pipe.get(t, 0) + sum(self.ptg.get((t, d), 0) for d in range(4)))
            self.occ[t].append(pa)
            for f in FL:
                self.fl[t, f] = self.B()
            if FL:
                m.Add(sum(self.fl[t, f] for f in FL) == pa)
        # occupancy: one entity per tile
        for t in self.tiles:
            m.Add(sum(self.occ[t]) <= 1)
        if not any_fluid:
            return

        def open_(t, d):
            return [x for x in (self.pipe.get(t), self.ptg.get((t, d))) if x is not None]

        M = 1 + sum(g.count * (len(g.fluid_in) + len(g.fluid_out)) for g in P.groups if g.ports_active) + \
            sum(1 for p in P.pins if p.kind == 'pipe')
        self.fedges = []          # (a, b, literal)
        # neighbouring faces
        for t in self.tiles:
            for d in (1, 2):
                u = step(t, d)
                if not self.inside(u):
                    continue
                a, b = open_(t, d), open_(u, OPP[d])
                x = self.B()
                ao, bo = self.B(), self.B()
                m.Add(ao == sum(a))
                m.Add(bo == sum(b))
                m.AddImplication(x, ao)
                m.AddImplication(x, bo)
                m.AddBoolOr([ao.Not(), bo.Not(), x])
                for f in FL:
                    m.AddBoolOr([x.Not(), self.fl[t, f].Not(), self.fl[u, f]])
                    m.AddBoolOr([x.Not(), self.fl[u, f].Not(), self.fl[t, f]])
                self.fedges.append((t, u, x))
        # pipe-to-ground pairs (searched from faces N and W, i.e. undergrounds heading S and E)
        self.ptg_pairs = {}
        ends = {}
        for (t, d), v in self.ptg.items():
            if d not in (0, 3):
                continue
            e = OPP[d]
            opts = []
            for j in range(1, gd.PTG_MAX + 1):
                u = step(t, e, j)
                if (u, e) not in self.ptg:
                    continue
                Q = self.B()
                m.AddImplication(Q, self.ptg[u, e])
                for i in range(1, j):
                    w = step(t, e, i)
                    for d2 in (d, e):
                        if (w, d2) in self.ptg:
                            m.AddBoolOr([Q.Not(), self.ptg[w, d2].Not()])
                for f in FL:
                    m.AddBoolOr([Q.Not(), self.fl[t, f].Not(), self.fl[u, f]])
                    m.AddBoolOr([Q.Not(), self.fl[u, f].Not(), self.fl[t, f]])
                self.ptg_pairs[t, d, j] = Q
                ends.setdefault((u, e), []).append(Q)
                opts.append(Q)
                self.fedges.append((t, u, Q))
            m.Add(sum(opts) == v)
        for (u, e), v in self.ptg.items():
            if e in (1, 2):
                m.Add(sum(ends.get((u, e), [])) == v)
        # machine ports
        demand = {t: [] for t in self.tiles}
        supply = {t: [] for t in self.tiles}
        port_index = {}
        self.port_opts = []
        for gi, g in enumerate(P.groups):
            if not g.ports_active:
                continue
            for (a, r), v in self.mach[gi].items():
                for typ, idx, tile, d in gd.machine_ports(g.entity, r):
                    inside_t = (a[0] + tile[0], a[1] + tile[1])
                    u = step(inside_t, d)
                    fluid = (g.fluid_in if typ == 'input' else g.fluid_out).get(idx)
                    port_index.setdefault((inside_t, d), []).append(v)
                    self.port_opts.append((v, inside_t, d, u, fluid, typ))
                    if not self.inside(u):
                        if fluid is not None:
                            m.Add(v == 0)
                        continue
                    faces = open_(u, OPP[d])
                    if fluid is None:
                        for fc in faces:
                            m.AddBoolOr([v.Not(), fc.Not()])
                        continue
                    m.AddBoolOr([v.Not()] + faces)
                    m.AddImplication(v, self.fl[u, fluid])
                    (demand if typ == 'input' else supply)[u].append(v)
        # two machines' ports must never face each other
        for (v, inside_t, d, u, fluid, typ) in self.port_opts:
            for v2 in port_index.get((u, OPP[d]), []):
                if v2 is not v:
                    m.AddBoolOr([v.Not(), v2.Not()])
        # pipe pins
        self.pipe_pin_at = {}
        for pin in P.pins:
            if pin.kind != 'pipe':
                continue
            cands = [t for t in pin.positions(self.W, self.H) if self.inside(t)]
            pos = {t: self.B() for t in cands}
            m.Add(sum(pos.values()) == 1)
            self.pipe_pin_at[pin.name] = pos
            for t, v in pos.items():
                faces = open_(t, pin.outward)
                m.AddBoolOr([v.Not()] + faces)
                m.AddImplication(v, self.fl[t, pin.fluid])
                s = m.NewIntVar(0, M, '')
                m.Add(s <= M * v)
                m.Add(s >= v)
                (supply if pin.dir == 'in' else demand)[t].append(s)
        # connectivity: one commodity, conserved per tile; edges only within one fluid
        flow_in = {t: [] for t in self.tiles}
        flow_out = {t: [] for t in self.tiles}
        for a, b, x in self.fedges:
            for s_, d_ in ((a, b), (b, a)):
                f = m.NewIntVar(0, M, '')
                m.Add(f <= M * x)
                flow_out[s_].append(f)
                flow_in[d_].append(f)
        for t in self.tiles:
            if flow_in[t] or flow_out[t] or demand[t] or supply[t]:
                m.Add(sum(flow_in[t]) + sum(supply[t]) == sum(flow_out[t]) + sum(demand[t]))

    # ---- objective
    def _objective(self):
        P, w = self.p, self.p.weights
        cost = w['belt'] * sum(self.belt.values())
        cost += w['underground'] * (sum(self.ugi.values()) + sum(self.ugo.values()))
        for (kind, t, d), v in self.ins.items():
            cost += (w['long_inserter'] if gd.INSERTER_REACH[kind] > 1 else w['inserter']) * v
        cost += w['pipe'] * sum(self.pipe.values()) + w['pipe_to_ground'] * sum(self.ptg.values())
        cost += w['beacon'] * sum(self.bcn.values())
        self.cost = cost
        self.short_total = sum(self.shortfall)
        # one unit is 1e-5 items/s: missing 0.01/s costs 1000, far more than any entity
        self.m.Minimize(cost + self.short_total)

    # ---------------------------------------------------------- fix / hint
    def _layout_sets(self, lay):
        gidx = {g.name: i for i, g in enumerate(self.p.groups)}
        mach = {(gidx[mc['group']], (mc['x'], mc['y']), mc.get('rot', 0)) for mc in lay.get('machines', [])}
        return dict(
            mach=mach,
            bcn={(b['x'], b['y']) for b in lay.get('beacons', [])},
            belt={((b['x'], b['y']), b['dir']) for b in lay.get('belts', [])},
            ugi={(u['tier'], (u['x'], u['y']), u['dir']) for u in lay.get('undergrounds', [])},
            ugo={(u['tier'], (u['ox'], u['oy']), u['dir']) for u in lay.get('undergrounds', [])},
            ins={(i['kind'], (i['x'], i['y']), i['dir']) for i in lay.get('inserters', [])},
            pipe={(p['x'], p['y']) for p in lay.get('pipes', [])},
            ptg={((p['x'], p['y']), p['face']) for p in lay.get('pipe_to_ground', [])})

    def _entity_vars(self, lay, machines_only=False):
        S = self._layout_sets(lay)
        anchors = {(gi, a) for gi, a, r in S['mach']}
        for gi, opts in self.mach.items():
            if machines_only:
                # positions fixed, rotation left to the solver
                for a in {a for (a, r) in opts}:
                    yield sum(v for (a2, r), v in opts.items() if a2 == a), int((gi, a) in anchors)
                continue
            for (a, r), v in opts.items():
                yield v, int((gi, a, r) in S['mach'])
        for a, v in self.bcn.items():
            yield v, int(a in S['bcn'])
        if machines_only:
            return
        for k, v in self.belt.items():
            yield v, int(k in S['belt'])
        for k, v in self.ugi.items():
            yield v, int(k in S['ugi'])
        for k, v in self.ugo.items():
            yield v, int(k in S['ugo'])
        for k, v in self.ins.items():
            yield v, int(k in S['ins'])
        for k, v in self.pipe.items():
            yield v, int(k in S['pipe'])
        for k, v in self.ptg.items():
            yield v, int(k in S['ptg'])

    def fix(self, lay, machines_only=False):
        for v, val in self._entity_vars(lay, machines_only):
            self.m.Add(v == val)

    def hint(self, lay):
        for v, val in self._entity_vars(lay):
            self.m.AddHint(v, val)

    # ---------------------------------------------------------- solve
    def solve(self, seconds=60, workers=8, log=None, seed=0):
        s = cp_model.CpSolver()
        s.parameters.max_time_in_seconds = seconds
        s.parameters.num_workers = workers
        s.parameters.random_seed = seed
        t0 = time.time()
        outer = self

        class CB(cp_model.CpSolverSolutionCallback):
            def on_solution_callback(cb):
                if log:
                    short = cb.Value(outer.short_total) if outer.shortfall else 0
                    log(f'    {time.time() - t0:6.1f}s  objective {cb.ObjectiveValue():.0f}'
                        + (f'  (short {short / gd.SCALE_I:.3g} items/s)' if short else ''))
        st = s.Solve(self.m, CB())
        self.status = s.StatusName(st)
        self.solve_time = time.time() - t0
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        self.objective, self.bound = s.ObjectiveValue(), s.BestObjectiveBound()
        self.short_value = s.Value(self.short_total) if self.shortfall else 0
        lay = self.extract(s)
        lay['shortfall_items_per_s'] = self.short_value / gd.SCALE_I
        return lay

    def extract(self, s):
        P, val = self.p, s.Value
        lay = dict(spec=P.name, W=self.W, H=self.H, machines=[], beacons=[], belts=[], undergrounds=[],
                   inserters=[], pipes=[], pipe_to_ground=[], pins={}, lanes={}, fluids={})
        for gi, opts in self.mach.items():
            g = P.groups[gi]
            for (a, r), v in opts.items():
                if val(v):
                    c = val(self.crate[gi, a])
                    lay['machines'].append(dict(group=g.name, x=a[0], y=a[1], rot=r, size=g.size,
                                                rate=c / gd.SCALE_C, c=c))
        lay['beacons'] = [dict(x=a[0], y=a[1]) for a, v in self.bcn.items() if val(v)]
        lay['belts'] = [dict(x=t[0], y=t[1], dir=d) for (t, d), v in self.belt.items() if val(v)]
        for (k, t, d, j), Pv in self.pairs.items():
            if val(Pv):
                u = step(t, d, j)
                lay['undergrounds'].append(dict(tier=k, x=t[0], y=t[1], dir=d, ox=u[0], oy=u[1]))
        for key, v in self.ins.items():
            if not val(v):
                continue
            kind, t, d = key
            flows = {}
            for l in N_LANES:
                for k in P.items:
                    f = self.pick.get((key, l, k))
                    if f is not None and val(f):
                        flows[f'pick:{k}:{l}'] = val(f)
            for (kk, gi, k, D), (f, ln) in self.drop.items():
                if kk == key and val(f):
                    flows[f'drop:{k}:{ln}'] = flows.get(f'drop:{k}:{ln}', 0) + val(f)
            for (kk, gi, k), f in self.direct.items():
                if kk == key and val(f):
                    flows[f'direct:{k}'] = val(f)
            lay['inserters'].append(dict(kind=kind, x=t[0], y=t[1], dir=d, flows=flows))
        lay['pipes'] = [dict(x=t[0], y=t[1]) for t, v in self.pipe.items() if val(v)]
        for (t, d, j), Q in getattr(self, 'ptg_pairs', {}).items():
            if val(Q):
                u = step(t, OPP[d], j)
                lay['pipe_to_ground'].append(dict(x=t[0], y=t[1], face=d))
                lay['pipe_to_ground'].append(dict(x=u[0], y=u[1], face=OPP[d]))
        for t in self.tiles:
            if val(self.bl[t]):
                lay['lanes'][f'{t[0]},{t[1]}'] = [
                    ([k for k in P.items if val(self.lane[t, l, k])] or [None])[0] for l in N_LANES
                ] + [val(self.F[t, 0]), val(self.F[t, 1])]
            for f in P.fluids:
                if val(self.fl[t, f]):
                    lay['fluids'][f'{t[0]},{t[1]}'] = f
        for name, pos in list(self.pin_at.items()) + list(getattr(self, 'pipe_pin_at', {}).items()):
            t = [t for t, v in pos.items() if val(v)][0]
            lay['pins'][name] = dict(x=t[0], y=t[1])
            o = self.pin_orient.get(name)
            if o is not None:
                pin = next(p for p in P.pins if p.name == name)
                items = list(pin.items)
                lay['pins'][name]['lanes'] = [items[val(o)], items[1 - val(o)]]
        return lay
