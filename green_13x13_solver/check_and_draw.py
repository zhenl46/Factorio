#!/usr/bin/env python3
"""Independent checks of a green-block solution, plus a picture.

Re-derives belt connections, lane contents and item flows from the placed
entities alone (not from the solver's lane variables), then checks overlaps,
belt legality, one item per lane, machine input/output balance, inserter and
lane capacity, beacon and power coverage, pole connectivity, and that the
blueprint string decodes back to the same entities.
"""
import base64
import json
import math
import sys
import zlib
from collections import defaultdict

sys.path.insert(0, __file__.rsplit('/', 1)[0])
from cell import (DIRS, OPP, MACHINE_TYPES as MACHINES, INS_CAP, LANE_CAP,
                  POLE_REACH, POLE_WIRE, BEACON_RANGE, U, far_lane, perpendicular)

PIN_FG = {'plate': 9, 'gear': 9}   # block inputs/outputs in units of U
PIN_C = 6
EXIT_GREEN = 6
COUNTS = {'G': 6, 'I': 2, 'F': 1}
CHECK_POWER = True          # set False for layouts without poles
CHECK_INSERTER_RATE = True  # set False when inserter throughput is ignored
MAX_BEACONS = None


def step(t, d, k=1):
    return (t[0] + DIRS[d][0] * k, t[1] + DIRS[d][1] * k)


