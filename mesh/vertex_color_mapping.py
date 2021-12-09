from math import pi
import bpy
import sys

def get_first_mapping(obj):
    return obj.vertex_color_mapping[0] if obj.vertex_color_mapping else None

def copy_mapping(obj, other_obj):
    mapping = get_first_mapping(obj)
    if mapping and not other_obj.vertex_color_mapping:
        other_obj.vertex_color_mapping.add()
    elif not mapping and other_obj.vertex_color_mapping:
        other_obj.vertex_color_mapping.clear()
    other_mapping = get_first_mapping(other_obj)

    if mapping and other_mapping:
        other_mapping.r = mapping.r
        other_mapping.g = mapping.g
        other_mapping.b = mapping.b
        other_mapping.a = mapping.a
        other_mapping.invert = mapping.invert
        other_mapping.extents = mapping.extents

def values_to_vcol(mesh, src_values, dst_vcol, dst_channel_idx, invert=False):
    for loop_idx, loop in enumerate(mesh.loops):
        value = max(0.0, min(1.0, src_values[loop.vertex_index]))
        if invert:
            value = 1.0 - value
        dst_vcol.data[loop_idx].color[dst_channel_idx] = value

def update_vcol_from_src(obj, mapping, src, dst_vcol, dst_channel_idx, invert=False):
    mesh = obj.data
    values = None
    remap_co = lambda co: (co[dst_channel_idx] / mapping.extents) + 0.5
    if src == 'ZERO':
        values = 0.0
    elif src == 'ONE':
        values = 1.0
    elif src == 'BEVEL':
        values = [vert.bevel_weight for vert in mesh.vertices]
    elif src == 'HASH':
        min_hash = -sys.maxsize - 1
        max_hash = sys.maxsize
        values = (hash(obj.name) - min_hash) / (max_hash - min_hash)
    elif src == 'PIVOTLOC':
        assert dst_channel_idx <= 3
        values = remap_co(obj.location)
    elif src == 'PIVOTROT':
        assert dst_channel_idx <= 3
        values = (obj.rotation_euler[dst_channel_idx] % pi) / pi
    elif src == 'VERTEX':
        assert dst_channel_idx <= 3
        m = obj.matrix_world
        values = [remap_co(m @ vert.co) for vert in mesh.vertices]
    elif src.startswith('vg_'):
        # Get values from vertex group
        values = [0.0] * len(mesh.vertices)
        vgroup_name = src[3:]
        vgroup = obj.vertex_groups.get(vgroup_name)
        if vgroup:
            vgroup_idx = vgroup.index
            for vert_idx, vert in enumerate(mesh.vertices):
                for vg in vert.groups:
                    if vg.group == vgroup_idx:
                        values[vert_idx] = vg.weight
                        break
    if type(values) is float:
        values = [values] * len(mesh.vertices)
    if values:
        assert len(values) == len(mesh.vertices)
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx, invert=invert)

def update_vcols(obj, invert=False):
    mapping = get_first_mapping(obj)
    if not mapping:
        return
    if all(src == 'NONE' for src in (mapping.r, mapping.g, mapping.b, mapping.a)):
        # Avoid creating a vertex group if nothing would be done anyway
        return

    mesh = obj.data
    vcol = mesh.vertex_colors.active if mesh.vertex_colors else mesh.vertex_colors.new()
    invert = invert != mapping.invert
    update_vcol_from_src(obj, mapping, mapping.r, vcol, 0, invert=invert)
    update_vcol_from_src(obj, mapping, mapping.g, vcol, 1, invert=invert)
    update_vcol_from_src(obj, mapping, mapping.b, vcol, 2, invert=invert)
    update_vcol_from_src(obj, mapping, mapping.a, vcol, 3, invert=invert)
    mesh.update()

persistent_items = [], [], [], []
def vcol_src_items(self, context, channel_idx=0):
    axis = ("X", "Y", "Z", "")[channel_idx]
    obj = context.active_object
    items = persistent_items[channel_idx]
    items.clear()
    if obj and obj.type == 'MESH':
        items.extend([
            ('NONE', "", "Leave the channel unchanged"),
            ('ZERO', "Zero", "Fill the channel with the minimum value"),
            ('ONE', "One", "Fill the channel with the maximum value"),
            ('BEVEL', "Bevel", "Vertex bevel weight"),
            ('HASH', "Random", "Random value based on the object's name"),
        ])
        if axis:
            items.extend([
                ('PIVOTLOC', "Location", f"Object pivot {axis} location"),
                ('PIVOTROT', "Rotation", f"Object pivot {axis} rotation"),
                ('VERTEX', "Vertex", f"Vertex {axis} world coordinates"),
            ])
        if obj.vertex_groups:
            items.extend([(f'vg_{vg.name}', vg.name, "Vertex group") for vg in obj.vertex_groups])
    return items

# Blender doesn't recognize functools.partial as a valid function for EnumProperty items
def vcol_src_r_items(self, context):
    return vcol_src_items(self, context, channel_idx=0)
def vcol_src_g_items(self, context):
    return vcol_src_items(self, context, channel_idx=1)
def vcol_src_b_items(self, context):
    return vcol_src_items(self, context, channel_idx=2)
def vcol_src_a_items(self, context):
    return vcol_src_items(self, context, channel_idx=3)

def vcol_src_update(self, context):
    obj = context.active_object
    if obj and obj.type == 'MESH' and obj.data.vertex_colors:
        # Automatically refresh mappings only if it wouldn't create a vcol layer
        bpy.ops.gret.vertex_color_mapping_refresh()

