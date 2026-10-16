#!/usr/bin/env python3
"""
Entity-level CP-SAT model of one rectangular factory cell.

Every assembler, foundry, beacon, inserter, belt, underground belt and power
pole is a placement decision on a W x H tile grid.  Belts carry two lanes; a
lane holds at most one item type and an integer flow, and flow is conserved
tile by tile, so anything an inserter picks up has to come from an input pin or
from a machine.  Input pins sit on the bottom edge and output pins on the top
edge, all as north-facing belts, so cells stack vertically like macros sharing
a pin.  Coordinates follow Factorio: x grows east, y grows south.

Rates are integers in units of U = 0.55 items/s (one science assembler's output).
"""
import base64
import json
import math
import time
import zlib

from ortools.sat.python import cp_model

U = 0.55                       # items/s per unit
INS_CAP = 4                    # fast inserter, ~2.31 items/s -> 4 units (2.2/s)
LANE_CAP = 40                  # express belt lane, 22.5 items/s
UG_MAX = 5                     # underground exit at most 5 tiles past entrance
POLE_REACH = 3                 # medium pole supply area is 7x7
POLE_WIRE = 9                  # medium pole wire reach
BEACON_RANGE = 3               # beacon effect reaches 3 tiles past its edge

DIRS = [(0, -1), (1, 0), (0, 1), (-1, 0)]          # N E S W
OPP = [2, 3, 0, 1]
ITEMS = ['plate', 'gear', 'circuit', 'inserter', 'belt', 'green']

MACHINE_TYPES = {
    'G': dict(size=3, entity='assembling-machine-3', recipe='logistic-science-pack',
              needs={'inserter': 1, 'belt': 1}, out=('green', 1),
              modules={'speed-module-3': 1}, min_beacons=1),
    'I': dict(size=3, entity='assembling-machine-3', recipe='inserter',
              needs={'plate': 3, 'gear': 3, 'circuit': 3}, out=('inserter', 3)),
    # one inserter assembler for the whole block: 1x speed module 3 inside, kept out of beacon range
    'I1': dict(size=3, entity='assembling-machine-3', recipe='inserter',
               needs={'plate': 6, 'gear': 6, 'circuit': 6}, out=('inserter', 6),
               modules={'speed-module-3': 1}, no_beacon=True),
    # productivity-moduled science (4x prod 3 + one beacon of 2x speed 3): 2.375 crafting speed,
    # +40% output.  Units here are 0.55/7 items/s, so each assembler takes 5 + 5 and makes 7.
    'Gp': dict(size=3, entity='assembling-machine-3', recipe='logistic-science-pack',
               needs={'inserter': 5, 'belt': 5}, out=('green', 7),
               modules={'productivity-module-3': 4}, min_beacons=1),
    'Ip': dict(size=3, entity='assembling-machine-3', recipe='inserter',
               needs={'plate': 30, 'gear': 30, 'circuit': 30}, out=('inserter', 30),
               modules={'efficiency-module-2': 2}, no_beacon=True),
    'Fp': dict(size=5, entity='foundry', recipe='transport-belt',
               needs={'plate': 15, 'gear': 15}, out=('belt', 30), modules={'efficiency-module-2': 2}),
    'F': dict(size=5, entity='foundry', recipe='transport-belt',
              needs={'plate': 3, 'gear': 3}, out=('belt', 6)),
}
BELT_TIER = 'express'            # entity prefix used in the blueprint
WEIGHTS = dict(belt=2, ug=3, ins=2, pole=2, beacon=4)


def left_vec(d):
    dx, dy = DIRS[d]
    return (dy, -dx)


def far_lane(belt_dir, ins_dir):
    """Lane an inserter moving in ins_dir drops onto a belt flowing belt_dir (0 = left)."""
    return 0 if DIRS[ins_dir] == left_vec(belt_dir) else 1


def perpendicular(d):
    return [(d + 1) % 4, (d + 3) % 4]


def step(t, d, k=1):
    return (t[0] + DIRS[d][0] * k, t[1] + DIRS[d][1] * k)