def check(sol):
    W, H = sol['W'], sol['H']
    errors = []
    occ = {}

    def put(t, what):
        if not (0 <= t[0] < W and 0 <= t[1] < H):
            errors.append(f'{what} outside grid at {t}')
        if t in occ:
            errors.append(f'overlap at {t}: {occ[t]} and {what}')
        occ[t] = what

    machine_at = {}
    for i, mch in enumerate(sol['machines']):
        for j in range(mch['size']):
            for k in range(mch['size']):
                t = (mch['x'] + k, mch['y'] + j)
                put(t, f"{mch['type']}{i}")
                machine_at[t] = i
    for i, b in enumerate(sol['beacons']):
        for j in range(3):
            for k in range(3):
                put((b['x'] + k, b['y'] + j), f'beacon{i}')
    belt = {}       # tile -> (kind, dir)
    for b in sol['belts']:
        put((b['x'], b['y']), 'belt')
        belt[(b['x'], b['y'])] = ('belt', b['dir'])
    ug_pair = {}
    for u in sol['undergrounds']:
        a, z = (u['x'], u['y']), (u['ox'], u['oy'])
        put(a, 'ug-in')
        put(z, 'ug-out')
        belt[a] = ('ugi', u['dir'])
        belt[z] = ('ugo', u['dir'])
        ug_pair[a] = z
        # nothing on the same axis in between may be an underground
        gap = max(abs(z[0] - a[0]), abs(z[1] - a[1]))
        for i in range(1, gap):
            w = step(a, u['dir'], i)
            for v in sol['undergrounds']:
                if w in ((v['x'], v['y']), (v['ox'], v['oy'])) and v['dir'] in (u['dir'], OPP[u['dir']]):
                    errors.append(f'underground pairing broken between {a} and {z}')
    ins = {}
    for i in sol['inserters']:
        put((i['x'], i['y']), 'inserter')
        ins[(i['x'], i['y'])] = i
    for p in sol['poles']:
        put((p['x'], p['y']), 'pole')

    # ---- belt edges from geometry
    edges = {}
    feeders = defaultdict(list)
    for t, (kind, d) in belt.items():
        if kind == 'ugi':
            edges[t] = ug_pair[t]
            continue
        u = step(t, d)
        if u in belt:
            k2, d2 = belt[u]
            if k2 == 'ugo':
                errors.append(f'belt at {t} points into underground exit {u}')
            elif k2 == 'ugi' and d2 != d:
                errors.append(f'belt at {t} side-loads underground entrance {u}')
            elif k2 == 'belt' and d2 == OPP[d]:
                errors.append(f'head-on belts at {t} and {u}')
            else:
                edges[t] = u
                feeders[u].append(t)
    # how each feeder joins its target: straight, curve (only feeder, from the side) or side-load
    mode = {}           # src tile -> 'id' or ('side', near lane)
    for t, u in edges.items():
        kind, d = belt[t]
        if kind == 'ugi' or belt[u][0] == 'ugi':
            mode[t] = 'id'
            continue
        D = belt[u][1]
        if D == d:
            mode[t] = 'id'
            continue
        fs = feeders[u]
        straight_in = any(belt[f][1] == D for f in fs)
        other_side = any(f != t and belt[f][1] != D for f in fs)
        if straight_in or other_side:
            sv = (t[0] - u[0], t[1] - u[1])
            mode[t] = ('side', 0 if sv == (DIRS[D][1], -DIRS[D][0]) else 1)
        else:
            mode[t] = 'id'     # curve
    curve_tiles = {u for t, u in edges.items() if mode[t] == 'id' and belt[t][0] != 'ugi'
                   and belt[u][0] == 'belt' and belt[u][1] != belt[t][1]}

    pins = sol['pins']
    fg, cp = tuple(pins['fg']), tuple(pins['circuit'])
    ex = tuple(pins.get('science') or pins['exit'])
    left_item = pins['fg_lanes'][0] if 'fg_lanes' in pins else pins['fg_left_lane']
    for T, n in COUNTS.items():
        have = sum(1 for mc in sol['machines'] if mc['type'] == T)
        if have != n:
            errors.append(f'{have} machines of type {T}, expected {n}')
    for t in (fg, cp):
        if belt.get(t) != ('belt', 0) or t[1] != H - 1:
            errors.append(f'input pin {t} is not a north belt on the bottom edge')
        if feeders.get(t):
            errors.append(f'input pin {t} is fed from inside the block')
    if belt.get(ex) not in (('belt', 0), ('ugo', 0)) or ex[1] != 0:
        errors.append(f'exit {ex} is not a north belt on the top edge')
    for t, (kind, d) in belt.items():
        if kind != 'ugi' and not (0 <= step(t, d)[0] < W and 0 <= step(t, d)[1] < H) and t != ex:
            errors.append(f'belt at {t} points out of the block')

    # ---- sources of each lane: pins and inserter drops (machine -> belt)
    out_item = {T: MACHINES[T]['out'][0] for T in MACHINES}
    source = defaultdict(set)
    source[fg, 0].add(left_item)
    source[fg, 1].add('gear' if left_item == 'plate' else 'plate')
    # circuit split across the pin's lanes = lane outflow + picks at the pin tile (nothing drops there)
    cp_lanes = sol['lanes'].get(f'{cp[0]},{cp[1]}', [None, None, 0, 0])
    cp_pick = defaultdict(int)
    for t, i in ins.items():
        if step(t, OPP[i['dir']]) == cp:
            for key, f in i['flows'].items():
                if key.startswith('pick:'):
                    cp_pick[int(key[-1])] += f
    cp_supply = {l: cp_lanes[2 + l] + cp_pick[l] for l in (0, 1)}
    if sum(cp_supply.values()) != PIN_C:
        errors.append(f'circuit pin supplies {sum(cp_supply.values())}, expected {PIN_C}')
    for l in (0, 1):
        if cp_supply[l]:
            source[cp, l].add('circuit')
    for t, i in ins.items():
        d = i['dir']
        p, q = step(t, OPP[d]), step(t, d)
        if p in machine_at and q in belt:
            D = belt[q][1]      # belts and both underground ends take drops from the side
            if D not in perpendicular(d):
                errors.append(f'inserter at {t} drops parallel to the belt')
                continue
            T = sol['machines'][machine_at[p]]['type']
            source[q, far_lane(D, d)].add(out_item[T])
            if q in curve_tiles:
                errors.append(f'inserter at {t} drops onto a curve at {q} (lane placement differs there)')
    # propagate downstream (lane index kept through curves and undergrounds)
    lane_items = defaultdict(set)

    def nxt(a, l):
        md = mode.get(a)
        return (edges[a], l if md == 'id' else md[1]) if a in edges else None
    for (t, l), ks in source.items():
        stack = [(t, l)]
        seen = set()
        while stack:
            a = stack.pop()
            if a in seen:
                continue
            seen.add(a)
            lane_items[a] |= ks
            n = nxt(*a)
            if n:
                stack.append(n)
    for (t, l), ks in lane_items.items():
        if len(ks) > 1:
            errors.append(f'lane {l} at {t} mixes {sorted(ks)}')
    for l in (0, 1):
        if lane_items[ex, l] - {'green'}:
            errors.append(f'exit lane {l} carries {lane_items[ex, l]}')

    # ---- flows: rebuild every lane's balance from inserter flows reported by the solver
    pick = defaultdict(int)
    drop = defaultdict(int)
    m_in = defaultdict(lambda: defaultdict(int))
    m_out = defaultdict(int)
    for t, i in ins.items():
        d = i['dir']
        p, q = step(t, OPP[d]), step(t, d)
        tot = sum(i['flows'].values())
        if CHECK_INSERTER_RATE and tot > INS_CAP:
            errors.append(f'inserter at {t} over capacity ({tot} > {INS_CAP})')
        if tot == 0:
            errors.append(f'inserter at {t} moves nothing')
        for key, f in i['flows'].items():
            kind, item, *rest = key.split(':')
            if kind == 'pick':
                l = int(rest[0][-1])
                if item not in lane_items[p, l]:
                    errors.append(f'inserter at {t} picks {item} from lane {l} at {p}, which carries {lane_items[p, l]}')
                pick[p, l] += f
                if q not in machine_at:
                    errors.append(f'inserter at {t} picks from a belt but does not feed a machine')
                else:
                    m_in[machine_at[q]][item] += f
            else:
                if p not in machine_at:
                    errors.append(f'inserter at {t} reports machine output but picks from {p}')
                    continue
                m_out[machine_at[p]] += f
                if kind == 'drop':
                    drop[q, far_lane(belt[q][1], d)] += f
                else:
                    m_in[machine_at[q]][item] += f
    for idx, mch in enumerate(sol['machines']):
        spec = MACHINES[mch['type']]
        for k, need in spec['needs'].items():
            if m_in[idx][k] != need:
                errors.append(f"{mch['type']} at ({mch['x']},{mch['y']}) receives {m_in[idx][k]} {k}, needs {need}")
        if m_out[idx] != spec['out'][1]:
            errors.append(f"{mch['type']} at ({mch['x']},{mch['y']}) outputs {m_out[idx]}, makes {spec['out'][1]}")
    # walk each lane from its upstream end, carrying flow
    supply = {(fg, 0): PIN_FG[left_item], (fg, 1): PIN_FG['gear' if left_item == 'plate' else 'plate'],
              (cp, 0): cp_supply[0], (cp, 1): cp_supply[1]}
    inflow = defaultdict(int)
    order = []
    indeg = {t: len(feeders.get(t, [])) + (1 if t in ug_pair.values() else 0) for t in belt}
    stack = [t for t, n in indeg.items() if n == 0]
    while stack:
        a = stack.pop()
        order.append(a)
        if a in edges:
            b = edges[a]
            indeg[b] -= 1
            if indeg[b] == 0:
                stack.append(b)
    if len(order) != len(belt):
        errors.append('belt network contains a loop')
    exit_flow = 0
    for a in order:
        for l in (0, 1):
            f = inflow[a, l] + supply.get((a, l), 0) + drop[a, l] - pick[a, l]
            if f < 0:
                errors.append(f'lane {l} at {a} goes negative ({f})')
            if f > LANE_CAP:
                errors.append(f'lane {l} at {a} over capacity')
            if a in edges:
                inflow[nxt(a, l)] += f
            elif a == ex:
                exit_flow += f
            elif f != 0:
                errors.append(f'lane {l} dead-ends at {a} with {f} units left over')
    if exit_flow != EXIT_GREEN:
        errors.append(f'exit carries {exit_flow} units, expected {EXIT_GREEN}')

    # ---- beacons and power
    R, PR = BEACON_RANGE, POLE_REACH
    for mch in sol['machines']:
        if not MACHINES[mch['type']].get('min_beacons'):
            continue
        n = sum(1 for b in sol['beacons']
                if mch['x'] <= b['x'] + 2 + R and mch['x'] + 2 >= b['x'] - R
                and mch['y'] <= b['y'] + 2 + R and mch['y'] + 2 >= b['y'] - R)
        if n < MACHINES[mch['type']]['min_beacons']:
            errors.append(f"green assembler at ({mch['x']},{mch['y']}) has {n} beacons")
    for mch in sol['machines']:
        if MACHINES[mch['type']].get('no_beacon'):
            s_ = mch['size']
            if any(mch['x'] <= b['x'] + 2 + R and mch['x'] + s_ - 1 >= b['x'] - R
                   and mch['y'] <= b['y'] + 2 + R and mch['y'] + s_ - 1 >= b['y'] - R for b in sol['beacons']):
                errors.append(f"{mch['type']} at ({mch['x']},{mch['y']}) is inside a beacon's range")
    if MAX_BEACONS is not None and len(sol['beacons']) > MAX_BEACONS:
        errors.append(f"{len(sol['beacons'])} beacons, at most {MAX_BEACONS} allowed")
    if not CHECK_POWER:
        return errors + _blueprint_errors(sol), lane_items

    def powered(x, y, s):
        return any(x <= p['x'] + PR and x + s - 1 >= p['x'] - PR and y <= p['y'] + PR and y + s - 1 >= p['y'] - PR
                   for p in sol['poles'])
    for mch in sol['machines']:
        if not powered(mch['x'], mch['y'], mch['size']):
            errors.append(f"unpowered {mch['type']} at ({mch['x']},{mch['y']})")
    for b in sol['beacons']:
        if not powered(b['x'], b['y'], 3):
            errors.append(f"unpowered beacon at ({b['x']},{b['y']})")
    for t in ins:
        if not powered(t[0], t[1], 1):
            errors.append(f'unpowered inserter at {t}')
    poles = [(p['x'], p['y']) for p in sol['poles']]
    seen, stack = {poles[0]}, [poles[0]]
    while stack:
        a = stack.pop()
        for b in poles:
            if b not in seen and math.dist(a, b) <= POLE_WIRE:
                seen.add(b)
                stack.append(b)
    if len(seen) != len(poles):
        errors.append('power poles are not all connected')

    return errors + _blueprint_errors(sol), lane_items


