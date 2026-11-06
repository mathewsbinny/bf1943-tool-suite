r"""Core: collision through a blueprint's mesh alias (level1943.load, a made-up game copy)

level1943.load on a stand-in game copy (test_level1943_made_up.py's DBX writer and game), with a stand-in Havok reader
that gives each collision file its own triangles: a placed object takes its blueprint's own collision; only a
blueprint without one reuses the collision of the object its Mesh names (levelobjects1943: "a blueprint reusing
another object's collision"); an alias naming the blueprint itself, or an object without collision, gives none.

A pytest-style test (README "Tests"): python -B -m pytest <this file>, or run_tests.py.
"""
from types import SimpleNamespace

import fuzzcheck
import numpy as np

import levelobjects1943 as lo
import spawnmap
from bfbc2core import game1943, level1943

M = fuzzcheck.fixtures('core/tests/test_level1943_made_up.py')
SHAPES = {b'own': [(0, 0, 0), (1, 0, 0), (0, 0, 1)], b'other': [(0, 0, 0), (2, 0, 0), (0, 0, 2)]}


class Packfile:
    """A stand-in Havok file: one triangle, its size by the file's contents (SHAPES)."""
    def __init__(self, data, classes=None):
        self.data = data

    def objects_of(self, name):
        return [0] if name == 'hkStorageExtendedMeshShapeMeshSubpartStorage' and self.data in SHAPES else []


def triangles(pf, off):
    return SHAPES[pf.data], [(0, 1, 2)]


def with_mesh(name):
    return M.blueprint('StaticModelEntityData', M.field('Mesh', ref=f'objects/props/{name}_mesh/M'))


def files():
    level = M.dbx_bytes([
        M.instance('Levels.LevelData', 'ld', [M.field('Name', 'Levels/Made_Up')]),
        M.placed('own', 'objects/props/own', 0, 0, 0),             # its own collision, and a Mesh naming 'other'
        M.placed('borrower', 'objects/props/borrower', 100, 0, 0), # none of its own: 'other''s
        M.placed('itself', 'objects/props/itself', 200, 0, 0),     # none, its Mesh names itself
        M.placed('ghost', 'objects/props/haunted', 300, 0, 0),     # none, its Mesh names an object without any
    ])
    return {
        f'levels/{M.LV}.dbx': level,
        'objects/props/own.dbx': with_mesh('other'),
        'objects/props/borrower.dbx': with_mesh('other'),
        'objects/props/itself.dbx': with_mesh('itself'),
        'objects/props/haunted.dbx': with_mesh('ghost'),
        'objects/props/own_havok.res': b'own',
        'objects/props/other_havok.res': b'other',
    }


def load(files):
    saved = (game1943.Game, game1943.find_game, spawnmap.spawn_map, lo.hkpack, lo.havok461)
    game1943.Game = lambda game, level: M.Game(files)
    game1943.find_game = lambda given=None: given or 'made-up copy'
    spawnmap.spawn_map = lambda game, level: {'objectives': []}
    lo.hkpack = SimpleNamespace(Packfile=Packfile)
    lo.havok461 = SimpleNamespace(CLASSES=None, triangles=triangles, convex_box=None)
    try:
        return level1943.load(M.LV, log=lambda line: None)
    finally:
        game1943.Game, game1943.find_game, spawnmap.spawn_map, lo.hkpack, lo.havok461 = saved


def test_mesh_alias_only_without_own_collision():
    lvl = load(files())
    meshes = {lvl.entities[m.entity].guid: m.triangles.reshape(-1, 3) for m in lvl.meshes}
    assert sorted(meshes) == ['borrower', 'own'], sorted(meshes)
    assert meshes['own'].tolist() == [[0, 0, 0], [1, 0, 0], [0, 0, 1]], 'its own, not its Mesh\'s'
    assert meshes['borrower'].tolist() == [[100, 0, 0], [102, 0, 0], [100, 0, 2]], 'the Mesh\'s object\'s'
    assert meshes['own'].dtype == np.float32 and lvl.missing_collision == []