class Cell:
    def __init__(self, W, H, machines, in_pins, out_pins, max_beacons=None, soft=False,
                 undergrounds=True, poles=True):
        """machines: {type: count}; pins: [{'name', 'items': {item: units}, optional 'x'}]."""
        self.W, self.H = W, H
        self.counts = machines
        self.types = list(machines)
        self.in_pins, self.out_pins = in_pins, out_pins
        self.max_beacons = max_beacons
        # soft: unmet rates become heavily penalised slack, so the solver always has a
        # layout in hand and spends its time driving the slack to zero
        self.soft = soft
        self.slacks = []
        self.use_ug = undergrounds
        self.use_poles = poles     # False: poles are placed afterwards on free tiles
        self.m = cp_model.CpModel()
        self.tiles = [(x, y) for y in range(H) for x in range(W)]

    def inside(self, t):
        return 0 <= t[0] < self.W and 0 <= t[1] < self.H

    def anchors(self, s):
        return [(x, y) for y in range(self.H - s + 1) for x in range(self.W - s + 1)]

    def bool(self):
        return self.m.NewBoolVar('')

    def slack(self, hi):
        v = self.m.NewIntVar(0, hi if self.soft else 0, '')
        self.slacks.append(v)
        return v

    def build(self):
        m, W, H = self.m, self.W, self.H
        TS = self.types
        spec = {T: MACHINE_TYPES[T] for T in TS}
        need_beacons = any(spec[T].get('min_beacons') for T in TS) or bool(self.max_beacons)

        # ---------------- machines and beacons
        self.mach = {T: {a: self.bool() for a in self.anchors(spec[T]['size'])} for T in TS}
        self.bcn = {a: self.bool() for a in self.anchors(3)} if need_beacons else {}
        for T in TS:
            m.Add(sum(self.mach[T].values()) == self.counts[T])
        if self.max_beacons is not None:
            m.Add(sum(self.bcn.values()) <= self.max_beacons)

        def covering(anchors, s, t):
            return [v for a, v in anchors.items() if a[0] <= t[0] < a[0] + s and a[1] <= t[1] < a[1] + s]
        self.cov = {T: {} for T in TS}
        self.covB, self.covAny = {}, {}
        for t in self.tiles:
            for T in TS:
                c = self.bool()
                m.Add(c == sum(covering(self.mach[T], spec[T]['size'], t)))
                self.cov[T][t] = c
            c = self.bool()
            m.Add(c == sum(covering(self.bcn, 3, t)))
            self.covB[t] = c
            c = self.bool()
            m.Add(c == sum(self.cov[T][t] for T in TS))
            self.covAny[t] = c

        # ---------------- belts, undergrounds, inserters, poles
        self.belt, self.ugi, self.ugo, self.ins = {}, {}, {}, {}
        for t in self.tiles:
            for d in range(4):
                u = step(t, d)
                if self.inside(u) or (d == 0 and t[1] == 0 and self.out_pins):
                    self.belt[t, d] = self.bool()
                if self.use_ug and (self.inside(u) or (d == 0 and t[1] == 0 and self.out_pins)):
                    self.ugo[t, d] = self.bool()      # an exit on the top edge can be an output pin
                if self.use_ug and any(self.inside(step(t, d, j)) for j in range(1, UG_MAX + 1)):
                    self.ugi[t, d] = self.bool()
                if self.inside(step(t, OPP[d])) and self.inside(u):
                    self.ins[t, d] = self.bool()
        self.pole = {t: (self.bool() if self.use_poles else 0) for t in self.tiles}
        self.beltAny, self.bl, self.insAny = {}, {}, {}
        for t in self.tiles:
            b = self.bool()
            m.Add(b == sum(self.belt.get((t, d), 0) for d in range(4)))
            bl = self.bool()
            m.Add(bl == b + sum(self.ugi.get((t, d), 0) + self.ugo.get((t, d), 0) for d in range(4)))
            i = self.bool()
            m.Add(i == sum(self.ins.get((t, d), 0) for d in range(4)))
            self.beltAny[t], self.bl[t], self.insAny[t] = b, bl, i
        # belt-like entity facing D (belt or either underground end): inserters can use any of them
        self.dirAt = {}
        for t in self.tiles:
            for D in range(4):
                terms = [x.get((t, D)) for x in (self.belt, self.ugi, self.ugo) if (t, D) in x]
                if terms:
                    v = self.bool()
                    m.Add(v == sum(terms))
                    self.dirAt[t, D] = v
            m.Add(self.covAny[t] + self.covB[t] + self.bl[t] + self.insAny[t] + self.pole[t] <= 1)

        # ---------------- lanes
        self.lane = {(t, l, k): self.bool() for t in self.tiles for l in (0, 1) for k in ITEMS}
        self.F = {(t, l): m.NewIntVar(0, LANE_CAP, '') for t in self.tiles for l in (0, 1)}
        for t in self.tiles:
            for l in (0, 1):
                ks = [self.lane[t, l, k] for k in ITEMS]
                m.Add(sum(ks) <= 1)
                for v in ks:
                    m.AddImplication(v, self.bl[t])
                m.Add(self.F[t, l] <= LANE_CAP * sum(ks))

        # ---------------- belt connections
        # A belt or underground exit pointing into tile u feeds it one of three ways
        # (Factorio's rules): straight in from behind; round a curve, when it is u's only
        # feeder and comes from the side; or as a side-load, when it comes from the side
        # and u also has a feeder behind it or on its other side.  A side-load puts both
        # of the feeder's lanes onto u's near lane.
        self.edges = []          # (src, dst, act)
        self.ident = {}          # edge index -> literal: lanes map straight through
        self.sides = {}          # edge index -> [(D, side-load literal, near lane)]
        self.curves_into = {t: [] for t in self.tiles}
        into = {t: [] for t in self.tiles}
        out_of = {t: [] for t in self.tiles}
        side_lits = {}           # (u, D, side vector) -> side-connection literal
        straight_lit = {}        # (u, D) -> literal for a straight belt feeder into belt (u, D)
        pending = []
        for (t, d), v in list(self.belt.items()) + list(self.ugo.items()):
            u = step(t, d)
            if not self.inside(u):
                continue
            if (u, OPP[d]) in self.belt:
                m.AddBoolOr([v.Not(), self.belt[u, OPP[d]].Not()])        # head-on
            for d2 in range(4):
                if (u, d2) in self.ugo:
                    m.AddBoolOr([v.Not(), self.ugo[u, d2].Not()])         # into an exit's back
                if d2 != d and (u, d2) in self.ugi:
                    m.AddBoolOr([v.Not(), self.ugi[u, d2].Not()])         # side/front of entrance
            # straight: target is a belt or entrance facing the same way
            st = self.bool()
            targets = [x[u, d] for x in (self.belt, self.ugi) if (u, d) in x]
            m.AddBoolOr([st.Not(), v])
            m.Add(st <= sum(targets)) if targets else m.Add(st == 0)
            for tg in targets:
                m.AddBoolOr([v.Not(), tg.Not(), st])
            if (u, d) in self.belt:
                sb = m.NewBoolVar('')
                m.AddBoolAnd([v, self.belt[u, d]]).OnlyEnforceIf(sb)
                m.AddBoolOr([v.Not(), self.belt[u, d].Not(), sb])
                straight_lit.setdefault((u, d), []).append(sb)     # a belt or an exit behind u
            sides = []
            for D in perpendicular(d):
                if (u, D) not in self.belt:
                    continue
                sd = self.bool()
                m.AddBoolAnd([v, self.belt[u, D]]).OnlyEnforceIf(sd)
                m.AddBoolOr([v.Not(), self.belt[u, D].Not(), sd])
                svec = (-DIRS[d][0], -DIRS[d][1])
                near = 0 if svec == left_vec(D) else 1
                side_lits.setdefault((u, D, svec), []).append(sd)
                sides.append((D, sd, near, svec))
            act = self.bool()
            m.Add(act == st + sum(x[1] for x in sides))
            idx = len(self.edges)
            e = (t, u, act)
            self.edges.append(e)
            into[u].append(e)
            out_of[t].append(e)
            pending.append((idx, u, st, sides))
        for idx, u, st, sides in pending:
            ident = [st]
            self.sides[idx] = []
            for D, sd, near, svec in sides:
                blockers = straight_lit.get((u, D), []) + side_lits.get((u, D, (-svec[0], -svec[1])), [])
                cv = self.bool()                     # curve: the only feeder, from the side
                sl = self.bool()                     # side-load
                m.Add(cv + sl == sd)
                for b in blockers:
                    m.AddImplication(b, cv.Not())
                m.AddBoolOr([sd.Not()] + blockers + [cv])
                ident.append(cv)
                self.curves_into[u].append(cv)
                self.sides[idx].append((D, sl, near))
            lit = self.bool()
            m.Add(lit == sum(ident))
            self.ident[idx] = lit
        self.pairs = {}
        for (t, d), v in self.ugi.items():
            opts = []
            for j in range(1, UG_MAX + 1):
                u = step(t, d, j)
                if not self.inside(u) or (u, d) not in self.ugo:
                    continue
                P = self.bool()
                m.AddImplication(P, self.ugo[u, d])
                for i in range(1, j):
                    w = step(t, d, i)
                    for d2 in (d, OPP[d]):
                        for g in (self.ugi.get((w, d2)), self.ugo.get((w, d2))):
                            if g is not None:
                                m.AddBoolOr([P.Not(), g.Not()])
                self.pairs[t, d, j] = P
                opts.append(P)
                idx = len(self.edges)
                e = (t, u, P)
                self.edges.append(e)
                into[u].append(e)
                out_of[t].append(e)
                self.ident[idx] = P
                self.sides[idx] = []
            m.Add(sum(opts) == v)
        for (u, d), v in self.ugo.items():
            m.Add(sum(P for (t, d2, j), P in self.pairs.items() if d2 == d and step(t, d, j) == u) == v)
        # lane contents follow the mapping; flows split into straight/curve and side-load parts
        self.Gi, self.Gs = {}, {}
        for idx, (t, u, act) in enumerate(self.edges):
            for l in (0, 1):
                for k in ITEMS:
                    m.AddBoolOr([self.ident[idx].Not(), self.lane[t, l, k].Not(), self.lane[u, l, k]])
                    for D, sl, near in self.sides[idx]:
                        m.AddBoolOr([sl.Not(), self.lane[t, l, k].Not(), self.lane[u, near, k]])
                g = m.NewIntVar(0, LANE_CAP, '')
                m.Add(g <= LANE_CAP * self.ident[idx])
                self.Gi[idx, l] = g
                for D, sl, near in self.sides[idx]:
                    g = m.NewIntVar(0, LANE_CAP, '')
                    m.Add(g <= LANE_CAP * sl)
                    self.Gs[idx, D, l] = (g, near)

        # ---------------- pins
        self.supply = {}
        self.pin_at = {}
        bottom = [(x, H - 1) for x in range(W)]
        top = [(x, 0) for x in range(W)]
        used_bottom = {t: [] for t in bottom}
        for pin in self.in_pins:
            pos = {t: self.bool() for t in bottom if pin.get('x') in (None, t[0])}
            m.Add(sum(pos.values()) == 1)
            self.pin_at[pin['name']] = pos
            items = list(pin['items'])
            orient = self.bool() if len(items) == 2 else None
            pin['_orient'] = orient
            if orient is not None and pin.get('lanes'):
                m.Add(orient == int(pin['lanes'][0] != items[0]))   # lane order fixed by the cell below
            for t, v in pos.items():
                used_bottom[t].append(v)
                m.AddImplication(v, self.belt[t, 0])
                for e in into[t]:
                    m.AddBoolOr([v.Not(), e[2].Not()])
                if len(items) == 2:
                    a, b = items
                    m.AddBoolOr([v.Not(), orient, self.lane[t, 0, a]])
                    m.AddBoolOr([v.Not(), orient, self.lane[t, 1, b]])
                    m.AddBoolOr([v.Not(), orient.Not(), self.lane[t, 0, b]])
                    m.AddBoolOr([v.Not(), orient.Not(), self.lane[t, 1, a]])
                    assert pin['items'][a] == pin['items'][b], 'two-item pins carry equal amounts'
                    for l in (0, 1):
                        sv = m.NewIntVar(0, pin['items'][a], '')
                        m.Add(sv + self.slack(pin['items'][a]) == pin['items'][a] * v) if not self.soft else m.Add(sv <= pin['items'][a] * v)
                        self.supply.setdefault((t, l), []).append(sv)
                else:
                    (k,) = items
                    amt = pin['items'][k]
                    sc = [m.NewIntVar(0, amt, '') for _ in (0, 1)]
                    m.Add(sum(sc) <= amt * v) if self.soft else m.Add(sum(sc) == amt * v)
                    for l in (0, 1):
                        m.Add(sc[l] <= amt * self.lane[t, l, k])
                        if pin.get('split'):
                            m.Add(sc[l] == pin['split'][l] * v)     # lane split fixed by the cell below
                        self.supply.setdefault((t, l), []).append(sc[l])
        for t, vs in used_bottom.items():
            if len(vs) > 1:
                m.Add(sum(vs) <= 1)
        # output pins: the only belts allowed to point out of the cell
        self.exF = {}
        exit_vars = {t: [] for t in top}
        for pin in self.out_pins:
            pos = {t: self.bool() for t in top if pin.get('x') in (None, t[0])}
            m.Add(sum(pos.values()) == 1)
            self.pin_at[pin['name']] = pos
            for t, v in pos.items():
                exit_vars[t].append(v)
                m.Add(self.belt[t, 0] + (self.ugo[t, 0] if (t, 0) in self.ugo else 0) == 1).OnlyEnforceIf(v)
                for l in (0, 1):
                    for k in ITEMS:
                        if k not in pin['items']:
                            m.AddBoolOr([v.Not(), self.lane[t, l, k].Not()])
                        else:
                            f = m.NewIntVar(0, LANE_CAP, '')
                            m.Add(f <= LANE_CAP * v)
                            m.Add(f <= LANE_CAP * self.lane[t, l, k])
                            self.exF[t, l, k, pin['name']] = f
            for k, amt in pin['items'].items():
                m.Add(sum(f for (t, l, k2, nm), f in self.exF.items() if k2 == k and nm == pin['name'])
                      + self.slack(amt) == amt)
        for t in top:
            if (t, 0) in self.belt:
                m.Add(self.belt[t, 0] + (self.ugo[t, 0] if (t, 0) in self.ugo else 0) == sum(exit_vars[t]))

        # ---------------- inserters
        needs = {k: {} for k in ITEMS}
        for t in self.tiles:
            for k in ITEMS:
                needs[k][t] = sum(self.cov[T][t] for T in TS if k in spec[T]['needs'])
        pickable = [k for k in ITEMS if any(k in spec[T]['needs'] for T in TS)]
        self.qf, self.mB, self.mM = {}, {}, {}
        self.mB_by = {}
        drops = {(t, l): [] for t in self.tiles for l in (0, 1)}
        picks = {(t, l): [] for t in self.tiles for l in (0, 1)}
        for (t, d), v in self.ins.items():
            p, q = step(t, OPP[d]), step(t, d)
            m.AddBoolOr([v.Not(), self.bl[p], self.covAny[p]])
            m.AddBoolOr([v.Not(), self.bl[q], self.covAny[q]])
            m.AddBoolOr([v.Not(), self.bl[p].Not(), self.bl[q].Not()])
            for dd in (d, OPP[d]):
                if (q, dd) in self.dirAt:
                    m.AddBoolOr([v.Not(), self.dirAt[q, dd].Not()])         # no parallel drops
            for T in TS:
                for T2 in TS:
                    if spec[T]['out'][0] not in spec[T2]['needs']:
                        m.AddBoolOr([v.Not(), self.cov[T][p].Not(), self.cov[T2][q].Not()])
            total = []
            for l in (0, 1):
                for k in pickable:
                    f = m.NewIntVar(0, INS_CAP, '')
                    m.Add(f <= INS_CAP * self.lane[p, l, k])
                    m.Add(f <= INS_CAP * needs[k][q])
                    self.qf[t, d, l, k] = f
                    picks[p, l].append(f)
                    total.append(f)
            for T in TS:
                item = spec[T]['out'][0]
                for D in perpendicular(d):
                    if (q, D) not in self.dirAt:
                        continue
                    ln = far_lane(D, d)
                    m.AddBoolOr([v.Not(), self.cov[T][p].Not(), self.dirAt[q, D].Not(), self.lane[q, ln, item]])
                    f = m.NewIntVar(0, INS_CAP, '')
                    m.Add(f <= INS_CAP * self.cov[T][p])
                    m.Add(f <= INS_CAP * self.dirAt[q, D])
                    self.mB[t, d, T, D] = f
                    self.mB_by.setdefault((t, d, T), []).append(f)
                    drops[q, ln].append(f)
                    total.append(f)
                if item in pickable:
                    f = m.NewIntVar(0, INS_CAP, '')
                    m.Add(f <= INS_CAP * self.cov[T][p])
                    m.Add(f <= INS_CAP * needs[item][q])
                    self.mM[t, d, T] = f
                    total.append(f)
            m.Add(sum(total) <= INS_CAP * v)
            m.Add(sum(total) >= v)                                        # no idle inserters
            for cv in self.curves_into[q]:
                m.AddBoolOr([v.Not(), self.covAny[p].Not(), cv.Not()])      # no drops onto curves

        # ---------------- conservation on every lane
        inflow = {(t, l): [] for t in self.tiles for l in (0, 1)}
        outflow = {(t, l): [] for t in self.tiles for l in (0, 1)}
        for idx, (t, u, act) in enumerate(self.edges):
            for l in (0, 1):
                outflow[t, l].append(self.Gi[idx, l])
                inflow[u, l].append(self.Gi[idx, l])
                for D, sl, near in self.sides[idx]:
                    g, _ = self.Gs[idx, D, l]
                    outflow[t, l].append(g)
                    inflow[u, near].append(g)
        for (t2, l2, k, nm), f in self.exF.items():
            outflow[t2, l2].append(f)
        for t in self.tiles:
            for l in (0, 1):
                m.Add(sum(outflow[t, l]) == self.F[t, l])
                m.Add(sum(inflow[t, l]) + sum(self.supply.get((t, l), [])) + sum(drops[t, l])
                      == self.F[t, l] + sum(picks[t, l]))

        # ---------------- machine balance
        for T in TS:
            s = spec[T]['size']
            for a, v in self.mach[T].items():
                fp = {(a[0] + i, a[1] + j) for j in range(s) for i in range(s)}
                ins_in = [(t, d) for (t, d) in self.ins if t not in fp and step(t, d) in fp]
                ins_out = [(t, d) for (t, d) in self.ins if t not in fp and step(t, OPP[d]) in fp]
                for k, need in spec[T]['needs'].items():
                    expr = []
                    for (t, d) in ins_in:
                        expr += [self.qf[t, d, l, k] for l in (0, 1)]
                        expr += [self.mM[t, d, T2] for T2 in TS
                                 if spec[T2]['out'][0] == k and (t, d, T2) in self.mM]
                    m.Add(sum(expr) + self.slack(need) == need).OnlyEnforceIf(v)
                expr = []
                for (t, d) in ins_out:
                    expr += self.mB_by.get((t, d, T), [])
                    if (t, d, T) in self.mM:
                        expr.append(self.mM[t, d, T])
                m.Add(sum(expr) + self.slack(spec[T]['out'][1]) == spec[T]['out'][1]).OnlyEnforceIf(v)
                # redundant in hard mode: enough inserters on the faces (helps propagation)
                if self.soft:
                    continue
                m.Add(sum(self.ins[i] for i in ins_in) >= math.ceil(sum(spec[T]['needs'].values()) / INS_CAP)).OnlyEnforceIf(v)
                m.Add(sum(self.ins[i] for i in ins_out) >= math.ceil(spec[T]['out'][1] / INS_CAP)).OnlyEnforceIf(v)

        # ---------------- beacons
        R = BEACON_RANGE
        for T in TS:
            if spec[T].get('no_beacon'):
                s_ = spec[T]['size']
                for a, v in self.mach[T].items():
                    for (bx, by), b in self.bcn.items():
                        if a[0] <= bx + 2 + R and a[0] + s_ - 1 >= bx - R and a[1] <= by + 2 + R and a[1] + s_ - 1 >= by - R:
                            m.AddBoolOr([v.Not(), b.Not()])
        for T in TS:
            nb = spec[T].get('min_beacons', 0)
            if not nb:
                continue
            for a, v in self.mach[T].items():
                near = [b for (bx, by), b in self.bcn.items()
                        if a[0] <= bx + 2 + R and a[0] + 2 >= bx - R and a[1] <= by + 2 + R and a[1] + 2 >= by - R]
                m.Add(sum(near) >= nb).OnlyEnforceIf(v)

        # ---------------- power coverage (pole connectivity is fixed afterwards)
        PR = POLE_REACH
        if not self.use_poles:
            PR = None

        def poles_touching(x0, y0, s):
            return [self.pole[(px, py)] for (px, py) in self.tiles
                    if x0 <= px + PR and x0 + s - 1 >= px - PR and y0 <= py + PR and y0 + s - 1 >= py - PR]
        if PR is not None:
            for T in TS:
                for a, v in self.mach[T].items():
                    m.Add(sum(poles_touching(a[0], a[1], spec[T]['size'])) >= 1).OnlyEnforceIf(v)
            for a, v in self.bcn.items():
                m.Add(sum(poles_touching(a[0], a[1], 3)) >= 1).OnlyEnforceIf(v)
            for t in self.tiles:
                m.Add(sum(poles_touching(t[0], t[1], 1)) >= 1).OnlyEnforceIf(self.insAny[t])

        # ---------------- identical machines: break symmetry is implicit (no instance labels)
        self.cost = (WEIGHTS['belt'] * sum(self.belt.values())
                     + WEIGHTS['ug'] * (sum(self.ugi.values()) + sum(self.ugo.values()))
                     + WEIGHTS['ins'] * sum(self.ins.values())
                     + WEIGHTS['pole'] * sum(v for v in self.pole.values() if self.use_poles)
                     + WEIGHTS['beacon'] * sum(self.bcn.values()))
        self.unmet = sum(self.slacks)
        m.Minimize(self.cost + 200 * self.unmet)

    # ---------------------------------------------------------------- solve
    def solve(self, seconds, workers=2, log=None, checkpoint=None):
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = seconds
        solver.parameters.num_workers = workers
        t0 = time.time()
        outer = self
        last = [0.0]

        class Progress(cp_model.CpSolverSolutionCallback):
            def on_solution_callback(cb):
                now = time.time() - t0
                if log:
                    log(f'    {now:5.0f}s  cost {cb.ObjectiveValue():.0f}  unmet {cb.Value(outer.unmet)}')
                if checkpoint and now - last[0] > 10:
                    last[0] = now
                    with open(checkpoint, 'w') as f:
                        json.dump(outer.extract(cb), f)
        st = solver.Solve(self.m, Progress())
        self.solve_time = time.time() - t0
        self.status = solver.StatusName(st)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return None
        self.objective = solver.ObjectiveValue()
        self.bound = solver.BestObjectiveBound()
        self.unmet_value = solver.Value(self.unmet)
        return self.extract(solver)

    def extract(self, s):
        val = s.Value
        sol = dict(W=self.W, H=self.H, machines=[], beacons=[], belts=[], undergrounds=[],
                   inserters=[], poles=[], lanes={}, pins={})
        for T, d in self.mach.items():
            for a, v in d.items():
                if val(v):
                    sol['machines'].append(dict(type=T, x=a[0], y=a[1], size=MACHINE_TYPES[T]['size']))
        sol['beacons'] = [dict(x=a[0], y=a[1]) for a, v in self.bcn.items() if val(v)]
        sol['belts'] = [dict(x=t[0], y=t[1], dir=d) for (t, d), v in self.belt.items() if val(v)]
        for (t, d, j), P in self.pairs.items():
            if val(P):
                u = step(t, d, j)
                sol['undergrounds'].append(dict(x=t[0], y=t[1], dir=d, ox=u[0], oy=u[1]))
        for (t, d), v in self.ins.items():
            if not val(v):
                continue
            flows = {}
            for (t2, d2, l, k), f in self.qf.items():
                if (t2, d2) == (t, d) and val(f):
                    flows[f'pick:{k}:lane{l}'] = val(f)
            for (t2, d2, T, D), f in self.mB.items():
                if (t2, d2) == (t, d) and val(f):
                    flows[f'drop:{MACHINE_TYPES[T]["out"][0]}'] = val(f)
            for (t2, d2, T), f in self.mM.items():
                if (t2, d2) == (t, d) and val(f):
                    flows[f'direct:{MACHINE_TYPES[T]["out"][0]}'] = val(f)
            sol['inserters'].append(dict(x=t[0], y=t[1], dir=d, flows=flows))
        sol['poles'] = [dict(x=t[0], y=t[1]) for t, v in self.pole.items() if self.use_poles and val(v)]
        for t in self.tiles:
            if val(self.bl[t]):
                sol['lanes'][f'{t[0]},{t[1]}'] = [
                    ([k for k in ITEMS if val(self.lane[t, l, k])] or [None])[0] for l in (0, 1)
                ] + [val(self.F[t, 0]), val(self.F[t, 1])]
        for name, pos in self.pin_at.items():
            sol['pins'][name] = [t for t, v in pos.items() if val(v)][0]
        for pin in self.in_pins:
            t = tuple(sol['pins'][pin['name']])
            items = list(pin['items'])
            if len(items) == 2:
                o = val(pin['_orient'])
                sol['pins'][pin['name'] + '_lanes'] = [items[o], items[1 - o]]
            else:
                lanes = sol['lanes'][f'{t[0]},{t[1]}']
                sol['pins'][pin['name'] + '_lanes'] = [lanes[0], lanes[1]]
        return sol


