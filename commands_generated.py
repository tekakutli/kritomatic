# AUTO-GENERATED from commands/*.yaml - DO NOT EDIT
# Run 'kritomatic compile' to regenerate

from decorators import command, arg, get_client


@command('brush', 'bg', help="Set background color")
@arg('color', type=str, help='Hex color (e.g.,')
def brush_bg(client, color):
    # Convert numeric types
    return client.execute('set_background_color', value=color)

@command('brush', 'fg', help="Set foreground color")
@arg('color', type=str, help='Hex color (e.g.,')
def brush_fg(client, color):
    # Convert numeric types
    return client.execute('set_foreground_color', value=color)

@command('brush', 'flow', help="Set brush flow percentage")
@arg('value', type=int, help='Flow percentage (0-100)')
def brush_flow(client, value):
    # Convert numeric types
    if value is not None:
        value = int(value)
    return client.execute('set_brush_flow', value=value)

@command('brush', 'get', help="Get current brush properties")
def brush_get(client):
    # Convert numeric types
    return client.execute('get_brush_properties')

@command('brush', 'list', help="List all brush presets")
def brush_list(client):
    # Convert numeric types
    return client.execute('list_brush_presets')

@command('brush', 'mode', help="Set brush blending mode")
@arg('value', type=str, help='Blending mode')
def brush_mode(client, value):
    # Convert numeric types
    return client.execute('set_brush_blending_mode', value=value)

@command('brush', 'opacity', help="Set brush opacity percentage")
@arg('value', type=int, help='Opacity percentage (0-100)')
def brush_opacity(client, value):
    # Convert numeric types
    if value is not None:
        value = int(value)
    return client.execute('set_brush_opacity', value=value)

@command('brush', 'preset', help="Switch to a brush preset")
@arg('value', type=str, help='Brush preset name')
def brush_preset(client, value):
    # Convert numeric types
    return client.execute('set_brush_preset', value=value)

@command('brush', 'select-opaque', help="Select opaque pixels")
@arg('--mode', type=str, default='replace', choices=['replace', 'add', 'subtract', 'intersect'], help='Selection mode')
def brush_select_opaque(client, mode='replace'):
    # Convert numeric types
    return client.execute('select_opaque', mode=mode)

@command('brush', 'size', help="Set brush size in pixels")
@arg('value', type=int, help='Brush size in pixels (1-1000)')
def brush_size(client, value):
    # Convert numeric types
    if value is not None:
        value = int(value)
    return client.execute('set_brush_size', value=value)

@command('doc', 'info', help="Get current document dimensions")
def doc_info(client):
    # Convert numeric types
    return client.execute('get_current_dimensions')

@command('doc', 'new-from-current', help="Create a new document with same dimensions as current")
@arg('--name', type=str, default='New Document', help='Name for the new document')
def doc_new_from_current(client, name='New Document'):
    # Convert numeric types
    return client.execute('create_new_from_current', name=name)

@command('doc', 'new', help="Create a new document with custom dimensions")
@arg('--name', type=str, default='New Document', help='Name for the new document')
@arg('--width', type=int, default=1920, help='Width in pixels')
@arg('--height', type=int, default=1080, help='Height in pixels')
@arg('--resolution', type=float, default=300, help='Resolution in DPI')
def doc_new(client, name='New Document', width=1920, height=1080, resolution=300):
    # Convert numeric types
    if width is not None:
        width = int(width)
    if height is not None:
        height = int(height)
    if resolution is not None:
        resolution = float(resolution)
    return client.execute('create_new_with_dimensions', name=name, width=width, height=height, resolution=resolution)

@command('layer', 'activate', help="Activate a layer")
@arg('name', type=str, help='Layer name')
def layer_activate(client, name):
    # Convert numeric types
    return client.execute('set_active_layer', name=name)

