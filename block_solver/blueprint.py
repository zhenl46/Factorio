"""Blueprint strings <-> layouts (Factorio 2.0 blueprint format)."""
import base64
import json
import math
import zlib

import gamedata as gd
from gamedata import DIRS, OPP

TIER_OF = {}
for _k, _v in gd.BELT_TIERS.items():
    TIER_OF[_v['belt']] = ('belt', _k)
    TIER_OF[_v['underground']] = ('underground', _k)


def decode(s):
    return json.loads(zlib.decompress(base64.b64decode(s.strip()[1:])))


def encode(bp):
    raw = json.dumps(bp, separators=(',', ':')).encode()
    return '0' + base64.b64encode(zlib.compress(raw, 9)).decode()


def to_blueprint(lay, prob, label=None):
    groups = {g.name: g for g in prob.groups}
    ents = []

    def add(name, x, y, **kw):
        e = dict(entity_number=len(ents) + 1, name=name, position=dict(x=x, y=y))
        e.update({k: v for k, v in kw.items() if v is not None})
        ents.append(e)

    def module_items(mods, inventory):
        items, slot = [], 0
        for mod, n in mods.items():
            items.append(dict(id=dict(name=mod), items=dict(in_inventory=[
                dict(inventory=inventory, stack=slot + i) for i in range(n)])))
            slot += n
        return items or None

    for mc in lay['machines']:
        g = groups[mc['group']]
        s = g.size
        rot = mc.get('rot', 0)
        add(g.entity, mc['x'] + s / 2, mc['y'] + s / 2, recipe=g.recipe,
            direction=rot * 4 if rot else None, items=module_items(g.modules, 4))
    for b in lay['beacons']:
        add(prob.beacon_entity, b['x'] + 1.5, b['y'] + 1.5, items=module_items(prob.beacon_modules, 1))
    for b in lay['belts']:
        add(gd.BELT_TIERS[prob.belt]['belt'], b['x'] + 0.5, b['y'] + 0.5, direction=b['dir'] * 4)
    for u in lay['undergrounds']:
        name = gd.BELT_TIERS[u['tier']]['underground']
        add(name, u['x'] + 0.5, u['y'] + 0.5, direction=u['dir'] * 4, type='input')
        add(name, u['ox'] + 0.5, u['oy'] + 0.5, direction=u['dir'] * 4, type='output')
    for i in lay['inserters']:
        add(i['kind'], i['x'] + 0.5, i['y'] + 0.5, direction=OPP[i['dir']] * 4)    # faces its pickup
    for p in lay.get('pipes', []):
        add('pipe', p['x'] + 0.5, p['y'] + 0.5)
    for p in lay.get('pipe_to_ground', []):
        add('pipe-to-ground', p['x'] + 0.5, p['y'] + 0.5, direction=p['face'] * 4)
    bp = dict(blueprint=dict(icons=[dict(signal=dict(name='assembling-machine-3'), index=1)],
                             entities=ents, item='blueprint', label=label or prob.name,
                             version=562949958467584))
    return encode(bp)


def from_blueprint(s, prob):
    """Turn a blueprint string into a layout for `prob` (machines matched to groups by entity and recipe)."""
    bp = decode(s) if isinstance(s, str) else s
    ents = bp['blueprint']['entities']
    by_recipe = {(g.entity, g.recipe): g for g in prob.groups}
    xs, ys = [], []
    for e in ents:
        p = e['position']
        if e['name'] == 'beacon':
            size = 3
        elif e['name'] in gd.ENT['machines']:
            size = gd.machine_size(e['name'])
        else:
            size = 1
        xs.append(p['x'] - size / 2)
        ys.append(p['y'] - size / 2)
    x0, y0 = math.floor(min(xs) + 1e-6), math.floor(min(ys) + 1e-6)
    lay = dict(spec=prob.name, machines=[], beacons=[], belts=[], undergrounds=[], inserters=[], pipes=[],
               pipe_to_ground=[], pins={}, lanes={}, fluids={})
    ug_in, ug_out = [], []
    maxx = maxy = 0
    for e in ents:
        n, p = e['name'], e['position']
        d16 = e.get('direction', 0)
        d = d16 // 4
        if n == prob.beacon_entity or n == 'beacon':
            ax, ay = round(p['x'] - 1.5) - x0, round(p['y'] - 1.5) - y0
            lay['beacons'].append(dict(x=ax, y=ay))
            maxx, maxy = max(maxx, ax + 3), max(maxy, ay + 3)
            continue
        if (n, e.get('recipe')) in by_recipe:
            g = by_recipe[n, e['recipe']]
            s = g.size
            ax, ay = round(p['x'] - s / 2) - x0, round(p['y'] - s / 2) - y0
            lay['machines'].append(dict(group=g.name, x=ax, y=ay, rot=d, size=s))
            maxx, maxy = max(maxx, ax + s), max(maxy, ay + s)
            continue
        tx, ty = math.floor(p['x']) - x0, math.floor(p['y']) - y0
        maxx, maxy = max(maxx, tx + 1), max(maxy, ty + 1)
        if n in TIER_OF:
            kind, tier = TIER_OF[n]
            if kind == 'belt':
                lay['belts'].append(dict(x=tx, y=ty, dir=d))
            else:
                (ug_in if e.get('type') == 'input' else ug_out).append((tier, tx, ty, d))
        elif n in gd.INSERTER_REACH:
            lay['inserters'].append(dict(kind=n, x=tx, y=ty, dir=OPP[d], flows={}))
        elif n == 'pipe':
            lay['pipes'].append(dict(x=tx, y=ty))
        elif n == 'pipe-to-ground':
            lay['pipe_to_ground'].append(dict(x=tx, y=ty, face=d))
        else:
            raise ValueError(f'entity {n} (recipe {e.get("recipe")}) is not in the spec')
    for tier, x, y, d in ug_in:
        for k in range(1, gd.BELT_TIERS[tier]['max_distance'] + 1):
            u = (x + DIRS[d][0] * k, y + DIRS[d][1] * k)
            if (tier, u[0], u[1], d) in ug_out:
                lay['undergrounds'].append(dict(tier=tier, x=x, y=y, dir=d, ox=u[0], oy=u[1]))
                break
        else:
            raise ValueError(f'underground entrance at {(x, y)} has no exit')
    lay['W'], lay['H'] = maxx, maxy
    return lay