class GRET_OT_vertex_color_mapping_refresh(bpy.types.Operator):
    #tooltip
    """Creates or refreshes the active vertex color layer from source mappings"""

    bl_idname = 'gret.vertex_color_mapping_refresh'
    bl_label = "Refresh Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert the result",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if obj.vertex_color_mapping:
            update_vcols(obj, invert=self.invert)

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_add(bpy.types.Operator):
    #tooltip
    """Add vertex color mapping"""

    bl_idname = 'gret.vertex_color_mapping_add'
    bl_label = "Add Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if obj.vertex_color_mapping:
            return {'CANCELLED'}

        mapping = obj.vertex_color_mapping.add()
        mapping.r = mapping.g = mapping.b = mapping.a = 'ZERO'

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_clear(bpy.types.Operator):
    #tooltip
    """Clear vertex color mapping"""

    bl_idname = 'gret.vertex_color_mapping_clear'
    bl_label = "Clear Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        obj.vertex_color_mapping.clear()

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_copy_to_linked(bpy.types.Operator):
    #tooltip
    """Clear vertex color mapping"""

    bl_idname = 'gret.vertex_color_mapping_copy_to_linked'
    bl_label = "Copy Vertex Color Mapping to Linked"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        for other_obj in bpy.data.objects:
            if other_obj != obj and other_obj.data == obj.data:
                copy_mapping(obj, other_obj)

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_copy_to_selected(bpy.types.Operator):
    #tooltip
    """Clear vertex color mapping"""

    bl_idname = 'gret.vertex_color_mapping_copy_to_selected'
    bl_label = "Copy Vertex Color Mapping to Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        for other_obj in context.selected_objects:
            if other_obj != obj:
                copy_mapping(obj, other_obj)

        return {'FINISHED'}

class GRET_PG_vertex_color_mapping(bpy.types.PropertyGroup):
    r: bpy.props.EnumProperty(
        name="Vertex Color R Source",
        description="Source mapping to vertex color channel red",
        items=vcol_src_r_items,
        update=vcol_src_update,
    )
    g: bpy.props.EnumProperty(
        name="Vertex Color G Source",
        description="Source mapping to vertex color channel green",
        items=vcol_src_g_items,
        update=vcol_src_update,
    )
    b: bpy.props.EnumProperty(
        name="Vertex Color B Source",
        description="Source mapping to vertex color channel blue",
        items=vcol_src_b_items,
        update=vcol_src_update,
    )
    a: bpy.props.EnumProperty(
        name="Vertex Color A Source",
        description="Source mapping to vertex color channel alpha",
        items=vcol_src_a_items,
        update=vcol_src_update,
    )
    invert: bpy.props.BoolProperty(
        name="Invert Values",
        description="Make the result 1-value for each vertex color channel",
        default=False,
    )
    extents: bpy.props.FloatProperty(
        name="Extents",
        description="Extents of the box used to scale mappings that encode a location",
        default=4.0, min=0.001, precision=4, step=1, unit='LENGTH',
    )

def vcol_panel_draw(self, context):
    layout = self.layout
    obj = context.active_object
    mapping = get_first_mapping(obj)

    if not mapping:
        row = layout.row(align=True)
        row.operator('gret.vertex_color_mapping_add', icon='ADD')
        row.menu('GRET_MT_vertex_color_mapping', text='', icon='DOWNARROW_HLT')
    else:
        col = layout.column(align=True)
        row = col.row(align=True)
        row.operator('gret.vertex_color_mapping_clear', icon='X')
        row.menu('GRET_MT_vertex_color_mapping', text='', icon='DOWNARROW_HLT')
        row = col.row(align=True)
        row.prop(mapping, 'r', icon='COLOR_RED', text="")
        row.prop(mapping, 'g', icon='COLOR_GREEN', text="")
        row.prop(mapping, 'b', icon='COLOR_BLUE', text="")
        row.prop(mapping, 'a', icon='OUTLINER_DATA_FONT', text="")
        row.prop(mapping, 'invert', icon='REMOVE', text="")
        row.operator('gret.vertex_color_mapping_refresh', icon='FILE_REFRESH', text="")
        if any(src in {'PIVOTLOC', 'VERTEX'} for src in (mapping.r, mapping.g, mapping.b, mapping.a)):
            col.prop(mapping, 'extents')

class GRET_MT_vertex_color_mapping(bpy.types.Menu):
    bl_label = "Vertex Color Mapping Menu"

    def draw(self, context):
        layout = self.layout

        layout.operator('gret.vertex_color_mapping_copy_to_linked')
        layout.operator('gret.vertex_color_mapping_copy_to_selected')

classes = (
    GRET_MT_vertex_color_mapping,
    GRET_OT_vertex_color_mapping_add,
    GRET_OT_vertex_color_mapping_clear,
    GRET_OT_vertex_color_mapping_copy_to_linked,
    GRET_OT_vertex_color_mapping_copy_to_selected,
    GRET_OT_vertex_color_mapping_refresh,
    GRET_PG_vertex_color_mapping,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.vertex_color_mapping = bpy.props.CollectionProperty(
        type=GRET_PG_vertex_color_mapping,
    )
    bpy.types.DATA_PT_vertex_colors.append(vcol_panel_draw)

def unregister():
    bpy.types.DATA_PT_vertex_colors.remove(vcol_panel_draw)
    del bpy.types.Object.vertex_color_mapping

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