# ------------------------------------------------------------- composition
def stack(lower, upper):
    """Put `upper` on top of `lower` (same width); returns one combined layout."""
    assert lower['W'] == upper['W']
    dy = upper['H']
    out = dict(W=lower['W'], H=lower['H'] + upper['H'], machines=[], beacons=[], belts=[], undergrounds=[],
               inserters=[], poles=[], lanes={}, pins={})
    for src, oy in ((upper, 0), (lower, dy)):
        for key in ('machines', 'beacons', 'belts', 'inserters', 'poles'):
            for e in src[key]:
                e2 = dict(e)
                e2['y'] += oy
                out[key].append(e2)
        for u in src['undergrounds']:
            out['undergrounds'].append(dict(u, y=u['y'] + oy, oy=u['oy'] + oy))
        for k, v in src['lanes'].items():
            x, y = map(int, k.split(','))
            out['lanes'][f'{x},{y + oy}'] = v
    return out


# ------------------------------------------------------------- power poles
def connect_poles(sol, seconds=60):
    """Add the fewest poles on free tiles so the whole pole network is connected."""
    W, H = sol['W'], sol['H']
    occupied = set()
    for mc in sol['machines']:
        occupied |= {(mc['x'] + i, mc['y'] + j) for j in range(mc['size']) for i in range(mc['size'])}
    for b in sol['beacons']:
        occupied |= {(b['x'] + i, b['y'] + j) for j in range(3) for i in range(3)}
    for e in sol['belts'] + sol['inserters']:
        occupied.add((e['x'], e['y']))
    for u in sol['undergrounds']:
        occupied |= {(u['x'], u['y']), (u['ox'], u['oy'])}
    fixed = [(p['x'], p['y']) for p in sol['poles']]
    free = [(x, y) for y in range(H) for x in range(W) if (x, y) not in occupied and (x, y) not in fixed]
    cand = fixed + free
    n = len(cand)
    m = cp_model.CpModel()
    use = [m.NewBoolVar('') for _ in range(n)]
    for i in range(len(fixed)):
        m.Add(use[i] == 1)
    # every powered entity needs a pole whose 7x7 supply area touches it
    PR = POLE_REACH
    powered = [(mc['x'], mc['y'], mc['size']) for mc in sol['machines']]
    powered += [(b['x'], b['y'], 3) for b in sol['beacons']]
    powered += [(i['x'], i['y'], 1) for i in sol['inserters']]
    for x0, y0, s_ in powered:
        m.AddBoolOr([use[i] for i, (px, py) in enumerate(cand)
                     if x0 <= px + PR and x0 + s_ - 1 >= px - PR and y0 <= py + PR and y0 + s_ - 1 >= py - PR])
    adj = [[j for j in range(n) if j != i and math.dist(cand[i], cand[j]) <= POLE_WIRE] for i in range(n)]
    flow = {}
    for i in range(n):
        for j in adj[i]:
            flow[i, j] = m.NewIntVar(0, n, '')
            m.Add(flow[i, j] <= n * use[i])
            m.Add(flow[i, j] <= n * use[j])
    for i in range(n):
        out = sum(flow[i, j] for j in adj[i])
        inn = sum(flow[j, i] for j in adj[i])
        if i == 0:
            m.Add(out - inn == sum(use) - 1)
        else:
            m.Add(inn - out == use[i])
    m.Minimize(sum(use))
    s = cp_model.CpSolver()
    s.parameters.max_time_in_seconds = seconds
    if s.Solve(m) not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise RuntimeError('could not connect power poles')
    chosen = [cand[i] for i in range(n) if s.Value(use[i])]
    sol['poles'] = [dict(x=x, y=y) for x, y in chosen]
    tree, seen, todo = [], {chosen[0]}, [chosen[0]]
    while todo:
        a = todo.pop()
        for b in chosen:
            if b not in seen and math.dist(a, b) <= POLE_WIRE:
                seen.add(b)
                todo.append(b)
                tree.append((a, b))
    sol['pole_wires'] = tree
    return sol