@command('layer', 'add-text', help="Add vector text to a vector layer")
@arg('layer_name', type=str, help='Name of the target vector layer')
@arg('--text', type=str, required=True, help='Text content')
@arg('--font-family', type=str, default='sans-serif', help='Font family (e.g., Arial, sans-serif)')
@arg('--font-size', type=int, default=12, help='Font size in points')
@arg('--x', type=float, default=0, help='X position in pixels')
@arg('--y', type=float, default=0, help='Y position in pixels')
@arg('--color', type=str, default='#000000', help='Hex color (e.g.,')
@arg('--alignment', type=str, default='left', choices=['left', 'center', 'right'], help='Text alignment')
def layer_add_text(client, layer_name, text=None, font_family='sans-serif', font_size=12, x=0, y=0, color='#000000', alignment='left'):
    # Convert numeric types
    if font_size is not None:
        font_size = int(font_size)
    if x is not None:
        x = float(x)
    if y is not None:
        y = float(y)
    return client.execute('add_vector_text', layer_name=layer_name, text=text, font_family=font_family, font_size=font_size, x=x, y=y, color=color, alignment=alignment)

@command('layer', 'canvas-center', help="Get canvas center coordinates")
def layer_canvas_center(client):
    # Convert numeric types
    return client.execute('get_canvas_centerr')

@command('layer', 'create-blend', help="Create a new layer with blend mode above current layer")
@arg('name', type=str, help='Layer name')
@arg('blend_mode', type=str, choices=['normal', 'multiply', 'screen', 'overlay', 'add', 'difference', 'color', 'saturation', 'hue', 'luminosity'], help='Blending mode for the layer')
def layer_create_blend(client, name, blend_mode):
    # Convert numeric types
    return client.execute('create_blend_layer', name=name, blend_mode=blend_mode)

@command('layer', 'create-file', help="Create a file layer")
@arg('name', type=str, help='Layer name')
@arg('--file-path', type=str, required=True, help='Path to image file')
@arg('--position', type=str, default='above_current', choices=['above_current', 'below_current', 'above_named', 'below_named', 'top', 'bottom'], help='Where to place the layer')
@arg('--reference', type=str, help='Reference layer name')
def layer_create_file(client, name, file_path=None, position='above_current', reference=None):
    # Convert numeric types
    return client.execute('create_file_layer', name=name, file_path=file_path, position=position, reference=reference)

@command('layer', 'create-transform', help="Create a transform mask on a layer")
@arg('layer_name', type=str, help='Target layer name')
@arg('--mask-name', type=str, default='Transform Mask', help='Name for the transform mask')
def layer_create_transform(client, layer_name, mask_name='Transform Mask'):
    # Convert numeric types
    return client.execute('create_transform_mask', layer_name=layer_name, mask_name=mask_name)

@command('layer', 'create', help="Create a new layer")
@arg('name', type=str, help='Layer name')
@arg('--layer-type', type=str, default='paintlayer', choices=['paintlayer', 'grouplayer', 'selectionmask', 'vectorlayer', 'filterlayer'], help='Layer type')
@arg('--position', type=str, default='above_current', choices=['above_current', 'below_current', 'above_named', 'below_named', 'top', 'bottom'], help='Where to place the layer')
@arg('--reference', type=str, help='Reference layer name')
def layer_create(client, name, layer_type='paintlayer', position='above_current', reference=None):
    # Convert numeric types
    return client.execute('create_layer', name=name, layer_type=layer_type, position=position, reference=reference)

@command('layer', 'fill', help="Fill a layer with a specific color")
@arg('layer_name', type=str, help='Name of the layer to fill')
@arg('--color', type=str, help='Hex color to fill with (e.g.,')
@arg('--foreground', help='Use current foreground color')
@arg('--background', help='Use current background color')
def layer_fill(client, layer_name, color=None, foreground=None, background=None):
    # Convert numeric types
    return client.execute('fill_layer', layer_name=layer_name, color=color, foreground=foreground, background=background)

@command('layer', 'fill-selection', help="Fill the current selection with a specific color")
@arg('--color', type=str, help='Hex color to fill with (e.g.,')
@arg('--foreground', help='Use current foreground color')
@arg('--background', help='Use current background color')
def layer_fill_selection(client, color=None, foreground=None, background=None):
    # Convert numeric types
    return client.execute('fill_selection', color=color, foreground=foreground, background=background)

