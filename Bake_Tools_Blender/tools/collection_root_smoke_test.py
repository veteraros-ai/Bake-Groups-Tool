"""Headless acceptance test for HP/LP Collection roots."""

from __future__ import annotations

import sys
import os
from pathlib import Path

import bpy


ADDON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ADDON_ROOT.parent))

import Bake_Tools_Blender as addon  # noqa: E402


def make_collection(name, parent=None):
    collection = bpy.data.collections.new(name)
    (parent or bpy.context.scene.collection).children.link(collection)
    return collection


def make_cube(name, collection, location):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.active_object
    obj.name = name
    for owner in list(obj.users_collection):
        owner.objects.unlink(obj)
    collection.objects.link(obj)
    return obj


def layer_collection_for(layer_collection, target):
    if layer_collection.collection == target:
        return layer_collection
    for child in layer_collection.children:
        result = layer_collection_for(child, target)
        if result is not None:
            return result
    return None


def activate_collection(collection):
    layer = layer_collection_for(bpy.context.view_layer.layer_collection, collection)
    assert layer is not None
    bpy.context.view_layer.active_layer_collection = layer
    bpy.context.view_layer.objects.active = None
    for obj in bpy.context.selected_objects:
        obj.select_set(False)


def select_only(*objects):
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0] if objects else None


def main():
    addon.register()
    hp_collection = make_collection("HP_COLLECTION")
    hp_nested = make_collection("HP_NESTED", hp_collection)
    lp_collection = make_collection("LP_COLLECTION")
    hp_mesh = make_cube("HP_COLLECTION_MESH", hp_nested, (0.0, 0.0, 0.0))
    lp_mesh = make_cube("LP_COLLECTION_MESH", lp_collection, (0.0, 0.0, 0.0))

    from Bake_Tools_Blender.addon.bake_tools_blender.blender_bridge import capture_context

    # Blender can retain a stale active Object while the artist activates a
    # Collection in the Outliner.  A changed active LayerCollection must win.
    select_only(hp_mesh)
    capture_context()
    hp_layer = layer_collection_for(bpy.context.view_layer.layer_collection, hp_collection)
    bpy.context.view_layer.active_layer_collection = hp_layer
    capture_context()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="HP")
    lp_layer = layer_collection_for(bpy.context.view_layer.layer_collection, lp_collection)
    bpy.context.view_layer.active_layer_collection = lp_layer
    capture_context()
    assert "FINISHED" in bpy.ops.bake_tools.pick_object(role="LP")

    state = bpy.context.scene.bake_tools_settings
    assert state.hp_root is None and state.hp_collection == hp_collection
    assert state.lp_root is None and state.lp_collection == lp_collection
    assert state.hp_root_kind == "COLLECTION" and state.lp_root_kind == "COLLECTION"
    assert "FINISHED" in bpy.ops.bake_tools.create_pair()
    pair = state.pairs[0]
    assert pair.hp_collection == hp_collection and pair.lp_collection == lp_collection

    from Bake_Tools_Blender.addon.bake_tools_blender.object_repository import ObjectRepository
    from Bake_Tools_Blender.addon.bake_tools_blender.store import BlenderStateStore

    assert ObjectRepository.classify(pair, hp_mesh) == "HP"
    assert ObjectRepository.classify(pair, lp_mesh) == "LP"
    assert set(ObjectRepository.meshes_under_roots(pair)) == {hp_mesh, lp_mesh}

    select_only(hp_mesh, lp_mesh)
    assert "FINISHED" in bpy.ops.bake_tools.add_subgroup(name="Collection_Group")
    subgroup = pair.subgroups[0]
    assert [ref.target for ref in subgroup.hp_members] == [hp_mesh]
    assert [ref.target for ref in subgroup.lp_members] == [lp_mesh]

    hp_collection.name = "HP_COLLECTION_RENAMED"
    snapshot = BlenderStateStore().snapshot()
    assert snapshot.hp_root_kind == ""
    assert snapshot.hp_object == "" and snapshot.lp_object == ""
    assert snapshot.active_chapter.hp_root_kind == "COLLECTION"
    assert snapshot.active_chapter.hp_object == "HP_COLLECTION_RENAMED"

    assert "FINISHED" in bpy.ops.bake_tools.toggle_visibility(role="HP")
    assert hp_mesh.hide_viewport and not lp_mesh.hide_viewport
    assert "FINISHED" in bpy.ops.bake_tools.toggle_visibility(role="HP")
    assert not hp_mesh.hide_viewport

    roundtrip_path = os.environ.get("BAKE_TOOLS_COLLECTION_TEST_BLEND")
    if roundtrip_path:
        assert "FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=roundtrip_path)
        assert "FINISHED" in bpy.ops.wm.open_mainfile(filepath=roundtrip_path)
        state = bpy.context.scene.bake_tools_settings
        pair = state.pairs[0]
        assert pair.hp_collection is not None and pair.hp_collection.name == "HP_COLLECTION_RENAMED"
        assert pair.lp_collection is not None and pair.lp_collection.name == "LP_COLLECTION"
        assert set(ObjectRepository.meshes_under_roots(pair)) == {
            bpy.data.objects["HP_COLLECTION_MESH"], bpy.data.objects["LP_COLLECTION_MESH"]
        }

    addon.unregister()
    print("BAKE_TOOLS_COLLECTION_ROOT_SMOKE_OK")


if __name__ == "__main__":
    main()
