"""Cage identity, world-space edits, UI commit and intersection regressions."""
from types import SimpleNamespace
from unittest import mock
import test_marmoset_bridge_maya as fixture
from test_marmoset_bridge_maya import cmds, bg_mixins, QtWidgets
import maya.api.OpenMaya as om
import bg_cage

Cage = bg_cage.CageProcessor


def points(node):
    return [tuple(p)[:3] for p in Cage._fn_mesh(node)[0].getPoints(om.MSpace.kWorld)]


def near(a, b):
    assert len(a) == len(b)
    error = max((abs(x-y) for p,q in zip(a,b) for x,y in zip(p,q)), default=0)
    assert error < 2e-6, error


class Harness(QtWidgets.QWidget, bg_mixins.FinalViewMixin, bg_mixins.ExportMixin):
    def __init__(self):
        super(Harness, self).__init__()
        self.core = SimpleNamespace(resolve_main_nodes=lambda pair: (None, None, None))
        self._ensure_cage_visible = lambda pair: None
        self.root_pairs = []
        self._expansion_targets_for_test = []

    def _selected_cage_meshes(self, base):
        return [c for c, lp in self._expansion_targets_for_test]

    def _subgroup_for_cage_mesh(self, base, mesh):
        return "Group"

    def _collect_cage_subgroups(self, *args):
        return {"Group": {"hp": [], "lp": [lp for c, lp in self._expansion_targets_for_test]}}


def seed_islands(owner, cage, hp):
    owner._cage_islands = {
        cage: [{"verts": [0,1,2,3], "normal": (0,1,0),
                "normal_space": "world", "faces": [0]}]}
    owner._cage_island_stamps = {cage: {
        "cage": Cage.geometry_stamp(cage),
        "hp": [(hp, Cage.geometry_stamp(hp))]}}