@command('layer', 'list-shapes', help="List all shapes on a vector layer")
@arg('layer_name', type=str, help='Name of the vector layer')
def layer_list_shapes(client, layer_name):
    # Convert numeric types
    return client.execute('list_shapes', layer_name=layer_name)

@command('layer', 'list', help="List all layers")
def layer_list(client):
    # Convert numeric types
    return client.execute('list_layers')

@command('layer', 'move-active', help="Move active layer to group")
@arg('group_name', type=str, help='Destination group')
@arg('--position', type=str, default='inside', choices=['inside', 'above', 'below'], help='Position relative to group')
def layer_move_active(client, group_name, position='inside'):
    # Convert numeric types
    return client.execute('move_active_layer_to_group', group_name=group_name, position=position)

@command('layer', 'move-to-new-doc', help="Move a layer to its own new document")
@arg('layer_name', type=str, help='Name of the layer to move')
@arg('--new-doc-name', type=str, help='Name for the new document (defaults to layer name)')
def layer_move_to_new_doc(client, layer_name, new_doc_name=None):
    # Convert numeric types
    return client.execute('move_layer_to_new_document', layer_name=layer_name, new_doc_name=new_doc_name)

@command('layer', 'move', help="Move layer to group")
@arg('layer_name', type=str, help='Layer to move')
@arg('group_name', type=str, help='Destination group')
@arg('--position', type=str, default='inside', choices=['inside', 'above', 'below'], help='Position relative to group')
def layer_move(client, layer_name, group_name, position='inside'):
    # Convert numeric types
    return client.execute('move_layer_to_group', layer_name=layer_name, group_name=group_name, position=position)

@command('layer', 'rename-active', help="Rename active layer")
@arg('new_name', type=str, help='New name')
def layer_rename_active(client, new_name):
    # Convert numeric types
    return client.execute('rename_active_layer', new_name=new_name)

@command('layer', 'rename', help="Rename a layer")
@arg('old_name', type=str, help='Current name')
@arg('new_name', type=str, help='New name')
def layer_rename(client, old_name, new_name):
    # Convert numeric types
    return client.execute('rename_layer_by_name', old_name=old_name, new_name=new_name)

@command('layer', 'replace-all', help="Replace text across all vector layers")
@arg('--old-text', type=str, required=True, help='Text to find')
@arg('--new-text', type=str, required=True, help='Text to replace with')
@arg('--scope', type=str, default='all', choices=['all', 'active'], help='Scope of search (all layers or active layer only)')
def layer_replace_all(client, old_text=None, new_text=None, scope='all'):
    # Convert numeric types
    return client.execute('replace_all_text', old_text=old_text, new_text=new_text, scope=scope)

@command('layer', 'transform-mask', help="Apply transformation to a transform mask")
@arg('mask_name', type=str, help='Name of the transform mask')
@arg('--translate-x', type=float, default=0, help='X translation in pixels')
@arg('--translate-y', type=float, default=0, help='Y translation in pixels')
@arg('--rotation', type=float, default=0, help='Rotation in degrees')
@arg('--scale-x', type=float, default=1.0, help='X scale factor')
@arg('--scale-y', type=float, default=1.0, help='Y scale factor')
def layer_transform_mask(client, mask_name, translate_x=0, translate_y=0, rotation=0, scale_x=1.0, scale_y=1.0):
    # Convert numeric types
    if translate_x is not None:
        translate_x = float(translate_x)
    if translate_y is not None:
        translate_y = float(translate_y)
    if rotation is not None:
        rotation = float(rotation)
    if scale_x is not None:
        scale_x = float(scale_x)
    if scale_y is not None:
        scale_y = float(scale_y)
    return client.execute('transform_mask', mask_name=mask_name, translate_x=translate_x, translate_y=translate_y, rotation=rotation, scale_x=scale_x, scale_y=scale_y)

