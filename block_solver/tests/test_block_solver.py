"""Regression and mechanics tests. Run:  python3 -m pytest -q tests   (or  python3 tests/test_block_solver.py)

Each mechanics test solves a small block built so the mechanic is unavoidable, then checks the
result with the independent checker. The blueprint tests feed real in-game designs back through
the model and checker.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

import blueprint  # noqa: E402
import check      # noqa: E402
import model      # noqa: E402
import spec       # noqa: E402

SPECS = os.path.join(os.path.dirname(HERE), 'specs')
SECONDS, WORKERS = 120, 4


def solve(spec_path, seconds=SECONDS, mutate=None):
    prob, raw = spec.load(os.path.join(SPECS, spec_path))
    if mutate:
        mutate(prob)
    M = model.BlockModel(prob).build()
    if raw.get('fixed'):
        M.fix(raw['fixed'], machines_only=True)
    lay = M.solve(seconds, WORKERS)
    return prob, M, lay


def accept_blueprint(spec_path, bp_file):
    prob, _ = spec.load(os.path.join(SPECS, spec_path))
    lay = blueprint.from_blueprint(open(os.path.join(HERE, 'blueprints', bp_file)).read(), prob)
    M = model.BlockModel(prob).build()
    M.fix(lay)
    sol = M.solve(60, WORKERS)
    assert sol is not None, f'model rejects {bp_file}: {M.status}'
    R = check.check(sol, prob)
    assert not R.errors, R.errors
    return sol


def test_green_13x13_in_game_design_is_accepted():
    accept_blueprint('green_13x13.json', 'green_13x13_tested.txt')


def test_green_13x14_hand_design_is_accepted():
    accept_blueprint('green_13x14.json', 'green_13x14_user.txt')


def test_pipes():
    prob, M, lay = solve('tests/pipes_gear_caster.json')
    assert lay is not None, M.status
    R = check.check(lay, prob)
    assert not R.errors, R.errors
    assert R.stats['pipes'] + R.stats['pipe_to_ground'] >= 1


def test_pipe_to_ground():
    prob, M, lay = solve('tests/pipe_to_ground.json')
    assert lay is not None, M.status
    R = check.check(lay, prob)
    assert not R.errors, R.errors
    assert R.stats['pipe_to_ground'] >= 2


def test_long_handed_inserter():
    prob, M, lay = solve('tests/long_inserter.json')
    assert lay is not None, M.status
    R = check.check(lay, prob)
    assert not R.errors, R.errors
    assert R.stats['long_inserters'] >= 1


def test_weaving():
    prob, M, lay = solve('tests/weaving.json')
    assert lay is not None, M.status
    R = check.check(lay, prob)
    assert not R.errors, R.errors
    assert len(R.stats['underground_tiers']) == 2


def test_weaving_impossible_with_one_tier():
    def one_tier(p):
        p.ug_tiers = ['turbo']
    prob, M, lay = solve('tests/weaving.json', seconds=60, mutate=one_tier)
    assert M.status == 'INFEASIBLE', M.status


def test_checker_catches_mixed_fluids():
    """Feed the solved caster's molten-iron pipe into a foundry that expects copper: must be flagged."""
    prob, M, lay = solve('tests/pipes_gear_caster.json')
    for g in prob.groups:
        g.fluid_in = {1: 'molten-copper'}
    R = check.check(lay, prob)
    assert any('mixes' in e for e in R.errors), R.errors


def test_checker_catches_unused_port_contact():
    """A pipe touching the foundry's unused second input box must be flagged."""
    import gamedata as gd
    prob, _ = spec.load(os.path.join(SPECS, 'tests/pipes_gear_caster.json'))
    M = model.BlockModel(prob).build()
    for (a, r), v in M.mach[0].items():          # foundry fixed at (2, 2) facing north
        M.m.Add(v == int(a == (2, 2) and r == 0))
    lay = M.solve(SECONDS, WORKERS)
    assert lay is not None, M.status
    mc = lay['machines'][0]
    g = prob.groups[0]
    for typ, idx, tile, d in gd.machine_ports(g.entity, mc['rot']):
        if typ == 'input' and idx == 2:
            t = (mc['x'] + tile[0] + gd.DIRS[d][0], mc['y'] + tile[1] + gd.DIRS[d][1])
    bad = json.loads(json.dumps(lay))
    bad['pipes'].append(dict(x=t[0], y=t[1]))
    R = check.check(bad, prob)
    assert any('unused fluid box' in e for e in R.errors), R.errors


if __name__ == '__main__':
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('PASS', name, flush=True)
            except AssertionError as e:
                fails += 1
                print('FAIL', name, str(e)[:300], flush=True)
    sys.exit(1 if fails else 0)