def run():
    cmds.file(new=True, force=True)
    root = Cage.ensure_chapter_group("Probe")
    lp = cmds.polyPlane(name="Probe_Group_low_001", subdivisionsX=1, subdivisionsY=1,
                        constructionHistory=False)[0]
    cage = Cage.batch_create_cages([{"lp": lp, "folder": root, "name": lp+"_cage"}])[0]
    hp = cmds.polyCube(constructionHistory=False)[0]
    initial = points(cage)
    Cage.inflate_existing_cage(cage, .2, lp)
    moved = points(cage)
    near(moved, [(x,y+.2,z) for x,y,z in initial])
    cmds.undo()
    near(points(cage), initial)
    cmds.redo()
    near(points(cage), moved)
    renamed_lp = cmds.rename(lp, "Probe_Group_low_005")
    assert Cage.source_lp(cage) == cmds.ls(renamed_lp, long=True)[0]
    assert Cage.find_cage_for_lp(renamed_lp, Cage.index_existing_cages(root)) == cage
    other_root = Cage.ensure_chapter_group("OtherChapter")
    assert Cage.find_cage_for_lp(renamed_lp, Cage.index_existing_cages(other_root)) == cage
    Cage.prune_orphans(root, {"some_other_name"})
    assert cmds.objExists(cage), "A rename destroyed manual cage work"
    Cage.validate_export_pairs([renamed_lp], [cage])
    try:
        Cage.validate_export_pairs([renamed_lp, hp], [cage])
    except ValueError:
        pass
    else:
        raise AssertionError("Missing LP/Cage pair accepted")
    # Same vertex count, different ordered connectivity is incompatible.
    cmds.polyNormal(renamed_lp, normalMode=0, constructionHistory=False)
    assert Cage.topology_signature(renamed_lp)[0] == Cage.topology_signature(cage)[0]
    assert Cage.topology_signature(renamed_lp) != Cage.topology_signature(cage)
    try:
        Cage.inflate_existing_cage(cage, .1, renamed_lp)
    except ValueError:
        pass
    else:
        raise AssertionError("Same-count changed topology accepted")
    near(points(cage), moved)
    cmds.undo()
    # World unit displacement remains correct with rotated, mirrored parents.
    parent = cmds.group(empty=True)
    renamed_lp = cmds.parent(renamed_lp, parent)[0]
    cmds.setAttr(parent+".scale", -2, 3, .5)
    cmds.setAttr(parent+".rotate", 15, 23, 37)
    # Cage keeps its own transform, while normals come from transformed LP.
    fn, _ = Cage._fn_mesh(renamed_lp)
    dirs = fn.getVertexNormals(True, om.MSpace.kWorld)
    before = points(cage)
    Cage.inflate_existing_cage(cage, .1, renamed_lp)
    near(points(cage), [(p[0]+n.x*.1,p[1]+n.y*.1,p[2]+n.z*.1)
                        for p,n in zip(before,dirs)])
    island = [{"verts":[0,1,2,3], "normal":(0,1,0), "normal_space":"world"}]
    before = points(cage)
    Cage.move_islands(cage, island, .1)
    near(points(cage), [(x,y+.1,z) for x,y,z in before])
    # Numeric input is committed ONCE, and the actual UI edit is undoable.
    owner = Harness()
    panel = owner.build_cage_settings_panel({"base":"ProbeUI"})
    spin = panel.findChildren(QtWidgets.QDoubleSpinBox)[1]
    seed_islands(owner, cage, hp)
    before = points(cage)
    cmds.flushUndo()
    spin.setValue(.1)
    near(points(cage), before)
    spin.editingFinished.emit()
    after = points(cage)
    near(after, [(x,y+.1,z) for x,y,z in before])
    cmds.undo()
    near(points(cage), before)
    assert cmds.undoInfo(query=True, undoQueueEmpty=True), "UI gesture made multiple undo items"
    cmds.redo()
    near(points(cage), after)
    # A failed native Undo command must roll back the live preview and release
    # the closed transaction so the next gesture can work normally.
    import bg_cage_undo
    seed_islands(owner, cage, hp)
    before = points(cage)
    cmds.flushUndo()
    with mock.patch.object(bg_cage_undo, "_submit", side_effect=RuntimeError("Injected commit failure")):
        spin.setValue(.1)
        spin.editingFinished.emit()
    near(points(cage), before)
    assert spin.value() == 0
    assert cmds.undoInfo(query=True, undoQueueEmpty=True)
    spin.setValue(.1)
    spin.editingFinished.emit()
    near(points(cage), [(x,y+.1,z) for x,y,z in before])
    # A geometry edit invalidates old intersection islands.
    cmds.move(.1, 0, 0, hp+".vtx[0]", relative=True)
    try:
        owner._validate_cage_island_cache()
    except ValueError:
        pass
    else:
        raise AssertionError("Stale island cache accepted")
    # Closing/rebuilding the panel during a slider drag finalizes one command.
    seed_islands(owner, cage, hp)
    slider = panel.findChildren(QtWidgets.QSlider)[1]
    before = points(cage)
    cmds.flushUndo()
    slider.setSliderDown(True)
    slider.setValue(800)
    after = points(cage)
    assert after != before
    owner._cage_finish_edits()
    cmds.undo()
    near(points(cage), before)
    assert cmds.undoInfo(query=True, undoQueueEmpty=True)
    panel.close()
    owner.close()
    # Preflight blocks a missing pair and combined material splits, but allows
    # multi-material LP in Separate mode where its topology stays unchanged.
    owner = Harness()
    export_panel = owner.build_export_panel({})
    pair = {"id":"preflight", "base":"Probe"}
    snapshot = {"hp":hp, "lp":renamed_lp, "hp_all":[hp],
                "lp_all":[renamed_lp], "lp_meshes_all":[renamed_lp], "cage_meshes":[cage]}
    errors, _ = owner._export_preflight(
        [pair], True, True, True, {"preflight":snapshot})
    assert not errors, errors
    errors, _ = owner._export_preflight(
        [pair], False, True, True,
        {"preflight":dict(snapshot, lp_all=[renamed_lp,hp])})
    assert errors
    import bg_final_export
    owner.exp_files_one.setChecked(True)
    with mock.patch.object(bg_final_export.FinalExportProcessor,
                           "_get_mesh_materials_and_faces", return_value={"A":[0],"B":[1]}):
        errors, _ = owner._export_preflight(
            [pair], True, True, True, {"preflight":snapshot})
        assert errors
        owner.exp_files_sep.setChecked(True)
        errors, _ = owner._export_preflight(
            [pair], True, True, True, {"preflight":snapshot})
        assert not errors, errors
    export_panel.close()
    owner.close()
    # Expansion's typed value is also applied once with a single Undo item.
    owner = Harness()
    owner._expansion_targets_for_test = [(cage, renamed_lp)]
    owner.core.resolve_main_nodes = lambda pair: (hp, renamed_lp, None)
    panel = owner.build_cage_settings_panel({"base":"Probe"})
    expansion_spin = panel.findChildren(QtWidgets.QDoubleSpinBox)[0]
    before = points(cage)
    dirs = Cage._fn_mesh(renamed_lp)[0].getVertexNormals(True, om.MSpace.kWorld)
    cmds.flushUndo()
    expansion_spin.setValue(.1)
    near(points(cage), before)
    expansion_spin.editingFinished.emit()
    near(points(cage), [(p[0]+n.x*.1,p[1]+n.y*.1,p[2]+n.z*.1)
                        for p,n in zip(before,dirs)])
    cmds.undo()
    near(points(cage), before)
    assert cmds.undoInfo(query=True, undoQueueEmpty=True)
    panel.close()
    owner.close()
    # Reverse HP probes find a small HP crossing a large Cage face interior.
    cmds.file(new=True, force=True)
    large = cmds.polyPlane(width=10, height=10, subdivisionsX=1, subdivisionsY=1,
                           constructionHistory=False)[0]
    small = cmds.polyCube(width=1, height=1, depth=1, constructionHistory=False)[0]
    islands = Cage.find_hp_intersection_islands(large, [small])
    assert islands and islands[0]["faces"] == [0]
    assert islands[0]["normal_space"] == "world"
    # Fully contained cage in closed HP must not be called clean.
    inner = cmds.polyCube(width=.5, height=.5, depth=.5, constructionHistory=False)[0]
    assert Cage.find_hp_intersection_islands(inner, [small])
    inside_islands = Cage.find_hp_intersection_islands(inner, [small])
    before = points(inner)
    Cage.move_islands(inner, inside_islands, .1)
    assert points(inner) != before, "Opposing island normals cancelled all movement"
    # A valid enclosing cage and a far-away HP return no detected crossings.
    outer = cmds.polyCube(width=4, height=4, depth=4, constructionHistory=False)[0]
    assert not Cage.find_hp_intersection_islands(outer, [small])
    cmds.move(20, 0, 0, small)
    assert not Cage.find_hp_intersection_islands(inner, [small])
    hp_instance = cmds.instance(small)[0]
    cmds.move(0, 0, 0, hp_instance, absolute=True)
    assert Cage.find_hp_intersection_islands(large, [hp_instance])
    assert Cage.geometry_stamp(hp_instance, allow_instances=True)
    # Failed Create must leave no unbound duplicates; unsupported sources are
    # rejected before any copying.
    root = Cage.ensure_chapter_group("Failure")
    before_dag = set(cmds.ls(dag=True, long=True))
    spec = {"lp":outer, "folder":root, "name":"Failure_cage"}
    with mock.patch.object(cmds, "parent", side_effect=RuntimeError("Injected parent failure")):
        try:
            Cage.batch_create_cages([spec])
        except RuntimeError:
            pass
        else:
            raise AssertionError("Create failure was swallowed")
    assert set(cmds.ls(dag=True, long=True)) == before_dag
    instance = cmds.instance(outer)[0]
    with mock.patch.object(cmds, "duplicate") as duplicate:
        try:
            Cage.batch_create_cages([dict(spec, lp=instance)])
        except ValueError:
            pass
        else:
            raise AssertionError("Instanced source accepted")
        duplicate.assert_not_called()
    try:
        Cage.find_hp_intersection_islands(inner, ["missing_HP"])
    except ValueError:
        pass
    else:
        raise AssertionError("Failed scan incorrectly reported clean")
    print("CAGE_GEOMETRY_PASS: identity/topology/sculpt, world units, UI Undo/Redo, stale cache, reverse probes, containment")


if __name__ == "__main__":
    try:
        run()
    finally:
        fixture.maya.standalone.uninitialize()