@command('layer', 'update-text', help="Update existing text on a vector layer")
@arg('layer_name', type=str, help='Name of the target vector layer')
@arg('--old-text', type=str, required=True, help='Current text to replace')
@arg('--new-text', type=str, required=True, help='New text content')
def layer_update_text(client, layer_name, old_text=None, new_text=None):
    # Convert numeric types
    return client.execute('update_vector_text', layer_name=layer_name, old_text=old_text, new_text=new_text)

@command('mask', 'add-active', help="Add selection mask to active layer")
@arg('--from-selection', help='Use current selection')
def mask_add_active(client, from_selection=None):
    # Convert numeric types
    return client.execute('add_selection_mask_to_active', from_selection=from_selection)

@command('mask', 'add', help="Add selection mask to layer")
@arg('layer_name', type=str, help='Layer name')
@arg('--from-selection', help='Use current selection')
def mask_add(client, layer_name, from_selection=None):
    # Convert numeric types
    return client.execute('add_selection_mask', layer_name=layer_name, from_selection=from_selection)

@command('palette', 'activate', help="Activate a palette")
@arg('name', type=str, help='Palette name')
def palette_activate(client, name):
    # Convert numeric types
    return client.execute('activate_palette', name=name)

@command('palette', 'add', help="Add color to palette")
@arg('name', type=str, help='Palette name')
def palette_add(client, name):
    # Convert numeric types
    return client.execute('add_to_palette', name=name)

@command('palette', 'create', help="Create a new palette")
@arg('name', type=str, help='Palette name')
@arg('--columns', type=int, default=8, help='Number of columns')
@arg('--rows', type=int, default=8, help='Number of rows')
def palette_create(client, name, columns=8, rows=8):
    # Convert numeric types
    if columns is not None:
        columns = int(columns)
    if rows is not None:
        rows = int(rows)
    return client.execute('create_palette', name=name, columns=columns, rows=rows)

@command('palette', 'list', help="List all palettes")
def palette_list(client):
    # Convert numeric types
    return client.execute('list_palettes')

# Command dispatch table
COMMAND_REGISTRY = {
    ('brush', 'bg'): brush_bg,
    ('brush', 'fg'): brush_fg,
    ('brush', 'flow'): brush_flow,
    ('brush', 'get'): brush_get,
    ('brush', 'list'): brush_list,
    ('brush', 'mode'): brush_mode,
    ('brush', 'opacity'): brush_opacity,
    ('brush', 'preset'): brush_preset,
    ('brush', 'select-opaque'): brush_select_opaque,
    ('brush', 'size'): brush_size,
    ('doc', 'info'): doc_info,
    ('doc', 'new-from-current'): doc_new_from_current,
    ('doc', 'new'): doc_new,
    ('layer', 'activate'): layer_activate,
    ('layer', 'add-text'): layer_add_text,
    ('layer', 'canvas-center'): layer_canvas_center,
    ('layer', 'create-blend'): layer_create_blend,
    ('layer', 'create-file'): layer_create_file,
    ('layer', 'create-transform'): layer_create_transform,
    ('layer', 'create'): layer_create,
    ('layer', 'fill'): layer_fill,
    ('layer', 'fill-selection'): layer_fill_selection,
    ('layer', 'list-shapes'): layer_list_shapes,
    ('layer', 'list'): layer_list,
    ('layer', 'move-active'): layer_move_active,
    ('layer', 'move-to-new-doc'): layer_move_to_new_doc,
    ('layer', 'move'): layer_move,
    ('layer', 'rename-active'): layer_rename_active,
    ('layer', 'rename'): layer_rename,
    ('layer', 'replace-all'): layer_replace_all,
    ('layer', 'transform-mask'): layer_transform_mask,
    ('layer', 'update-text'): layer_update_text,
    ('mask', 'add-active'): mask_add_active,
    ('mask', 'add'): mask_add,
    ('palette', 'activate'): palette_activate,
    ('palette', 'add'): palette_add,
    ('palette', 'create'): palette_create,
    ('palette', 'list'): palette_list,
}