# ------------------------------------------------------------- blueprint
def to_blueprint(sol, label='Green block (CP-SAT)'):
    ents = []

    def add(name, x, y, **kw):
        e = dict(entity_number=len(ents) + 1, name=name, position=dict(x=x, y=y))
        e.update(kw)
        ents.append(e)
        return e['entity_number']

    for mc in sol['machines']:
        spec = MACHINE_TYPES[mc['type']]
        s = spec['size']
        kw = dict(recipe=spec['recipe'])
        if spec.get('modules'):
            items, slot = [], 0
            for mod, cnt in spec['modules'].items():
                items.append(dict(id=dict(name=mod), items=dict(in_inventory=[
                    dict(inventory=4, stack=slot + i) for i in range(cnt)])))
                slot += cnt
            kw['items'] = items
        add(spec['entity'], mc['x'] + s / 2, mc['y'] + s / 2, **kw)
    for b in sol['beacons']:
        add('beacon', b['x'] + 1.5, b['y'] + 1.5, items=[dict(id=dict(name='speed-module-3'), items=dict(
            in_inventory=[dict(inventory=1, stack=0), dict(inventory=1, stack=1)]))])
    for b in sol['belts']:
        add(f'{BELT_TIER}-transport-belt', b['x'] + 0.5, b['y'] + 0.5, direction=b['dir'] * 4)
    for u in sol['undergrounds']:
        add(f'{BELT_TIER}-underground-belt', u['x'] + 0.5, u['y'] + 0.5, direction=u['dir'] * 4, type='input')
        add(f'{BELT_TIER}-underground-belt', u['ox'] + 0.5, u['oy'] + 0.5, direction=u['dir'] * 4, type='output')
    for i in sol['inserters']:
        add('fast-inserter', i['x'] + 0.5, i['y'] + 0.5, direction=OPP[i['dir']] * 4)   # faces its pickup
    pole_ids = {}
    for p in sol['poles']:
        pole_ids[(p['x'], p['y'])] = add('medium-electric-pole', p['x'] + 0.5, p['y'] + 0.5)
    wires = [[pole_ids[tuple(a)], 5, pole_ids[tuple(b)], 5] for a, b in sol.get('pole_wires', [])]
    bp = dict(blueprint=dict(icons=[dict(signal=dict(name='logistic-science-pack'), index=1)],
                             entities=ents, wires=wires, item='blueprint', label=label,
                             version=562949953421312))
    raw = json.dumps(bp, separators=(',', ':')).encode()
    return '0' + base64.b64encode(zlib.compress(raw, 9)).decode()


