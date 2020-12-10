from bpy.types import Collection, Context
from typing import Optional

from .objects import get_vertices_of_object
from .plugin_helpers import use_mode_for_object
from .utils import with_context

__all__ = (
    "deselect_all",
    "get_selected_vertices",
    "get_selected_vertices_grouped_by_objects",
    "has_selection",
    "select_only",
)


@with_context
def deselect_all(*, context: Optional[Context] = None):
    """Deselects all objects in the given context (defaults to the current
    context).
    """
    for obj in context.selected_objects:
        obj.select_set(False)


@with_context
def get_selected_objects(*, context: Optional[Context] = None):
    """Returns the list of selected objects in the given context.

    When the context is in object mode, all the objects currently selected in
    the context will be returned. When the context is in any other mode, a list
    consisting of the active object of the context will be returned. This ensures
    that in edit mode, we only return the object that is being edited.

    Returns:
        the list of selected objects, taking into account the current mode
    """
    if context.mode == "OBJECT":
        return context.selected_objects
    else:
        active_object = context.active_object
        return [active_object] if active_object else []


@with_context
def get_selected_vertices(*, context: Optional[Context] = None):
    """Returns the list of currently selected vertices.

    When Blender is in edit mode, this will return the list of vertices that
    are currently selected in the mesh. When Blender is in object mode (or any
    other mode), this will return all the vertices in the selected object(s).
    """
    if context.mode == "OBJECT":
        # In object mode, we want to get the vertices in all the selected
        # objects, no matter whether they are selected or not.
        vertices = []
        for obj in context.selected_objects:
            vertices.extend(get_vertices_of_object(obj))

    else:
        # We need to switch to object mode temporarily so the vertex selection
        # gets updated; the edit mode works in a temporary copy of the mesh
        obj = context.active_object
        with use_mode_for_object("OBJECT"):
            pass

        vertices = [v for v in get_vertices_of_object(obj) if v.select]

    return vertices


@with_context
def get_selected_vertices_grouped_by_objects(*, context: Optional[Context] = None):
    """Returns a mapping that maps objects containing at least one selected
    vertex to the list of selected vertices in that object.

    When Blender is in object mode, the current set of selected objects will be
    considered and each vertex from these objects will be returned. In all other
    modes, only the object being edited (the one that is designated as the
    active object in the context) will be considered.
    """
    if context.mode == "OBJECT":
        # In object mode, we want to get the vertices in all the selected
        # objects, no matter whether they are selected or not.
        return {obj: get_vertices_of_object(obj) for obj in context.selected_objects}

    elif context.active_object:
        # We need to switch to object mode temporarily so the vertex selection
        # gets updated; the edit mode works in a temporary copy of the mesh
        obj = context.active_object
        with use_mode_for_object("OBJECT"):
            pass

        return {obj: [v for v in get_vertices_of_object(obj) if v.select]}

    else:
        return {}


@with_context
def has_selection(*, context: Optional[Context] = None) -> bool:
    """Mode-aware function that decides whether the user has a selection in the
    current mode.

    In object mode, we check the `selected_objects` property of the current
    context. In all other modes, we check the vertices of the `active_object`
    to see if there is at least one vertex selected.
    """
    if context.mode == "OBJECT":
        return len(context.selected_objects) > 0
    else:
        return any(v.select for v in get_vertices_of_object(context.active_object))


@with_context
def select_only(objects, *, context: Optional[Context] = None):
    """Selects only the given objects in the given context and deselects
    everything else (defaults to the current context).

    Can also handle collections; when a collection is selected, it will select
    all the objects and sub-collections.
    """
    deselect_all(context=context)

    if not hasattr(objects, "__iter__"):
        objects = [objects]

    queue = list(objects)
    while queue:
        item = queue.pop()
        if hasattr(item, "select_set"):
            item.select_set(True)
        elif isinstance(item, Collection):
            queue.extend(item.objects)
            queue.extend(item.children)
