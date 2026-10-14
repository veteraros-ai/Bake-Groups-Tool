# -*- coding: utf-8 -*-
"""Maya/Qt regression: chapter exclusion, TOC menu, persistence and export scopes."""
import os
import shutil
import tempfile

# Reuse the standalone Maya setup and real scene/geometry fixture.
import test_export_batch_maya as fixture
from test_export_batch_maya import cmds, bg_core, bg_mixins, bg_final_export, APP
import bg_main_window

QtWidgets, QtCore = bg_mixins.QtWidgets, bg_mixins.QtCore


class Harness(fixture.ExportHarness, bg_mixins.TOCMixin, bg_mixins.FinalViewMixin):
    def __init__(self, pairs):
        super(Harness, self).__init__(pairs)
        self.toc_tree = QtWidgets.QTreeWidget(self)
        self.toc_tree.setColumnCount(2)
        self.toc_tree.setItemDelegate(bg_main_window.TOCNameDelegate(self.toc_tree))
        bg_main_window.BakeManagerUI.apply_stylesheet(self)
        self.toc_tree.resize(500, 500)
        self.toc_tree.show()
        self.exp_target = fixture._Index(0)
        self.exp_inc_cage = fixture._Toggle(True)
        self.final_mesh_widgets = []
        self.packages = []

    def run_undoable_bg_action(self, _label, action, *args):
        return action(*args)

    def _material_split_slots(self, _pair):
        return []

    def _ensure_marmoset_plugin(self):
        return True

    def _active_pair(self):
        return next(p for p in self.root_pairs if p['id'] == self.active_root_id)

    def _export_preflight(self, targets, *args, **kwargs):
        assert all(not p.get('export_excluded') for p in targets)
        return [], []

    def _export_marmoset_package(self, targets, snapshots, inc_hp=True, inc_lp=True):
        assert all(not p.get('export_excluded') for p in targets)
        assert set(snapshots) == set(p['id'] for p in targets)
        self.packages.append([p['id'] for p in targets])

    def _scene_name_base(self):
        return "Scene"


def toc_item(owner, pair_id):
    pending = [owner.toc_tree.topLevelItem(i)
               for i in range(owner.toc_tree.topLevelItemCount())]
    while pending:
        item = pending.pop()
        if item.data(0, QtCore.Qt.UserRole) == pair_id:
            return item
        pending.extend(item.child(i) for i in range(item.childCount()))
    raise AssertionError(pair_id)