# ------------------------------------------------------------- fixing / hints
def _entity_sets(sol):
    mach = {(mc['type'], mc['x'], mc['y']) for mc in sol['machines']}
    bcn = {(b['x'], b['y']) for b in sol['beacons']}
    belts = {((b['x'], b['y']), b['dir']) for b in sol['belts']}
    ugi = {((u['x'], u['y']), u['dir']) for u in sol['undergrounds']}
    ugo = {((u['ox'], u['oy']), u['dir']) for u in sol['undergrounds']}
    ins = {((i['x'], i['y']), i['dir']) for i in sol['inserters']}
    return mach, bcn, belts, ugi, ugo, ins


def _entity_vars(c, sol):
    mach, bcn, belts, ugi, ugo, ins = _entity_sets(sol)
    for T, d in c.mach.items():
        for a, v in d.items():
            yield v, int((T, a[0], a[1]) in mach)
    for a, v in c.bcn.items():
        yield v, int(a in bcn)
    for k, v in c.belt.items():
        yield v, int(k in belts)
    for k, v in c.ugi.items():
        yield v, int(k in ugi)
    for k, v in c.ugo.items():
        yield v, int(k in ugo)
    for k, v in c.ins.items():
        yield v, int(k in ins)


def fix_entities(c, sol):
    """Pin every placement decision to `sol`; the solver then only finds lanes and flows."""
    for v, val in _entity_vars(c, sol):
        c.m.Add(v == val)


def hint_entities(c, sol):
    for v, val in _entity_vars(c, sol):
        c.m.AddHint(v, val)


def fix_machines(c, sol):
    """Pin only machines and beacons; inserters, belts and undergrounds stay free."""
    mach, bcn, *_ = _entity_sets(sol)
    for T, d in c.mach.items():
        for a, v in d.items():
            c.m.Add(v == int((T, a[0], a[1]) in mach))
    for a, v in c.bcn.items():
        c.m.Add(v == int(a in bcn))
