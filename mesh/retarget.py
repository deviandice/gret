from math import ceil
import bmesh
import bpy
import numpy as np

from gret.log import log, logd, logger
import gret.rbf as rbf

rbf_kernels = {
    'LINEAR': (rbf.linear, 1.0),
    'GAUSSIAN': (rbf.gaussian, 0.01),
    'PLATE': (rbf.thin_plate, 0.001),
    'BIHARMONIC': (rbf.multi_quadratic_biharmonic, 0.01),
    'INV_BIHARMONIC': (rbf.inv_multi_quadratic_biharmonic, 0.01),
    'C2': (rbf.beckert_wendland_c2_basis, 1.0),
}

# TODO
# - Investigate the mesh state where new shape keys are broken, see if it can be detected

class GRET_OT_retarget_mesh(bpy.types.Operator):
    #tooltip
    """Retarget meshes fit on a source mesh to a modified version of the source mesh.
The meshes are expected to share topology and vertex order"""
    # Note: If vertex order gets messed up, try using an addon like Transfer Vert Order to fix it

    bl_idname = 'gret.retarget_mesh'
    bl_label = "Retarget Mesh"
    bl_options = {'INTERNAL', 'UNDO'}

    source: bpy.props.StringProperty(
        name="Source",
        description="Source mesh object that the meshes were originally fit to",
    )
    destination: bpy.props.StringProperty(
        name="Destination",
        description="Modified mesh object to retarget to",
    )
    use_shape_key: bpy.props.BoolProperty(
        name="Use Shape Key",
        description="Destination is the name of a shape key in the source mesh",
        default=False,
    )
    function: bpy.props.EnumProperty(
        items=[
            ('LINEAR', "Linear", "Linear function"),
            ('GAUSSIAN', "Gaussian", "Gaussian function"),
            ('PLATE', "Thin Plate", "Thin plate function"),
            ('BIHARMONIC', "Biharmonic", "Multi quadratic biharmonic"),
            ('INV_BIHARMONIC', "Inverse Biharmonic", "Inverse multi quadratic biharmonic"),
            ('C2', "C2", "Beckert-Wendland C2 basis"),
        ],
        name="Function",
        description="Radial basis function kernel",
        default='BIHARMONIC',  # Least prone to explode and not too slidy
    )
    radius: bpy.props.FloatProperty(
        name="Radius",
        description="Smoothing parameter for the radial basis function",
        subtype='DISTANCE',
        default=0.5,
        min=0.0,
    )
    only_selection: bpy.props.BoolProperty(
        name="Only Vertex Selection",
        description="Sample only the current vertex selection of the source mesh",
        default=False,
    )
    high_quality: bpy.props.BoolProperty(
        name="High Quality",
        description="Sample as many vertices as possible for higher accuracy",
        default=False,
    )
    as_shape_key: bpy.props.BoolProperty(
        name="As Shape Key",
        description="Save the result as a shape key on the mesh",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_objects

    def execute(self, context):
        objs = context.selected_objects
        src_obj = bpy.data.objects.get(self.source)
        dst_is_shape_key = self.destination.startswith('s_')
        dst_obj = bpy.data.objects.get(self.destination[2:]) if not dst_is_shape_key else src_obj
        dst_shape_key_name = self.destination[2:] if dst_is_shape_key else None
        assert src_obj and dst_obj and src_obj.type == 'MESH' and dst_obj.type == 'MESH'
        src_mesh = src_obj.data
        dst_mesh = dst_obj.data

        num_vertices = len(src_mesh.vertices)
        if num_vertices == 0:
            self.report({'ERROR'}, "Source mesh has no vertices.")
            return {'CANCELLED'}
        if num_vertices != len(dst_mesh.vertices):
            self.report({'ERROR'}, "Source and destination meshes must have equal number of vertices.")
            return {'CANCELLED'}

        # Increase vertex sampling stride to speed up calculation (reduces accuracy)
        # A cap is still necessary since growth is not linear and it will take forever. Parallelize?
        # In practice sampling many vertices in a dense mesh doesn't change the result that much
        vertex_cap = 5000 if self.high_quality else 1000
        mask = [v.select for v in src_mesh.vertices] if self.only_selection else None
        num_masked = sum(mask) if mask else num_vertices
        stride = ceil(num_masked / vertex_cap)
        if num_masked == 0:
            self.report({'ERROR'}, "Source mesh has no vertices selected.")
            return {'CANCELLED'}
        logd(f"num_verts={num_masked}/{num_vertices} stride={stride} total={num_masked//stride}")

        rbf_kernel, scale = rbf_kernels.get(self.function, (rbf.linear, 1.0))
        src_pts = rbf.get_mesh_points(src_mesh, mask=mask, stride=stride)
        dst_pts = rbf.get_mesh_points(dst_mesh, shape_key=dst_shape_key_name, mask=mask, stride=stride)
        try:
            weights = rbf.get_weight_matrix(src_pts, dst_pts, rbf_kernel, self.radius * scale)
        except np.linalg.LinAlgError:
            # Solving for C2 kernel may throw 'SVD did not converge' sometimes
            self.report({'ERROR'}, "Failed to retarget. Try a different function or radius.")
            return {'CANCELLED'}

        for obj in objs:
            if obj.type != 'MESH' or obj == src_obj or obj == dst_obj:
                continue
            # Get the mesh points in retarget destination space
            dst_to_obj = obj.matrix_world.inverted() @ dst_obj.matrix_world
            obj_to_dst = dst_to_obj.inverted()
            mesh_pts = rbf.get_mesh_points(obj.data, matrix=obj_to_dst)
            num_mesh_pts = mesh_pts.shape[0]

            dist = rbf.get_distance_matrix(mesh_pts, src_pts, rbf_kernel, self.radius * scale)
            identity = np.ones((num_mesh_pts, 1))
            h = np.bmat([[dist, identity, mesh_pts]])
            new_mesh_pts = np.asarray(np.dot(h, weights))

            # Result back to local space
            new_mesh_pts = np.c_[new_mesh_pts, identity]
            new_mesh_pts = np.einsum('ij,aj->ai', dst_to_obj, new_mesh_pts)
            new_mesh_pts = new_mesh_pts[:, :-1]

            if self.as_shape_key:
                # Result to new shape key
                if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
                    obj.shape_key_add(name="Basis")
                shape_key_name = f"Retarget_{dst_obj.name}"
                if dst_is_shape_key:
                    shape_key_name += f"_{dst_shape_key_name}"
                shape_key = obj.shape_key_add(name=shape_key_name)
                shape_key.data.foreach_set('co', new_mesh_pts.ravel())
                shape_key.value = 1.0
            elif obj.data.shape_keys and obj.data.shape_keys.key_blocks:
                # There are shape keys, so replace the basis
                # Using bmesh propagates the change, where just setting the coordinates won't
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                for vert, new_pt in zip(bm.verts, new_mesh_pts):
                    vert.co[:] = new_pt
                bm.to_mesh(obj.data)
                bm.free()
            else:
                # Set new coordinates directly
                obj.data.vertices.foreach_set('co', new_mesh_pts.ravel())
            obj.data.update()

        return {'FINISHED'}

def draw_panel(self, context):
    layout = self.layout
    settings = context.scene.gret
    obj = context.object

    box = layout.box()
    box.label(text="Retarget Mesh", icon='MOD_MESHDEFORM')
    col = box.column(align=False)

    row = col.row(align=True)
    row.prop(settings, 'retarget_src', text="")
    row.label(text="", icon='FORWARD')
    row.prop(settings, 'retarget_dst', text="")

    row = col.row(align=True)
    row.prop(settings, 'retarget_function', text="")
    row.prop(settings, 'retarget_radius', text="")

    col.prop(settings, 'retarget_only_selection')
    col.prop(settings, 'retarget_high_quality')

    row = col.row(align=True)
    op1 = row.operator('gret.retarget_mesh', icon='CHECKMARK', text="Retarget")
    op2 = row.operator('gret.retarget_mesh', icon='SHAPEKEY_DATA', text="As Shape Key")
    if settings.retarget_src and settings.retarget_dst != 'NONE':
        op1.source = op2.source = settings.retarget_src.name
        op1.destination = op2.destination = settings.retarget_dst
        op1.function = op2.function = settings.retarget_function
        op1.radius = op2.radius = settings.retarget_radius
        op1.only_selection = op2.only_selection = settings.retarget_only_selection
        op1.high_quality = op2.high_quality = settings.retarget_high_quality
        op1.as_shape_key = False
        op2.as_shape_key = True
    else:
        row.enabled = False

classes = (
    GRET_OT_retarget_mesh,
)

def retarget_src_update(self, context):
    # On changing the source object, reset the destination object
    context.scene.gret.retarget_dst = 'NONE'

items = []
def retarget_dst_items(self, context):
    # Return shape keys of the source object and mesh objects with the same amount of vertices
    settings = context.scene.gret
    src_obj = settings.retarget_src

    items.clear()
    items.append(('NONE', "", ""))
    if src_obj:
        src_mesh = src_obj.data
        for o in context.scene.objects:
            if o.type == 'MESH' and o != src_obj and len(o.data.vertices) == len(src_mesh.vertices):
                items.append(('o_' + o.name, o.name, f"Object '{o.name}'", 'OBJECT_DATA', len(items)))
        if src_mesh.shape_keys:
            for sk in src_mesh.shape_keys.key_blocks:
                items.append(('s_' + sk.name, sk.name, f"Shape Key '{sk.name}'", 'SHAPEKEY_DATA', len(items)))
    return items

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('retarget_src', bpy.props.PointerProperty(
        name="Mesh Retarget Source",
        description="Base mesh that the meshes are fit to",
        type=bpy.types.Object,
        poll=lambda self, obj: obj and obj.type == 'MESH',
        update=retarget_src_update,
    ))
    settings.add_property('retarget_dst', bpy.props.EnumProperty(
        name="Mesh Retarget Destination",
        description="Mesh or shape key to retarget to",
        items=retarget_dst_items,
    ))
    retarget_props = GRET_OT_retarget_mesh.__annotations__
    settings.add_property('retarget_function', retarget_props['function'])
    settings.add_property('retarget_radius', retarget_props['radius'])
    settings.add_property('retarget_only_selection', retarget_props['only_selection'])
    settings.add_property('retarget_high_quality', retarget_props['high_quality'])

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
