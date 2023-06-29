import bpy

from bpy.props import BoolProperty, FloatProperty, IntProperty
from bpy.types import Context, Object
from math import ceil
from typing import List

from sbstudio.plugin.api import get_api
from sbstudio.plugin.constants import Collections
from sbstudio.plugin.model.formation import create_formation
from sbstudio.plugin.model.storyboard import Storyboard
from sbstudio.plugin.utils.evaluator import create_position_evaluator

from .base import StoryboardOperator

__all__ = ("TakeoffOperator",)


class TakeoffOperator(StoryboardOperator):
    """Blender operator that adds a takeoff transition to the show, starting at
    the current frame.
    """

    bl_idname = "skybrush.takeoff"
    bl_label = "Takeoff"
    bl_description = "Add a takeoff maneuver to all the drones"
    bl_options = {"REGISTER", "UNDO"}

    only_with_valid_storyboard = True

    start_frame = IntProperty(
        name="at frame", description="Start frame of the takeoff maneuver"
    )

    velocity = FloatProperty(
        name="with velocity",
        description="Average vertical velocity during the takeoff maneuver",
        default=1.5,
        min=0.1,
        soft_min=0.1,
        soft_max=10,
        unit="VELOCITY",
    )

    altitude = FloatProperty(
        name="to altitude",
        description="Altitude to take off to",
        default=6,
        soft_min=0,
        soft_max=50,
        unit="LENGTH",
    )

    # TODO(ntamas): test whether it is safe to remove this property without
    # breaking compatibility with older versions

    altitude_is_relative = BoolProperty(
        name="Relative Altitude",
        description=(
            "Specifies whether the takeoff altitude is relative to the current "
            "altitude of the drone. Deprecated; not used any more."
        ),
        default=False,
        options={"HIDDEN"},
    )

    altitude_shift = FloatProperty(
        name="Layer height",
        description=(
            "Specifies the difference between altitudes of takeoff layers "
            "for multi-phase takeoffs when multiple drones occupy the same "
            "takeoff slot within safety distance."
        ),
        default=5,
        soft_min=0,
        soft_max=50,
        unit="LENGTH",
    )

    """
    delay = FloatProperty(
        name="Delay",
        description="Delay between takeoffs of consecutive drones in the takeoff sequence",
        default=0,
        min=0,
        soft_min=0,
        soft_max=5,
        unit="TIME",
        subtype="TIME",
    )

    order = EnumProperty(
        name="Order",
        description="Order of drones in the takeoff sequence",
        items=(
            (
                "DEFAULT",
                "Default",
                "Use the order in which the drones appear in the drone collection",
            ),
            ("NAME", "Name", "Sort the drones alphabetically"),
            ("XY", "X axis first", "Sort the drones by X axis first, then by Y axis"),
            ("YX", "Y axis first", "Sort the drones by Y axis first, then by X axis"),
        ),
        default="DEFAULT",
    )

    reverse_order = BoolProperty(
        name="Reverse ordering",
        description="Whether to reverse the ordering of the takeoff sequence",
        default=False,
    )
    """

    @classmethod
    def poll(cls, context):
        if not super().poll(context):
            return False

        drones = Collections.find_drones(create=False)
        return drones is not None and len(drones.objects) > 0

    def invoke(self, context, event):
        self.start_frame = context.scene.frame_current
        return context.window_manager.invoke_props_dialog(self)

    def execute_on_storyboard(self, storyboard, entries, context):
        return {"FINISHED"} if self._run(storyboard, context=context) else {"CANCELLED"}

    def _run(self, storyboard: Storyboard, *, context) -> bool:
        bpy.ops.skybrush.prepare()

        if not self._validate_start_frame(context):
            return False

        drones = Collections.find_drones().objects
        if not drones:
            return False

        self._sort_drones(drones)

        # Evaluate the initial positions of the drones
        with create_position_evaluator() as get_positions_of:
            source = get_positions_of(drones, frame=self.start_frame)

        # Figure out how many phases we will need, based on the current safety
        # threshold and the arrangement of the drones
        min_distance: float = (
            context.scene.skybrush.safety_check.proximity_warning_threshold
        )
        groups = get_api().decompose_points(
            source, min_distance=min_distance, method="balanced"
        )
        num_groups = max(groups) + 1

        # Prepare the points of the target formation to take off to
        altitude_shift_per_group = self.altitude_shift
        target = [
            (x, y, self.altitude + (num_groups - group - 1) * altitude_shift_per_group)
            for (x, y, _), group in zip(source, groups)
        ]

        # Calculate the Z distance to travel for each drone
        diffs = [t[2] - s[2] for s, t in zip(source, target)]
        if min(diffs) < 0:
            dist = abs(min(diffs))
            self.report(
                {"ERROR"},
                f"At least one drone would have to take off downwards by {dist}m",
            )
            return False

        # Calculate takeoff durations from distances to travel and the
        # average velocity
        fps = context.scene.render.fps
        takeoff_durations = [ceil((diff / self.velocity) * fps) for diff in diffs]

        # We ensure that drones arrive at the same time, so calculate the
        # takeoff delays for those drones that take off to lower altitudes
        takeoff_duration = max(takeoff_durations)
        delays = [takeoff_duration - d for d in takeoff_durations]

        # Calculate when the takeoff should end
        end_of_takeoff = self.start_frame + takeoff_duration
        if len(storyboard.entries) > 1:
            first_frame = storyboard.second_entry.frame_start
            if first_frame < end_of_takeoff:
                self.report(
                    {"ERROR"},
                    f"Takeoff maneuver needs at least {takeoff_duration} frames; "
                    f"there is not enough time after the first entry of the "
                    f"storyboard (frame {first_frame})",
                )
                return False

        # Add a new storyboard entry with the given formation
        entry = storyboard.add_new_entry(
            formation=create_formation("Takeoff", target),
            frame_start=end_of_takeoff,
            duration=0,
            select=True,
            context=context,
        )

        # Set up the custom departure delays for the drones
        if delays and max(delays) > 0:
            entry.schedule_overrides_enabled = True
            for index, delay in enumerate(delays):
                if delay > 0:
                    override = entry.add_new_schedule_override()
                    override.index = index
                    override.pre_delay = delay

        # Recalculate the transitions leading from and to the target formation
        bpy.ops.skybrush.recalculate_transitions(scope="TO_SELECTED")
        if len(storyboard.entries) > 2:
            bpy.ops.skybrush.recalculate_transitions(scope="FROM_SELECTED")

        return True

    def _sort_drones(self, drones: List[Object]):
        """Sorts the given list of drones in-place according to the order
        specified by the user in this operator.
        """
        # TODO(ntamas): add support for ordering the drones
        pass

    def _validate_start_frame(self, context: Context) -> bool:
        """Returns whether the takeoff time chosen by the user is valid."""
        storyboard = context.scene.skybrush.storyboard
        # Note: we assume here that the first entry is the takeoff grid on ground
        if len(storyboard.entries) > 0:
            frame = storyboard.first_entry.frame_end
            if self.start_frame < frame:
                self.report(
                    {"ERROR"},
                    f"Takeoff maneuver must start after the first (takeoff grid) entry of the storyboard (frame {frame})",
                )
                return False
            if len(storyboard.entries) > 1:
                frame = storyboard.second_entry.frame_start
                if frame is not None and self.start_frame >= frame:
                    self.report(
                        {"ERROR"},
                        f"Takeoff maneuver must start before the second entry of the storyboard (frame {frame})",
                    )
                    return False

        return True
