"""Independent checker for block layouts.

Rebuilds everything from the placed entities using Factorio's rules, without the CP-SAT
model: underground pairing (nearest same-tier underground on the axis), belt joins
(straight / curve / side-load), lane contents and flows, inserter reach and drops,
machine rates, beacon counts, pipe networks (no mixing, every fluid box reached by a
source or sink of its fluid, unused boxes untouched) and pins.
"""
import math
from collections import defaultdict

import gamedata as gd
from gamedata import DIRS, OPP


def step(t, d, k=1):
    return (t[0] + DIRS[d][0] * k, t[1] + DIRS[d][1] * k)


def left_vec(d):
    dx, dy = DIRS[d]
    return (dy, -dx)


def far_lane(belt_dir, ins_dir):
    return 0 if DIRS[ins_dir] == left_vec(belt_dir) else 1


class Report:
    def __init__(self):
        self.errors = []
        self.lane_items = defaultdict(set)
        self.fluid_of = {}
        self.stats = {}

    def err(self, msg):
        self.errors.append(msg)


def check(lay, prob):
    R = Report()
    W, H = prob.W, prob.H
    groups = {g.name: g for g in prob.groups}
    inside = lambda t: 0 <= t[0] < W and 0 <= t[1] < H and t not in prob.blocked
    occ = {}

    def put(t, what):
        if not inside(t):
            R.err(f'{what} at {t} is outside the block or on a blocked tile')
        if t in occ:
            R.err(f'overlap at {t}: {occ[t]} and {what}')
        occ[t] = what

    # ---------------- entities
    mach_at, machines = {}, []
    for i, mc in enumerate(lay['machines']):
        g = groups[mc['group']]
        machines.append((g, (mc['x'], mc['y']), mc.get('rot', 0), mc.get('c', 0)))
        for j in range(g.size):
            for k in range(g.size):
                t = (mc['x'] + k, mc['y'] + j)
                put(t, f'{g.name}#{i}')
                mach_at[t] = i
    for i, b in enumerate(lay['beacons']):
        for j in range(3):
            for k in range(3):
                put((b['x'] + k, b['y'] + j), f'beacon#{i}')
    beltlike = {}            # t -> (kind, dir, tier)
    for b in lay['belts']:
        t = (b['x'], b['y'])
        put(t, 'belt')
        beltlike[t] = ('belt', b['dir'], prob.belt)
    for u in lay['undergrounds']:
        a, z = (u['x'], u['y']), (u['ox'], u['oy'])
        put(a, f"{u['tier']} underground entrance")
        put(z, f"{u['tier']} underground exit")
        beltlike[a] = ('ugi', u['dir'], u['tier'])
        beltlike[z] = ('ugo', u['dir'], u['tier'])
    ins = {}
    for i in lay['inserters']:
        t = (i['x'], i['y'])
        put(t, i['kind'])
        ins[t] = i
    pipes = {(p['x'], p['y']) for p in lay.get('pipes', [])}
    for t in pipes:
        put(t, 'pipe')
    ptg = {}
    for p in lay.get('pipe_to_ground', []):
        t = (p['x'], p['y'])
        put(t, 'pipe-to-ground')
        ptg[t] = p['face']

    # ---------------- underground pairing from geometry (nearest same-tier on the axis)
    pair = {}
    for t, (kind, d, tier) in beltlike.items():
        if kind != 'ugi':
            continue
        found = None
        for j in range(1, gd.BELT_TIERS[tier]['max_distance'] + 1):
            u = step(t, d, j)
            e = beltlike.get(u)
            if e and e[0] in ('ugi', 'ugo') and e[2] == tier and e[1] in (d, OPP[d]):
                if e[0] == 'ugo' and e[1] == d:
                    found = u
                else:
                    R.err(f'{tier} underground at {t} meets another {tier} underground at {u} before its exit')
                break
        if found is None:
            R.err(f'{tier} underground entrance at {t} has no exit in range')
        else:
            pair[t] = found
    for u in lay['undergrounds']:
        if pair.get((u['x'], u['y'])) != (u['ox'], u['oy']):
            R.err(f"underground {(u['x'], u['y'])} pairs with {pair.get((u['x'], u['y']))} in game, "
                  f"not {(u['ox'], u['oy'])}")
    exits_paired = set(pair.values())
    for t, (kind, d, tier) in beltlike.items():
        if kind == 'ugo' and t not in exits_paired:
            R.err(f'underground exit at {t} has no entrance')

    # ---------------- belt connections and join types
    edges, feeders = {}, defaultdict(list)
    for t, (kind, d, tier) in beltlike.items():
        if kind == 'ugi':
            if t in pair:
                edges[t] = pair[t]
            continue
        u = step(t, d)
        if u not in beltlike:
            continue
        k2, d2, _ = beltlike[u]
        if k2 == 'ugo':
            R.err(f'belt at {t} points into the underground exit at {u}')
        elif k2 == 'ugi' and d2 != d:
            R.err(f'belt at {t} feeds the side or front of the underground entrance at {u}')
        elif k2 == 'belt' and d2 == OPP[d]:
            R.err(f'belts at {t} and {u} face each other')
        else:
            edges[t] = u
            feeders[u].append(t)
    mode, curve_tiles = {}, set()
    for t, u in edges.items():
        if beltlike[t][0] == 'ugi' or beltlike[u][0] == 'ugi':
            mode[t] = 'id'
            continue
        D, d = beltlike[u][1], beltlike[t][1]
        if d == D:
            mode[t] = 'id'
            continue
        fs = feeders[u]
        straight = any(beltlike[f][1] == D for f in fs)
        other = any(f != t and beltlike[f][1] != D for f in fs)
        if straight or other:
            sv = (t[0] - u[0], t[1] - u[1])
            mode[t] = ('side', 0 if sv == left_vec(D) else 1)
        else:
            mode[t] = 'id'
            curve_tiles.add(u)

    # ---------------- pins
    pins = {p.name: p for p in prob.pins}
    pin_pos = {}
    for name, pp in lay.get('pins', {}).items():
        pin_pos[name] = (pp['x'], pp['y'])
    out_pin_tiles = {}
    in_pin_tiles = {}
    for p in prob.pins:
        if p.name not in pin_pos:
            R.err(f'pin {p.name} has no position')
            continue
        t = pin_pos[p.name]
        if t not in p.positions(W, H):
            R.err(f'pin {p.name} at {t} is not on its allowed side/range')
        if p.kind == 'belt':
            e = beltlike.get(t)
            want = p.outward if p.dir == 'out' else OPP[p.outward]
            ok_kinds = ('belt', 'ugo') if p.dir == 'out' else ('belt', 'ugi')
            if not e or e[1] != want or e[0] not in ok_kinds:
                R.err(f'pin {p.name}: no {"outgoing" if p.dir == "out" else "incoming"} belt at {t}')
            if p.dir == 'out':
                out_pin_tiles[t] = p
            else:
                in_pin_tiles[t] = p
                if feeders.get(t):
                    R.err(f'input pin {p.name} at {t} is fed from inside the block')
    for t, (kind, d, tier) in beltlike.items():
        if kind != 'ugi' and not inside(step(t, d)) and t not in out_pin_tiles:
            R.err(f'belt at {t} points out of the block but is not an output pin')

    # ---------------- lane contents: sources are pins and machine drops
    def nxt(a, l):
        md = mode.get(a)
        if a not in edges:
            return None
        return (edges[a], l if md == 'id' else md[1])

    sources = defaultdict(set)
    for t, p in in_pin_tiles.items():
        lanes = lay['pins'][p.name].get('lanes')
        items = list(p.items)
        if len(items) == 2:
            if not lanes:
                R.err(f'two-item pin {p.name} has no lane order')
                lanes = items
            sources[t, 0].add(lanes[0])
            sources[t, 1].add(lanes[1])
        else:
            sources[t, 0].add(items[0])
            sources[t, 1].add(items[0])
    for t, i in ins.items():
        r = gd.INSERTER_REACH.get(i['kind'])
        if r is None:
            R.err(f'unknown inserter {i["kind"]}')
            continue
        d = i['dir']
        p, q = step(t, OPP[d], r), step(t, d, r)
        if p in mach_at and q in beltlike:
            D = beltlike[q][1]
            if D not in ((d + 1) % 4, (d + 3) % 4):
                R.err(f'inserter at {t} drops along the belt at {q}')
                continue
            if q in curve_tiles:
                R.err(f'inserter at {t} drops onto a curve at {q}')
            for key in i['flows']:
                if key.startswith('drop:'):
                    sources[q, far_lane(D, d)].add(key.split(':')[1])
    for (t, l), ks in sources.items():
        seen, cur = set(), (t, l)
        while cur and cur not in seen:
            seen.add(cur)
            R.lane_items[cur] |= ks
            cur = nxt(*cur)
    for key, ks in R.lane_items.items():
        if len(ks) > 1:
            R.err(f'lane {key[1]} at {key[0]} mixes {sorted(ks)}')

    # ---------------- inserters and machine balance
    m_in = defaultdict(lambda: defaultdict(int))
    m_out = defaultdict(lambda: defaultdict(int))
    pick = defaultdict(int)
    drop = defaultdict(int)
    for t, i in ins.items():
        r = gd.INSERTER_REACH.get(i['kind'], 1)
        d = i['dir']
        p, q = step(t, OPP[d], r), step(t, d, r)
        okp = p in mach_at or p in beltlike
        okq = q in mach_at or q in beltlike
        if not (okp and okq) or (p in beltlike and q in beltlike):
            R.err(f'inserter at {t} must move between a belt and a machine or two machines')
            continue
        if sum(i['flows'].values()) == 0:
            R.err(f'inserter at {t} moves nothing')
        if prob.inserter_cap and sum(i['flows'].values()) > prob.inserter_cap:
            R.err(f'inserter at {t} exceeds its rate')
        for key, f in i['flows'].items():
            kind, item, *rest = key.split(':')
            if kind == 'pick':
                l = int(rest[0])
                if item not in R.lane_items[p, l]:
                    R.err(f'inserter at {t} picks {item} from lane {l} at {p}, which carries {R.lane_items[p, l] or "nothing"}')
                pick[p, l] += f
                if q not in mach_at:
                    R.err(f'inserter at {t} picks from a belt but does not feed a machine')
                else:
                    m_in[mach_at[q]][item] += f
            else:
                if p not in mach_at:
                    R.err(f'inserter at {t} reports a machine output but picks from {p}')
                    continue
                g = machines[mach_at[p]][0]
                if item not in g.items_out:
                    R.err(f'inserter at {t} takes {item} out of {g.name}, which does not make it')
                m_out[mach_at[p]][item] += f
                if kind == 'drop':
                    if q not in beltlike:
                        R.err(f'inserter at {t} drops onto {q}, which is not a belt')
                        continue
                    ln = far_lane(beltlike[q][1], d)
                    if int(rest[0]) != ln:
                        R.err(f'inserter at {t} reports lane {rest[0]}, geometry says lane {ln}')
                    drop[q, ln] += f
                else:
                    if q not in mach_at or item not in machines[mach_at[q]][0].items_in:
                        R.err(f'inserter at {t} inserts {item} into something that does not use it')
                    else:
                        m_in[mach_at[q]][item] += f
    for idx, (g, a, rot, c) in enumerate(machines):
        if c > g.cmax:
            R.err(f'{g.name} at {a} crafts faster than it can')
        for k, per in g.items_in.items():
            if m_in[idx][k] != per * c:
                R.err(f'{g.name} at {a} receives {m_in[idx][k]} {k}, needs {per * c}')
        for k, per in g.items_out.items():
            if m_out[idx][k] != per * c:
                R.err(f'{g.name} at {a} outputs {m_out[idx][k]} {k}, makes {per * c}')

    # ---------------- lane flows
    indeg = {t: len(feeders.get(t, [])) + (1 if t in exits_paired else 0) for t in beltlike}
    order, stack = [], [t for t, n in indeg.items() if n == 0]
    while stack:
        a = stack.pop()
        order.append(a)
        if a in edges:
            b = edges[a]
            indeg[b] -= 1
            if indeg[b] == 0:
                stack.append(b)
    if len(order) != len(beltlike):
        R.err('the belt network contains a loop')
    supply = {}
    for t, p in in_pin_tiles.items():
        lanes_f = lay['lanes'].get(f'{t[0]},{t[1]}', [None, None, 0, 0])
        for l in (0, 1):
            supply[t, l] = lanes_f[2 + l] + pick[t, l] - drop[t, l]
        items = list(p.items)
        for k in items:
            got = sum(supply[t, l] for l in (0, 1) if k in sources[t, l])
            if got > p.items[k]:
                R.err(f'pin {p.name} would need {got} {k}, only {p.items[k]} available')
    inflow = defaultdict(int)
    delivered = defaultdict(int)
    cap = {k: gd.lane_units(k) for k in gd.BELT_TIERS}
    for a in order:
        for l in (0, 1):
            f = inflow[a, l] + supply.get((a, l), 0) + drop[a, l] - pick[a, l]
            if f < 0:
                R.err(f'lane {l} at {a} runs dry ({f})')
            if f > cap[beltlike[a][2]]:
                R.err(f'lane {l} at {a} is over capacity')
            n = nxt(a, l)
            if n:
                inflow[n] += f
            elif a in out_pin_tiles:
                for k in R.lane_items[a, l]:
                    delivered[out_pin_tiles[a].name, k] += f
            elif f:
                R.err(f'lane {l} dead-ends at {a} with {f} left over')
    for t, p in out_pin_tiles.items():
        for k, amt in p.items.items():
            if delivered[p.name, k] < amt:
                R.err(f'pin {p.name} delivers {delivered[p.name, k]} {k}, needs {amt}')

    # ---------------- beacons
    Rb = gd.BEACON['reach']
    if len(lay['beacons']) > prob.max_beacons:
        R.err(f"{len(lay['beacons'])} beacons, at most {prob.max_beacons}")
    for g, a, rot, c in machines:
        s = g.size
        n = sum(1 for b in lay['beacons'] if a[0] <= b['x'] + 2 + Rb and a[0] + s - 1 >= b['x'] - Rb
                and a[1] <= b['y'] + 2 + Rb and a[1] + s - 1 >= b['y'] - Rb)
        if n < g.min_beacons:
            R.err(f'{g.name} at {a} sees {n} beacons, needs {g.min_beacons}')
        if g.no_beacon and n:
            R.err(f'{g.name} at {a} is in range of a beacon')

    # ---------------- fluids
    _check_fluids(R, lay, prob, machines, pipes, ptg, pin_pos, inside)

    R.stats = dict(machines=len(lay['machines']), beacons=len(lay['beacons']), inserters=len(ins),
                   long_inserters=sum(1 for i in ins.values() if gd.INSERTER_REACH.get(i['kind'], 1) > 1),
                   belts=len(lay['belts']), underground_pairs=len(lay['undergrounds']),
                   underground_tiers=sorted({u['tier'] for u in lay['undergrounds']}),
                   pipes=len(pipes), pipe_to_ground=len(ptg))
    return R