def _blueprint_errors(sol):
    errors = []
    if 'blueprint' not in sol:
        return ['no blueprint string']
    bp = json.loads(zlib.decompress(base64.b64decode(sol['blueprint'][1:])))
    ents = bp['blueprint']['entities']
    expected = (len(sol['machines']) + len(sol['beacons']) + len(sol['belts']) + 2 * len(sol['undergrounds'])
                + len(sol['inserters']) + len(sol['poles']))
    if len(ents) != expected:
        errors.append(f'blueprint has {len(ents)} entities, expected {expected}')
    return errors


COLORS = {'plate': '#7d8ea3', 'gear': '#b08a3e', 'circuit': '#3d9a4a', 'inserter': '#c9572e',
          'belt': '#d4b21f', 'green': '#2fbf5b', None: '#c8c8c8'}


def draw(sol, lane_items, path):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as pa
    W, H = sol['W'], sol['H']
    fig, ax = plt.subplots(figsize=(W * 0.55 + 2.5, H * 0.55 + 1))
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect('equal')
    ax.set_xticks(range(W + 1))
    ax.set_yticks(range(H + 1))
    ax.tick_params(labelsize=6)
    ax.grid(True, color='#e5e5e5', lw=0.5)
    names = {'G': 'Logistic\nscience', 'I': 'Inserter', 'I1': 'Inserter', 'F': 'Foundry\n(belts)',
             'Gp': 'Logistic\nscience', 'Ip': 'Inserter', 'Fp': 'Foundry\n(belts)',
             'Gs': 'Logistic\nscience', 'Is': 'Inserter', 'Fs': 'Foundry\n(belts)'}
    fills = {'G': '#cfeed6', 'I': '#f6d7c9', 'I1': '#f6d7c9', 'F': '#f7eab0',
             'Gp': '#cfeed6', 'Ip': '#f6d7c9', 'Fp': '#f7eab0',
             'Gs': '#cfeed6', 'Is': '#f6d7c9', 'Fs': '#f7eab0'}
    for mch in sol['machines']:
        s = mch['size']
        ax.add_patch(pa.Rectangle((mch['x'] + 0.08, mch['y'] + 0.08), s - 0.16, s - 0.16,
                                  fc=fills[mch['type']], ec='#333', lw=1.2))
        ax.text(mch['x'] + s / 2, mch['y'] + s / 2, names[mch['type']], ha='center', va='center', fontsize=7)
    for b in sol['beacons']:
        ax.add_patch(pa.Rectangle((b['x'] + 0.08, b['y'] + 0.08), 2.84, 2.84, fc='#d9d2f2', ec='#5b4bb0', lw=1.2))
        ax.text(b['x'] + 1.5, b['y'] + 1.5, 'Beacon', ha='center', va='center', fontsize=7)

    def belt_tile(x, y, d, style):
        dx, dy = DIRS[d]
        lx, ly = dy, -dx
        for l, sgn in ((0, 1), (1, -1)):
            items = lane_items.get(((x, y), l), set())
            c = COLORS[next(iter(items))] if items else COLORS[None]
            cx, cy = x + 0.5 + sgn * lx * 0.2, y + 0.5 + sgn * ly * 0.2
            ax.add_patch(pa.Rectangle((cx - 0.17, cy - 0.17), 0.34, 0.34, fc=c, ec='none'))
        ax.annotate('', xy=(x + 0.5 + dx * 0.42, y + 0.5 + dy * 0.42), xytext=(x + 0.5 - dx * 0.42, y + 0.5 - dy * 0.42),
                    arrowprops=dict(arrowstyle='-|>', color='#222', lw=0.9, mutation_scale=8))
        if style:
            ax.add_patch(pa.Rectangle((x + 0.05, y + 0.05), 0.9, 0.9, fill=False, ec='#222', lw=1.4, ls=style))
    for b in sol['belts']:
        belt_tile(b['x'], b['y'], b['dir'], None)
    for u in sol['undergrounds']:
        belt_tile(u['x'], u['y'], u['dir'], '--')
        belt_tile(u['ox'], u['oy'], u['dir'], '--')
        ax.plot([u['x'] + 0.5, u['ox'] + 0.5], [u['y'] + 0.5, u['oy'] + 0.5], color='#222', lw=0.8, ls=':', zorder=0)
    for i in sol['inserters']:
        dx, dy = DIRS[i['dir']]
        x, y = i['x'] + 0.5, i['y'] + 0.5
        ax.add_patch(pa.Circle((x, y), 0.36, fc='#e8f1fb', ec='#2b6cb0', lw=1))
        ax.annotate('', xy=(x + dx * 0.33, y + dy * 0.33), xytext=(x - dx * 0.33, y - dy * 0.33),
                    arrowprops=dict(arrowstyle='-|>', color='#2b6cb0', lw=1.2, mutation_scale=9))
    for p in sol['poles']:
        ax.add_patch(pa.Rectangle((p['x'] + 0.3, p['y'] + 0.3), 0.4, 0.4, fc='#8a5a2b', ec='#4a2f14'))
    for a, b in sol.get('pole_wires', []):
        ax.plot([a[0] + 0.5, b[0] + 0.5], [a[1] + 0.5, b[1] + 0.5], color='#b87333', lw=0.6, alpha=0.6)
    handles = [pa.Patch(color=COLORS[k], label=k.replace('green', 'logistic science')
                        .replace('belt', 'transport belts').replace('plate', 'iron plates')
                        .replace('gear', 'gears').replace('circuit', 'circuits').replace('inserter', 'inserters'))
               for k in ['plate', 'gear', 'circuit', 'inserter', 'belt', 'green']]
    ax.legend(handles=handles, loc='upper left', bbox_to_anchor=(1.01, 1), fontsize=7, frameon=False)
    ax.set_title(f"Green block, {W}×{H} — CP-SAT layout", fontsize=10)
    fig.savefig(path, dpi=170, bbox_inches='tight')


if __name__ == '__main__':
    sol = json.load(open(sys.argv[1]))
    errs, lanes = check(sol)
    print('OK: no problems found' if not errs else '\n'.join(errs))
    if len(sys.argv) > 2:
        draw(sol, lanes, sys.argv[2])
        print('drew', sys.argv[2])
