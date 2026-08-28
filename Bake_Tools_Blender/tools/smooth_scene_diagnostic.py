"""Read-only diagnostic of subgroup smoothing and explicit ZBrush metadata."""

from __future__ import annotations

import bpy


def main():
    state = getattr(bpy.context.scene, "bake_tools_settings", None)
    if state is None:
        print("SMOOTH_DIAGNOSTIC no Bake Tools state")
        return
    collection = bpy.data.collections.get("BakeTools_ZBrush_Layer")
    collection_names = {obj.name for obj in collection.objects} if collection else set()
    registry = {
        ref.target.as_pointer() for ref in state.zbrush_members if ref.target is not None
    }
    print(
        "SMOOTH_DIAGNOSTIC preview={} pairs={} explicit_registry={} collection={}".format(
            state.preview_smoothing, len(state.pairs), len(registry), len(collection_names)
        )
    )
    for pair in state.pairs:
        for subgroup in pair.subgroups:
            if "zbrush" not in subgroup.name.casefold():
                continue
            print("GROUP {!r} smooth={} hp={}".format(
                subgroup.name, subgroup.smooth_level, len(subgroup.hp_members)
            ))
            for ref in subgroup.hp_members:
                obj = ref.target
                if obj is None:
                    continue
                print(
                    "  OBJ {!r} marker={} registry={} collection={} modifiers={}".format(
                        obj.name,
                        bool(obj.get("bake_tools_zbrush", False)),
                        obj.as_pointer() in registry,
                        obj.name in collection_names,
                        [(modifier.name, modifier.type, modifier.show_viewport) for modifier in obj.modifiers],
                    )
                )


if __name__ == "__main__":
    main()
