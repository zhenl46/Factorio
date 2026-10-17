#!/usr/bin/env python3
"""Design a Factorio sub-block from a spec.

  check   SPEC --blueprint BP.txt | --layout L.json   does the model + checker accept this design?
  route   SPEC --placement P.json                      machines/beacons fixed, solver places the rest
  search  SPEC [--tries N]                             propose placements, route each, keep the best
  refine  SPEC --layout L.json                         everything free, warm-started from a layout
  scratch SPEC                                         everything free, no warm start (small blocks)

Writes OUT.json (layout), OUT.txt (blueprint string), OUT.png (picture) and prints the
independent checker's report.
"""
import argparse
import json
import sys
import time

import blueprint
import check
import draw
import model
import place
import spec


def finish(lay, prob, out, title=None):
    R = check.check(lay, prob)
    lay['blueprint'] = blueprint.to_blueprint(lay, prob)
    json.dump(lay, open(out + '.json', 'w'), indent=1)
    open(out + '.txt', 'w').write(lay['blueprint'] + '\n')
    draw.draw(lay, prob, R, out + '.png', title=title)
    print(check.report_text(R))
    print(f'wrote {out}.json, {out}.txt, {out}.png')
    return R


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mode', choices=['check', 'route', 'search', 'refine', 'scratch'])
    ap.add_argument('spec')
    ap.add_argument('--blueprint')
    ap.add_argument('--layout')
    ap.add_argument('--placement')
    ap.add_argument('--seconds', type=float, default=120, help='solver time per route/solve')
    ap.add_argument('--budget', type=float, default=600, help='total time for search')
    ap.add_argument('--tries', type=int, default=20)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--seed', type=int, default=0)
    ap.add_argument('--refine-seconds', type=float, default=0, help='search: refine the best result afterwards')
    ap.add_argument('--out', default='out')
    ap.add_argument('--soft', action='store_true', help='allow missed output targets at a large cost (finds a start fast)')
    a = ap.parse_args(argv)
    prob, raw = spec.load(a.spec)
    print(spec.summary(prob), flush=True)
    log = lambda s: print(s, flush=True)

    if a.mode == 'check':
        lay = blueprint.from_blueprint(open(a.blueprint).read(), prob) if a.blueprint else json.load(open(a.layout))
        M = model.BlockModel(prob).build()
        M.fix(lay)
        sol = M.solve(a.seconds, a.workers)
        print(f'model: {M.status}')
        if sol is None:
            return 1
        finish(sol, prob, a.out)
        return 0

    if a.mode in ('route', 'refine', 'scratch'):
        M = model.BlockModel(prob, soft=a.soft).build()
        if raw.get('fixed'):
            M.fix(raw['fixed'], machines_only=True)
        if a.mode == 'route':
            M.fix(json.load(open(a.placement or a.layout)), machines_only=True)
            if a.layout:
                M.hint(json.load(open(a.layout)))         # continue from an earlier routing
        elif a.mode == 'refine':
            M.hint(json.load(open(a.layout)))
        sol = M.solve(a.seconds, a.workers, log=log, seed=a.seed)
        print(f'{a.mode}: {M.status} in {M.solve_time:.0f}s' + (f', objective {M.objective:.0f}' if sol else '')
              + (f', SHORT by {M.short_value / 1e5:.3g} items/s' if sol and M.short_value else ''))
        if sol is None:
            return 1
        finish(sol, prob, a.out)
        return 0

    # search
    t0, best, banned = time.time(), None, []
    for k in range(a.tries):
        left = a.budget - (time.time() - t0)
        if left < 15:
            break
        res, st = place.propose(prob, seed=a.seed + k, banned=banned, workers=a.workers)
        if res is None:
            print(f'placement: {st}')
            break
        key, pl = res
        banned.append(key)
        M = model.BlockModel(prob).build()
        M.fix(pl, machines_only=True)
        sol = M.solve(min(a.seconds, left - 5), a.workers, seed=a.seed)
        desc = ', '.join(f"{mc['group']}@{mc['x']},{mc['y']}" for mc in pl['machines'])
        print(f'try {k}: {M.status} in {M.solve_time:.0f}s' + (f', cost {M.objective:.0f}' if sol else '') +
              f'  [{desc}; beacons {[(b["x"], b["y"]) for b in pl["beacons"]]}]', flush=True)
        if sol is not None and (best is None or M.objective < best[0]):
            best = (M.objective, sol)
            json.dump(sol, open(a.out + '.json', 'w'), indent=1)
    if best is None:
        print('no routable placement found')
        return 1
    sol = best[1]
    if a.refine_seconds:
        M = model.BlockModel(prob).build()
        M.hint(sol)
        s2 = M.solve(a.refine_seconds, a.workers, log=log)
        if s2 is not None and M.objective <= best[0]:
            sol = s2
            print(f'refined: cost {M.objective:.0f}')
    finish(sol, prob, a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