def _check_fluids(R, lay, prob, machines, pipes, ptg, pin_pos, inside):
    parent = {}

    def find(a):
        parent.setdefault(a, a)
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a, b):
        parent[find(a)] = find(b)

    def open_face(t, d):
        return t in pipes or ptg.get(t) == d

    for t in list(pipes) + list(ptg):
        find(('tile', t))
        for d in (1, 2):
            u = step(t, d)
            if open_face(t, d) and open_face(u, OPP[d]):
                union(('tile', t), ('tile', u))
    # pipe-to-ground pairing: nearest pipe-to-ground on the axis must face back
    for t, d in ptg.items():
        e = OPP[d]
        partner = None
        for j in range(1, gd.PTG_MAX + 1):
            u = step(t, e, j)
            if u in ptg and ptg[u] in (d, e):
                partner = u if ptg[u] == e else None
                break
        if partner is None:
            R.err(f'pipe-to-ground at {t} has no partner')
        else:
            union(('tile', t), ('tile', partner))
    labels = defaultdict(set)          # component -> fluids
    roles = defaultdict(list)          # component -> (role, fluid)
    port_faces = {}
    for idx, (g, a, rot, c) in enumerate(machines):
        if not g.ports_active:
            continue
        for typ, bi, tile, d in gd.machine_ports(g.entity, rot):
            inside_t = (a[0] + tile[0], a[1] + tile[1])
            u = step(inside_t, d)
            fluid = (g.fluid_in if typ == 'input' else g.fluid_out).get(bi)
            port_faces[inside_t, d] = (idx, fluid)
            connected = open_face(u, OPP[d])
            if fluid is None:
                if connected:
                    R.err(f'{g.name} at {a}: unused fluid box faces a pipe at {u}')
                continue
            if not connected:
                R.err(f'{g.name} at {a}: {typ} box for {fluid} is not connected (needs a pipe at {u})')
                continue
            node = ('box', idx, typ, bi)
            union(node, ('tile', u))
            labels[node].add(fluid)
            roles[node].append((typ, fluid))
    for (inside_t, d), (idx, fluid) in port_faces.items():
        other = port_faces.get((step(inside_t, d), OPP[d]))
        if other and other[0] != idx:
            R.err(f'fluid ports of two machines touch at {inside_t}')
    for p in prob.pins:
        if p.kind != 'pipe' or p.name not in pin_pos:
            continue
        t = pin_pos[p.name]
        if not open_face(t, p.outward):
            R.err(f'pipe pin {p.name} at {t} has no pipe opening outwards')
            continue
        node = ('pin', p.name)
        union(node, ('tile', t))
        labels[node].add(p.fluid)
        roles[node].append(('pin_' + p.dir, p.fluid))
    comp_fluids, comp_roles = defaultdict(set), defaultdict(list)
    for node, fs in labels.items():
        comp_fluids[find(node)] |= fs
    for node, rs in roles.items():
        comp_roles[find(node)] += rs
    for comp, fs in comp_fluids.items():
        if len(fs) > 1:
            R.err(f'pipe network mixes {sorted(fs)}')
    for comp, rs in comp_roles.items():
        kinds = {r for r, f in rs}
        for r, f in rs:
            if r == 'input' and not ({'output', 'pin_in'} & kinds):
                R.err(f'a fluid box needing {f} is not reached by any source of it')
            if r == 'output' and not ({'input', 'pin_out'} & kinds):
                R.err(f'a fluid box producing {f} has nowhere to send it')
            if r == 'pin_in' and 'input' not in kinds:
                R.err(f'input pin for {f} feeds nothing')
            if r == 'pin_out' and 'output' not in kinds:
                R.err(f'output pin for {f} is fed by nothing')
    for t in list(pipes) + list(ptg):
        fs = comp_fluids.get(find(('tile', t)), set())
        R.fluid_of[t] = next(iter(fs)) if len(fs) == 1 else None
        if not fs:
            R.err(f'pipe at {t} is not part of any fluid network')


def report_text(R):
    s = R.stats
    lines = ['OK: no problems found' if not R.errors else f'{len(R.errors)} problem(s):']
    lines += ['  - ' + e for e in R.errors[:40]]
    lines.append(f"entities: {s.get('machines')} machines, {s.get('beacons')} beacons, {s.get('inserters')} inserters "
                 f"({s.get('long_inserters')} long-handed), {s.get('belts')} belts, {s.get('underground_pairs')} underground "
                 f"pairs {s.get('underground_tiers')}, {s.get('pipes')} pipes, {s.get('pipe_to_ground')} pipe-to-ground")
    return '\n'.join(lines)
