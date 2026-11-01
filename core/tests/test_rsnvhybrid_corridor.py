r"""Core: rsnvhybrid.plan refuses an empty corridor

rsnvhybrid.plan on a made-up one-area world (no game data): an empty corridor is refused with a ValueError before the
search, as rsnav's rsn_hybrid_plan refuses n_corridor == 0 (RSN_ERR_ARG); until 2026-10-31 it raised a TypeError
from inside the search (corridor_d2 returns None for an empty corridor, found by the type checker). A one-segment
corridor still plans.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
from types import SimpleNamespace

from bfbc2core import rsnvhybrid


def world():
    area = SimpleNamespace(x0=-100.0, z0=-100.0, pitch=0.8)
    return SimpleNamespace(areas=[area], areas_near=lambda x, z: [0], walkable=lambda a, i, j: True,
                           ground=lambda a, x, z: 0.0)


def test_empty_corridor_refused():
    try:
        rsnvhybrid.plan(world(), rsnvhybrid.table('car'), (0, 0, 0), (20, 0, 0), [], 0.0)
    except ValueError as e:
        assert 'corridor is empty' in str(e), str(e)
    else:
        raise AssertionError('an empty corridor was not refused')


def test_corridor_plans():
    r = rsnvhybrid.plan(world(), rsnvhybrid.table('car'), (0, 0, 0), (20, 0, 0), [(0.0, 0.0), (16.0, 0.0)], 0.0)
    assert r['status'] == 'done', r['status']
