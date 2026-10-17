"""Pictures of block layouts."""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as pa

import gamedata as gd
from gamedata import DIRS, OPP

PALETTE = ['#7d8ea3', '#b08a3e', '#3d9a4a', '#c9572e', '#d4b21f', '#2fbf5b', '#8e5cc2', '#1f9bb4',
           '#d1477a', '#6b8e23', '#a0522d', '#4f6bd8']
FLUID_COLORS = {'molten-iron': '#e0603a', 'molten-copper': '#e09a3a', 'lava': '#b3261e', 'water': '#3a8fe0',
                'sulfuric-acid': '#c9c23a', 'petroleum-gas': '#9a5ab8', 'heavy-oil': '#7a4a2a',
                'light-oil': '#d8b04a', 'crude-oil': '#333333', 'lubricant': '#4ab86a'}
TIER_COLORS = {'transport': '#d6b11a', 'fast': '#d63c1a', 'express': '#1a7ed6', 'turbo': '#3fae4a'}


def step(t, d, k=1):
    return (t[0] + DIRS[d][0] * k, t[1] + DIRS[d][1] * k)


def draw(lay, prob, report, path, title=None):
    W, H = prob.W, prob.H
    items = prob.items
    icol = {k: PALETTE[i % len(PALETTE)] for i, k in enumerate(items)}
    fig, ax = plt.subplots(figsize=(W * 0.55 + 3.2, H * 0.55 + 1))
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect('equal')
    ax.set_xticks(range(W + 1))
    ax.set_yticks(range(H + 1))
    ax.tick_params(labelsize=6)
    ax.grid(True, color='#e5e5e5', lw=0.5)
    for t in prob.blocked:
        ax.add_patch(pa.Rectangle(t, 1, 1, fc='#555', ec='none', alpha=0.5))
    gcol = ['#cfeed6', '#f6d7c9', '#f7eab0', '#d6e4f7', '#ecd6f7', '#f7d6e4']
    groups = {g.name: (i, g) for i, g in enumerate(prob.groups)}
    for mc in lay['machines']:
        i, g = groups[mc['group']]
        s = g.size
        ax.add_patch(pa.Rectangle((mc['x'] + 0.08, mc['y'] + 0.08), s - 0.16, s - 0.16,
                                  fc=gcol[i % len(gcol)], ec='#333', lw=1.2))
        ax.text(mc['x'] + s / 2, mc['y'] + s / 2, g.name.replace('_', '\n'), ha='center', va='center', fontsize=7)
        if g.ports_active:
            for typ, bi, tile, d in gd.machine_ports(g.entity, mc.get('rot', 0)):
                fluid = (g.fluid_in if typ == 'input' else g.fluid_out).get(bi)
                cx, cy = mc['x'] + tile[0] + 0.5 + DIRS[d][0] * 0.38, mc['y'] + tile[1] + 0.5 + DIRS[d][1] * 0.38
                ax.add_patch(pa.Circle((cx, cy), 0.13, fc=FLUID_COLORS.get(fluid, '#999') if fluid else 'white',
                                       ec='#333', lw=0.6))
    for b in lay['beacons']:
        ax.add_patch(pa.Rectangle((b['x'] + 0.08, b['y'] + 0.08), 2.84, 2.84, fc='#d9d2f2', ec='#5b4bb0', lw=1.2))
        ax.text(b['x'] + 1.5, b['y'] + 1.5, 'Beacon', ha='center', va='center', fontsize=7)

    def belt_tile(x, y, d, tier, style):
        dx, dy = DIRS[d]
        lx, ly = dy, -dx
        for l, sgn in ((0, 1), (1, -1)):
            ks = report.lane_items.get(((x, y), l), set())
            c = icol.get(next(iter(ks)), '#c8c8c8') if ks else '#c8c8c8'
            cx, cy = x + 0.5 + sgn * lx * 0.2, y + 0.5 + sgn * ly * 0.2
            ax.add_patch(pa.Rectangle((cx - 0.17, cy - 0.17), 0.34, 0.34, fc=c, ec='none'))
        ax.annotate('', xy=(x + 0.5 + dx * 0.42, y + 0.5 + dy * 0.42), xytext=(x + 0.5 - dx * 0.42, y + 0.5 - dy * 0.42),
                    arrowprops=dict(arrowstyle='-|>', color='#222', lw=0.9, mutation_scale=8))
        if style:
            ax.add_patch(pa.Rectangle((x + 0.05, y + 0.05), 0.9, 0.9, fill=False, ec=TIER_COLORS.get(tier, '#222'),
                                      lw=1.6, ls=style))
    for b in lay['belts']:
        belt_tile(b['x'], b['y'], b['dir'], prob.belt, None)
    for u in lay['undergrounds']:
        belt_tile(u['x'], u['y'], u['dir'], u['tier'], '--')
        belt_tile(u['ox'], u['oy'], u['dir'], u['tier'], '--')
        ax.plot([u['x'] + 0.5, u['ox'] + 0.5], [u['y'] + 0.5, u['oy'] + 0.5], color=TIER_COLORS.get(u['tier'], '#222'),
                lw=0.9, ls=':', zorder=0)
    for i in lay['inserters']:
        r = gd.INSERTER_REACH.get(i['kind'], 1)
        dx, dy = DIRS[i['dir']]
        x, y = i['x'] + 0.5, i['y'] + 0.5
        col = '#2b6cb0' if r == 1 else '#b02b8a'
        ax.add_patch(pa.Circle((x, y), 0.36, fc='#e8f1fb' if r == 1 else '#f8e3f2', ec=col, lw=1))
        ax.annotate('', xy=(x + dx * 0.33, y + dy * 0.33), xytext=(x - dx * 0.33, y - dy * 0.33),
                    arrowprops=dict(arrowstyle='-|>', color=col, lw=1.2, mutation_scale=9))
        if r > 1:
            ax.plot([x - dx * (r - 0.4), x + dx * (r - 0.4)], [y - dy * (r - 0.4), y + dy * (r - 0.4)],
                    color=col, lw=0.8, ls='--', alpha=0.7)
    pipes = {(p['x'], p['y']) for p in lay.get('pipes', [])}
    ptg = {(p['x'], p['y']): p['face'] for p in lay.get('pipe_to_ground', [])}
    for t in list(pipes) + list(ptg):
        c = FLUID_COLORS.get(report.fluid_of.get(t), '#888')
        cx, cy = t[0] + 0.5, t[1] + 0.5
        if t in pipes:
            ax.add_patch(pa.Circle((cx, cy), 0.2, fc=c, ec='#333', lw=0.6))
            for d in range(4):
                u = step(t, d)
                if u in pipes or ptg.get(u) == OPP[d]:
                    ax.plot([cx, cx + DIRS[d][0] * 0.5], [cy, cy + DIRS[d][1] * 0.5], color=c, lw=4, solid_capstyle='butt')
        else:
            d = ptg[t]
            ax.add_patch(pa.Rectangle((t[0] + 0.25, t[1] + 0.25), 0.5, 0.5, fc=c, ec='#333', lw=0.6))
            ax.plot([cx, cx + DIRS[d][0] * 0.5], [cy, cy + DIRS[d][1] * 0.5], color=c, lw=4, solid_capstyle='butt')
    seen = set()
    for t, d in ptg.items():
        e = OPP[d]
        for j in range(1, gd.PTG_MAX + 1):
            u = step(t, e, j)
            if u in ptg:
                if (u, t) not in seen:
                    ax.plot([t[0] + 0.5, u[0] + 0.5], [t[1] + 0.5, u[1] + 0.5],
                            color=FLUID_COLORS.get(report.fluid_of.get(t), '#888'), lw=1, ls=':', zorder=0)
                    seen.add((t, u))
                break
    handles = [pa.Patch(color=icol[k], label=k) for k in items]
    handles += [pa.Patch(color=FLUID_COLORS.get(f, '#888'), label=f + ' (pipe)') for f in prob.fluids]
    tiers = sorted({u['tier'] for u in lay['undergrounds']})
    handles += [pa.Patch(fill=False, ec=TIER_COLORS.get(k, '#222'), ls='--', label=f'{k} underground') for k in tiers]
    if any(gd.INSERTER_REACH.get(i['kind'], 1) > 1 for i in lay['inserters']):
        handles.append(pa.Patch(fc='#f8e3f2', ec='#b02b8a', label='long-handed inserter'))
    ax.legend(handles=handles, loc='upper left', bbox_to_anchor=(1.01, 1), fontsize=7, frameon=False)
    ax.set_title(title or f'{prob.name}: {W}x{H}', fontsize=10)
    fig.savefig(path, dpi=160, bbox_inches='tight')
    plt.close(fig)
