"""Block specifications: what a sub-block must contain, and its pins.

A spec is JSON (see specs/). Coordinates: x grows east, y grows south, (0, 0) is the
top-left tile. Pins sit on a side ('top', 'bottom', 'left', 'right'); 'at' is the index
along that side (x for top/bottom, y for left/right), a [lo, hi] range, or omitted.
"""
import json
from dataclasses import dataclass, field

import gamedata as gd

SIDES = {'top': 0, 'right': 1, 'bottom': 2, 'left': 3}     # outward direction of each side


@dataclass
class Group:
    name: str
    entity: str
    recipe: str
    count: int
    size: int
    cmax: int                       # max crafting rate per machine, in SCALE_C units
    items_in: dict                  # item -> units per crafting-rate unit
    items_out: dict
    fluid_in: dict                  # input box index -> fluid
    fluid_out: dict
    ports_active: bool
    rotations: list
    min_beacons: int = 0
    no_beacon: bool = False
    modules: dict = field(default_factory=dict)
    crafts_per_s: float = 0.0
    productivity: float = 0.0


@dataclass
class Pin:
    name: str
    side: str
    kind: str                       # 'belt' or 'pipe'
    dir: str                        # 'in' or 'out'
    at: object = None               # None, int, or (lo, hi)
    items: dict = field(default_factory=dict)    # belt: item -> units/s (max for in, min for out)
    lanes: list = None              # belt: [left item, right item] fixed lane order
    fluid: str = None

    def positions(self, W, H):
        n = W if self.side in ('top', 'bottom') else H
        if self.at is None:
            idx = range(n)
        elif isinstance(self.at, int):
            idx = [self.at]
        else:
            idx = range(self.at[0], self.at[1] + 1)
        out = []
        for i in idx:
            if self.side == 'top':
                out.append((i, 0))
            elif self.side == 'bottom':
                out.append((i, H - 1))
            elif self.side == 'left':
                out.append((0, i))
            else:
                out.append((W - 1, i))
        return out

    @property
    def outward(self):
        return SIDES[self.side]


@dataclass
class Problem:
    name: str
    W: int
    H: int
    belt: str
    ug_tiers: list
    inserters: list
    inserter_cap: int
    beacon_entity: str
    beacon_modules: dict
    max_beacons: int
    groups: list
    pins: list
    blocked: set
    weights: dict
    items: list                     # items that may travel on belts
    fluids: list

    @property
    def lane_cap(self):
        return max(gd.lane_units(t) for t in [self.belt] + self.ug_tiers)


DEFAULT_WEIGHTS = dict(belt=2, underground=3, inserter=2, long_inserter=3, pipe=1, pipe_to_ground=3, beacon=4)


