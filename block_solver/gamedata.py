"""Game data: machine geometry (fluid ports), recipes, modules, beacons, belts and inserters.

Geometry comes from data/entities.json (extracted from wube/factorio-data); recipes,
machine speeds and module effects from data/recipes.json (trimmed from FactorioLab).
"""
import json
import math
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
ENT = json.load(open(os.path.join(_HERE, 'data', 'entities.json')))
LAB = json.load(open(os.path.join(_HERE, 'data', 'recipes.json')))

FLUIDS = set(LAB['fluids'])
DIRS = [(0, -1), (1, 0), (0, 1), (-1, 0)]          # N E S W, x right, y down
OPP = [2, 3, 0, 1]
BELT_TIERS = ENT['belt_tiers']
INSERTER_REACH = ENT['inserters']
PTG_MAX = ENT['pipe_to_ground_max_distance']
BEACON = ENT['beacon']

SCALE_C = 1000         # crafting-rate units per craft/s
SCALE_I = 100000       # item units per item/s


def is_fluid(name):
    return name in FLUIDS


def machine_size(entity):
    if entity in ENT['machines']:
        return ENT['machines'][entity]['size']
    m = LAB['machines'].get(entity)
    if m and m.get('size') and m['size'][0] == m['size'][1]:
        return m['size'][0]
    raise ValueError(f'unsupported machine {entity!r} (only square crafting machines are modelled)')


def rotate_tile(tile, size, rot):
    """Rotate a tile offset inside a size x size footprint clockwise `rot` quarter turns."""
    x, y = tile
    for _ in range(rot % 4):
        x, y = size - 1 - y, x
    return (x, y)


def machine_ports(entity, rot):
    """Fluid boxes of `entity` facing direction `rot` (0..3): [(type, index, inside tile, dir)]."""
    m = ENT['machines'].get(entity, {'boxes': []})
    size = machine_size(entity)
    out, counters = [], {'input': 0, 'output': 0}
    for b in m['boxes']:
        counters[b['type']] += 1
        out.append((b['type'], counters[b['type']], rotate_tile(b['tile'], size, rot), (b['dir'] + rot) % 4))
    return out


def recipe(rid):
    if rid not in LAB['recipes']:
        raise ValueError(f'unknown recipe {rid!r}')
    return LAB['recipes'][rid]


def assign_fluid_boxes(entity, rid):
    """Map recipe fluids to fluid box indices: ({index: fluid} for inputs, same for outputs)."""
    r = recipe(rid)
    over = ENT['fluidbox_index'].get(rid, {})
    res = {}
    for side, key in (('in', 'input'), ('out', 'output')):
        fluids = [f for f in r[side] if is_fluid(f)]
        nboxes = sum(1 for b in ENT['machines'].get(entity, {'boxes': []})['boxes'] if b['type'] == key)
        taken, mapping = set(), {}
        for f in fluids:
            if f in over.get(side, {}):
                mapping[over[side][f]] = f
                taken.add(over[side][f])
        nxt = 1
        for f in fluids:
            if f in over.get(side, {}):
                continue
            while nxt in taken:
                nxt += 1
            mapping[nxt] = f
            taken.add(nxt)
        if any(i > nboxes for i in mapping):
            raise ValueError(f'{rid} needs more {key} fluid boxes than {entity} has')
        res[key] = mapping
    return res['input'], res['output']


def ports_active(entity, rid):
    """Do the machine's fluid boxes exist with this recipe?"""
    m = ENT['machines'].get(entity, {'boxes': [], 'boxes_off_without_fluid_recipe': True})
    if not m['boxes']:
        return False
    r = recipe(rid)
    uses_fluid = any(is_fluid(k) for k in list(r['in']) + list(r['out']))
    return uses_fluid or not m.get('boxes_off_without_fluid_recipe', True)


def crafting(entity, rid, modules=None, beacons=0, beacon_modules=None, beacon='beacon'):
    """Crafts per second and productivity bonus for one machine."""
    mach = LAB['machines'][entity]
    r = recipe(rid)
    speed_bonus, prod_bonus = 0.0, mach.get('baseEffect', {}).get('productivity', 0.0)
    for mod, n in (modules or {}).items():
        eff = LAB['modules'][mod]
        if eff.get('productivity') and 'productivity' in (r.get('disallowedEffects') or []):
            raise ValueError(f'{rid} does not allow productivity modules')
        speed_bonus += n * eff.get('speed', 0.0)
        prod_bonus += n * eff.get('productivity', 0.0)
    if beacons:
        b = LAB['beacons'][beacon]
        per = sum(n * LAB['modules'][mod].get('speed', 0.0) for mod, n in (beacon_modules or {}).items())
        speed_bonus += beacons * b['profile'][beacons - 1] * b['effectivity'] * per
    if 'productivity' in (r.get('disallowedEffects') or []):
        prod_bonus = 0.0                      # recipe allows no productivity, base bonus included
    speed = mach['speed'] * max(0.2, 1 + speed_bonus)
    return speed / r['time'], prod_bonus


def lane_units(tier):
    return int(round(BELT_TIERS[tier]['lane_rate'] * SCALE_I))


def rate_units(rate):
    return int(round(rate * SCALE_I))