def run():
    bg_mixins.bg_l10n.set_language('en')
    cmds.file(new=True, force=True)
    pairs = [
        fixture._chapter("Chapter_01", "Book_A", 1),
        fixture._chapter("Chapter_02", "Book_A", 2),
        fixture._chapter("Chapter_03", "Book_B", 3),
    ]
    owner = Harness(pairs)
    export_dir = tempfile.mkdtemp(prefix="bake_groups_exclusion_")
    cmds.file(rename=os.path.join(export_dir, 'exclusion_scene.ma'))
    exports, menus = [], []
    originals = {}

    def replace(obj, name, value):
        originals[(obj, name)] = getattr(obj, name)
        setattr(obj, name, value)

    def capture(path):
        exports.append((os.path.basename(path), cmds.ls(selection=True, long=True) or []))

    def capture_external(meshes, _levels, path, **kwargs):
        exports.append((os.path.basename(path), list(meshes)))
        return True

    def menu_exec(menu, _pos):
        names = [action.text() for action in menu.actions()]
        menus.append(names)
        action = next(a for a in menu.actions()
                      if a.text() in ("Exclude from export", "Include in export"))
        action.trigger()

    replace(cmds, 'fileDialog2', lambda **kwargs: [export_dir])
    replace(cmds, 'inViewMessage', lambda **kwargs: None)
    replace(cmds, 'confirmDialog', lambda **kwargs: bg_mixins.bg_l10n.text('Yes'))
    replace(bg_final_export.FinalExportProcessor, 'export_selected_fbx', staticmethod(capture))
    replace(bg_final_export.FinalExportProcessor, '_export_external_hp_direct',
            staticmethod(capture_external))
    replace(QtWidgets.QMenu, 'exec_', menu_exec)
    try:
        # Trigger the actual menu action. The active chapter must stay yellow/bold.
        owner.refresh_right_panel()
        APP.processEvents()
        item = toc_item(owner, pairs[0]['id'])
        owner.show_toc_context_menu(owner.toc_tree.visualItemRect(item).center())
        assert "Exclude from export" in menus[-1]
        assert pairs[0]['export_excluded']
        item = toc_item(owner, pairs[0]['id'])
        assert item.foreground(0).color().name() == '#ffd54f'
        assert item.font(0).bold()
        item.setSelected(True)
        APP.processEvents()
        image = owner.toc_tree.viewport().grab().toImage()
        # Assert rendered text, not only the item brush: selection used to turn it white.
        assert any(image.pixelColor(x, y).name() == '#ffd54f'
                   for y in range(image.height()) for x in range(image.width()))
        assert bg_core.BakeSessionModel.load()[0]['export_excluded']
        owner.show_toc_context_menu(owner.toc_tree.visualItemRect(item).center())
        assert "Include in export" in menus[-1]
        assert not pairs[0]['export_excluded']
        assert toc_item(owner, pairs[0]['id']).foreground(0).color().name() == '#ffffff'

        # Exclude a chapter containing real HP, LP and cage geometry.
        owner.set_chapter_export_excluded(pairs[1]['id'], True)
        excluded = pairs[1]
        cage_group = bg_mixins.bg_cage.CageProcessor.ensure_chapter_group(excluded['base'])
        cage_mesh = cmds.polyCube(name='Excluded_cage', constructionHistory=False)[0]
        cmds.parent(cage_mesh, cage_group)
        excluded['cage_settings'] = {'export_enabled': True}
        excluded_nodes = cmds.listRelatives("Chapter_02_HP", allDescendents=True, fullPath=True)
        visibility = {n: cmds.getAttr(n + ".visibility") for n in excluded_nodes}
        names_before = list(excluded_nodes)
        allowed = [pairs[0], pairs[2]]
        keys = set(p['id'] for p in allowed)
        snapshots = owner._prepare_export_snapshots(pairs)
        assert set(snapshots) == keys
        assert cmds.listRelatives("Chapter_02_HP", allDescendents=True, fullPath=True) == names_before

        def check(expected):
            assert sorted(n for n, _ in exports) == sorted(expected), exports
            assert all("Chapter_02" not in n for _, nodes in exports for n in nodes), exports
            exports[:] = []

        def files(chapters):
            return [p['base'] + "_" + side + ".fbx" for p in chapters for side in ('HP', 'LP')]

        for scope, chapters in ((0, [pairs[0]]), (1, [pairs[0]]), (2, allowed)):
            owner.exp_scope.value = scope
            owner._export_run()
            check(files(chapters))

        owner.exp_scope.value = 2
        owner.exp_lp_one.value = True
        owner._export_run()
        check([p['base'] + '_HP.fbx' for p in allowed] + ['Scene_LP.fbx'])
        owner.exp_lp_one.value = False
        owner.exp_bymat.value = True
        owner._export_run()
        check([b + '_' + side + '.fbx' for b in ('Book_A', 'Book_B') for side in ('HP', 'LP')])
        owner.exp_bymat.value = False

        owner.batch_export_book()
        check(files([pairs[0]]))
        owner.export_by_material('all')
        check([b + '_' + side + '.fbx' for b in ('Book_A', 'Book_B') for side in ('HP', 'LP')])
        owner.batch_export_all_lp_only()
        check([p['base'] + '_LP.fbx' for p in allowed])
        owner.batch_export_lp_book()
        check(['Chapter_01_LP.fbx'])

        owner.exp_target.value = 1
        owner._export_run()
        assert owner.packages[-1] == [p['id'] for p in allowed]
        owner.exp_target.value = 0
        owner.active_root_id = excluded['id']
        owner.exp_scope.value = 0
        owner._export_run()
        owner.export_final_group_ui()
        owner.export_active_lp_only()
        owner.export_active_cage()
        assert not owner._export_chapter_files(excluded, export_dir, True, True, True, False)
        assert not owner._export_lp_for_pair(excluded, export_dir)
        assert not owner._export_chapter_cage(excluded, export_dir)
        assert not owner.export_chapter_cage_if_enabled(excluded, export_dir)
        check([])

        # Even stale snapshots cannot reintroduce a just-excluded chapter.
        owner._export_lp_combined(pairs, 'Combined', export_dir, snapshots=snapshots)
        check(['Combined_LP.fbx'])
        assert all(cmds.getAttr(n + '.visibility') == state for n, state in visibility.items())

        # Entirely excluded scope must stop before folder selection.
        for pair in pairs:
            owner.set_chapter_export_excluded(pair['id'], True)
        cmds.fileDialog2 = lambda **kwargs: (_ for _ in ()).throw(AssertionError("Unexpected export dialog"))
        owner.exp_scope.value = 2
        owner._export_run()
        owner.export_by_material('all')
        owner.batch_export_all_lp_only()
        check([])

        # Re-include restores export, including a real FBX serialization/reimport.
        owner.set_chapter_export_excluded(pairs[0]['id'], False)
        cmds.fileDialog2 = lambda **kwargs: [export_dir]
        bg_final_export.FinalExportProcessor.export_selected_fbx = originals[
            (bg_final_export.FinalExportProcessor, 'export_selected_fbx')]
        owner.active_root_id = pairs[0]['id']
        owner.exp_scope.value = 2
        owner.exp_files_one.value = True
        owner._export_run()
        written = [n for n in os.listdir(export_dir) if n.lower().endswith('.fbx')]
        assert len(written) == 1, written
        cmds.file(new=True, force=True)
        cmds.file(os.path.join(export_dir, written[0]), i=True, type="FBX", ignoreVersion=True)
        mesh_parents = cmds.listRelatives(cmds.ls(type='mesh'), parent=True) or []
        assert mesh_parents and all('Chapter_01' in n for n in mesh_parents), mesh_parents
        print("CHAPTER_EXCLUSION_PASS: menu, yellow TOC, persistence, scopes, LP merge, FBX roundtrip")
    finally:
        for (obj, name), value in originals.items():
            setattr(obj, name, value)
        owner.close()
        APP.processEvents()
        shutil.rmtree(export_dir)


if __name__ == '__main__':
    try:
        run()
    finally:
        fixture.maya.standalone.uninitialize()