def load(path_or_dict):
    s = json.load(open(path_or_dict)) if isinstance(path_or_dict, str) else path_or_dict
    W, H = s['size']
    bcn = s.get('beacon', {})
    groups = []
    for g in s['machines']:
        ent, rid = g['entity'], g['recipe']
        r = gd.recipe(rid)
        nb = 0 if g.get('no_beacon') else int(g.get('beacons', 0))
        cps, prod = gd.crafting(ent, rid, g.get('modules'), nb, bcn.get('modules'), bcn.get('entity', 'beacon'))
        f_in, f_out = gd.assign_fluid_boxes(ent, rid)
        active = gd.ports_active(ent, rid)
        k = gd.SCALE_I / gd.SCALE_C
        groups.append(Group(
            name=g['name'], entity=ent, recipe=rid, count=int(g['count']), size=gd.machine_size(ent),
            cmax=int(cps * gd.SCALE_C),
            items_in={i: int(round(a * k)) for i, a in r['in'].items() if not gd.is_fluid(i)},
            items_out={i: int(round(a * (1 + prod) * k)) for i, a in r['out'].items() if not gd.is_fluid(i)},
            fluid_in=f_in, fluid_out=f_out, ports_active=active,
            rotations=g.get('rotations', [0, 1, 2, 3] if active else [0]),
            min_beacons=nb, no_beacon=bool(g.get('no_beacon')), modules=g.get('modules', {}),
            crafts_per_s=cps, productivity=prod))
    pins = []
    for p in s.get('pins', []):
        pins.append(Pin(name=p['name'], side=p['side'], kind=p.get('kind', 'belt'), dir=p['dir'],
                        at=tuple(p['at']) if isinstance(p.get('at'), list) else p.get('at'),
                        items={i: gd.rate_units(v) for i, v in p.get('items', {}).items()},
                        lanes=p.get('lanes'), fluid=p.get('fluid')))
    items = set()
    for g in groups:
        items |= set(g.items_in) | set(g.items_out)
    for p in pins:
        items |= set(p.items)
    fluids = set()
    for g in groups:
        if g.ports_active:
            fluids |= set(g.fluid_in.values()) | set(g.fluid_out.values())
    fluids |= {p.fluid for p in pins if p.kind == 'pipe'}
    belt = s.get('belt', 'turbo')
    ug = s.get('underground_tiers', [belt])
    ins = s.get('inserters', ['fast-inserter'])
    rate = s.get('inserter_rate')
    cap = gd.rate_units(rate) if rate else max(gd.lane_units(t) for t in [belt] + ug) * 2
    w = dict(DEFAULT_WEIGHTS)
    w.update(s.get('weights', {}))
    prob = Problem(name=s.get('name', 'block'), W=W, H=H, belt=belt, ug_tiers=ug, inserters=ins,
                   inserter_cap=cap, beacon_entity=bcn.get('entity', 'beacon'),
                   beacon_modules=bcn.get('modules', {}), max_beacons=int(bcn.get('max', 0)),
                   groups=groups, pins=pins, blocked={tuple(t) for t in s.get('blocked', [])},
                   weights=w, items=sorted(items), fluids=sorted(f for f in fluids if f))
    validate(prob)
    return prob, s


def validate(prob):
    names = [g.name for g in prob.groups] + [p.name for p in prob.pins]
    if len(names) != len(set(names)):
        raise ValueError('machine group and pin names must be unique')
    for p in prob.pins:
        if p.kind == 'belt' and not 1 <= len(p.items) <= 2:
            raise ValueError(f'belt pin {p.name} needs one or two items')
        if p.kind == 'pipe' and not p.fluid:
            raise ValueError(f'pipe pin {p.name} needs a fluid')
    for g in prob.groups:
        if g.min_beacons and not prob.max_beacons:
            raise ValueError(f'{g.name} needs beacons but the spec allows none (beacon.max)')


def summary(prob):
    lines = [f'{prob.name}: {prob.W}x{prob.H}, belts {prob.belt}, undergrounds {prob.ug_tiers}, '
             f'inserters {prob.inserters}']
    for g in prob.groups:
        outs = ', '.join(f'{k} {v * g.cmax / gd.SCALE_I:.3g}/s' for k, v in g.items_out.items())
        ins = ', '.join(f'{k} {v * g.cmax / gd.SCALE_I:.3g}/s' for k, v in g.items_in.items())
        fl = ''
        if g.ports_active:
            fl = f'; fluid boxes in {g.fluid_in} out {g.fluid_out}'
        lines.append(f'  {g.count} x {g.name} ({g.entity}, {g.recipe}): per machine {g.crafts_per_s:.3g} crafts/s, '
                     f'+{g.productivity:.0%} prod; in [{ins}] out [{outs}]{fl}')
    for p in prob.pins:
        what = p.fluid if p.kind == 'pipe' else ', '.join(f'{k} {v / gd.SCALE_I:.3g}/s' for k, v in p.items.items())
        lines.append(f'  pin {p.name}: {p.kind} {p.dir} on {p.side} at {p.at}: {what}')
    return '\n'.join(lines)
