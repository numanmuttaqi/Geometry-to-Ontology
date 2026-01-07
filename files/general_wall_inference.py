"""
================================================================================
GENERAL WALL INFERENCE SOLUTION
================================================================================
This solution works for ANY floor plan data by:
1. Analyzing spatial relationships between ALL room pairs
2. Detecting gaps between rooms (wall thickness ~0.12-0.19m)
3. Determining wall orientation from bbox overlaps
4. Creating walls at gap midpoints with proper dimensions

Key Principles:
- Rooms are separated by wall thickness (~0.12-0.20m)
- Room polygons represent INTERIOR space (not including walls)
- Walls should be placed in the GAP between rooms
- Orientation determined by overlap pattern, not by ID hints
================================================================================
"""

import json
from shapely.geometry import shape, mapping, LineString, box
from shapely.ops import nearest_points


def infer_missing_interior_walls_GENERAL(plan_dict, wall_thickness=0.1897, max_gap=0.30):
    """
    General solution to infer missing interior walls between rooms.
    
    Parameters:
    -----------
    plan_dict : dict
        The plan dictionary with rooms and structural elements
    wall_thickness : float
        Default wall thickness in meters (default: 0.1897m)
    max_gap : float
        Maximum gap distance to consider as potential wall location (default: 0.30m)
    
    Returns:
    --------
    list : List of inferred wall dictionaries to add to plan_dict
    """
    
    # ========================================================================
    # STEP 1: Extract all rooms
    # ========================================================================
    rooms = {}
    for room_type, room_list in plan_dict['instances']['room'].items():
        for room in room_list:
            rooms[room['id']] = {
                'id': room['id'],
                'type': room_type,
                'geom': shape(room['geom'])
            }
    
    print(f"\n{'='*80}")
    print(f"GENERAL WALL INFERENCE")
    print(f"{'='*80}")
    print(f"Total rooms: {len(rooms)}")
    print(f"Wall thickness: {wall_thickness}m")
    print(f"Max gap threshold: {max_gap}m")
    
    # ========================================================================
    # STEP 2: Get existing walls to avoid duplicates
    # ========================================================================
    existing_walls = set()
    existing_wall_geoms = []
    
    if 'interior_wall' in plan_dict['instances']['structural']:
        for wall in plan_dict['instances']['structural']['interior_wall']:
            existing_wall_geoms.append(shape(wall['geom']))
    
    # ========================================================================
    # STEP 3: Find all room pairs with gaps (potential walls)
    # ========================================================================
    inferred_walls = []
    wall_counter = 0
    room_pairs_checked = set()
    
    for room_id_a, room_data_a in rooms.items():
        for room_id_b, room_data_b in rooms.items():
            if room_id_a >= room_id_b:  # Avoid duplicates and self-comparison
                continue
            
            pair = tuple(sorted([room_id_a, room_id_b]))
            if pair in room_pairs_checked:
                continue
            room_pairs_checked.add(pair)
            
            geom_a = room_data_a['geom']
            geom_b = room_data_b['geom']
            
            # Calculate distance between rooms
            distance = geom_a.distance(geom_b)
            
            # Skip if rooms are touching or too far apart
            if distance < 0.01 or distance > max_gap:
                continue
            
            # ================================================================
            # STEP 4: Analyze bbox overlaps to determine wall orientation
            # ================================================================
            minxa, minya, maxxa, maxya = geom_a.bounds
            minxb, minyb, maxxb, maxyb = geom_b.bounds
            
            # Calculate overlaps
            x_overlap = min(maxxa, maxxb) - max(minxa, minxb)
            y_overlap = min(maxya, maxyb) - max(minya, minyb)
            
            # Calculate gaps
            x_gap = max(0, max(minxa, minxb) - min(maxxa, maxxb))
            y_gap = max(0, max(minya, minyb) - min(maxya, maxyb))
            
            # ================================================================
            # STEP 5: Create wall based on overlap pattern
            # ================================================================
            wall_line = None
            orientation = None
            
            # CASE 1: Horizontal wall (rooms stacked vertically)
            # - Significant X overlap
            # - Y gap exists
            if x_overlap > 0.5 and (y_gap > 0.05 or y_overlap < 0.5):
                x_start = max(minxa, minxb)
                x_end = min(maxxa, maxxb)
                
                # Place wall at midpoint of gap
                if minya > maxyb:  # A is above B
                    y_wall = (minya + maxyb) / 2
                elif minyb > maxya:  # B is above A
                    y_wall = (minyb + maxya) / 2
                else:
                    # Rooms overlap in Y - use midpoint of overlap
                    y_wall = (max(minya, minyb) + min(maxya, maxyb)) / 2
                
                wall_line = LineString([(x_start, y_wall), (x_end, y_wall)])
                orientation = "HORIZONTAL"
            
            # CASE 2: Vertical wall (rooms side-by-side)
            # - Significant Y overlap
            # - X gap exists
            elif y_overlap > 0.5 and (x_gap > 0.05 or x_overlap < 0.5):
                y_start = max(minya, minyb)
                y_end = min(maxya, maxyb)
                
                # Place wall at midpoint of gap
                if minxa > maxxb:  # A is to the right of B
                    x_wall = (minxa + maxxb) / 2
                elif minxb > maxxa:  # B is to the right of A
                    x_wall = (minxb + maxxa) / 2
                else:
                    # Rooms overlap in X - use midpoint of overlap
                    x_wall = (max(minxa, minxb) + min(maxxa, maxxb)) / 2
                
                wall_line = LineString([(x_wall, y_start), (x_wall, y_end)])
                orientation = "VERTICAL"
            
            # CASE 3: L-shaped corner or complex relationship
            # - Both overlaps significant
            elif x_overlap > 0.5 and y_overlap > 0.5:
                # Try to create two walls (horizontal + vertical)
                # For now, use nearest points as fallback
                p1, p2 = nearest_points(geom_a, geom_b)
                wall_line = LineString([p1, p2])
                orientation = "L-CORNER"
            
            # CASE 4: Fallback - nearest points
            else:
                p1, p2 = nearest_points(geom_a, geom_b)
                if p1.distance(p2) < max_gap:
                    wall_line = LineString([p1, p2])
                    orientation = "NEAREST"
            
            # ================================================================
            # STEP 6: Validate and create wall polygon
            # ================================================================
            if not wall_line or wall_line.is_empty or wall_line.length < 0.3:
                continue
            
            # Check if wall already exists (avoid duplicates)
            wall_poly = wall_line.buffer(
                wall_thickness / 2.0,
                cap_style=3,  # square caps
                join_style=2,  # mitre joins
            )
            
            # Skip if overlaps significantly with existing walls
            skip_wall = False
            for existing_wall_geom in existing_wall_geoms:
                if wall_poly.intersects(existing_wall_geom):
                    intersection_area = wall_poly.intersection(existing_wall_geom).area
                    if intersection_area > wall_poly.area * 0.5:
                        skip_wall = True
                        break
            
            if skip_wall:
                continue
            
            if wall_poly.is_empty or wall_poly.area < 0.001:
                continue
            
            # ================================================================
            # STEP 7: Create wall record
            # ================================================================
            wall_counter += 1
            wall_id = f"IW-INF-{wall_counter:03d}"
            
            wall_record = {
                "id": wall_id,
                "type": "interior_wall",
                "geom": mapping(wall_poly),
                "props": {
                    "area": round(wall_poly.area, 4),
                    "centroid": [round(wall_poly.centroid.x, 2), round(wall_poly.centroid.y, 2)],
                    "bbox": [round(c, 2) for c in wall_poly.bounds],
                    "adjacency": f"{room_id_a}↔{room_id_b}",
                    "gap": round(distance, 4),
                    "orientation": orientation
                },
                "inferred": True
            }
            
            inferred_walls.append(wall_record)
            existing_wall_geoms.append(wall_poly)
            
            # Log
            print(f"\n   ✅ {wall_id}: {room_id_a} ↔ {room_id_b}")
            print(f"      Orientation: {orientation}")
            print(f"      Length: {wall_line.length:.3f}m, Area: {wall_poly.area:.4f}m²")
            print(f"      Gap: {distance:.4f}m")
            print(f"      Overlaps: X={x_overlap:.3f}m, Y={y_overlap:.3f}m")
    
    print(f"\n{'='*80}")
    print(f"INFERENCE COMPLETE: {len(inferred_walls)} new walls created")
    print(f"{'='*80}\n")
    
    return inferred_walls


def visualize_floor_plan(plan_dict, output_path="floor_plan_visualization.png", 
                        figsize=(16, 14), show_labels=True):
    """
    Visualize the floor plan with rooms and walls.
    
    Parameters:
    -----------
    plan_dict : dict
        The plan dictionary
    output_path : str
        Path to save the visualization
    figsize : tuple
        Figure size (width, height)
    show_labels : bool
        Whether to show room labels
    """
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    
    fig, ax = plt.subplots(figsize=figsize)
    
    # ========================================================================
    # Plot rooms with different colors
    # ========================================================================
    room_colors = {
        'bedroom': '#FFE5B4',      # Peach
        'bathroom': '#B4E5FF',     # Light blue
        'kitchen': '#FFB4E5',      # Pink
        'living': '#E5D4B4',       # Tan
        'storage': '#FFFFB4',      # Light yellow
        'balcony': '#D4FFB4',      # Light green
    }
    
    for room_type, room_list in plan_dict['instances']['room'].items():
        color = room_colors.get(room_type, '#CCCCCC')
        
        for room in room_list:
            geom = shape(room['geom'])
            x, y = geom.exterior.xy
            ax.fill(x, y, alpha=0.4, color=color, edgecolor='black', linewidth=1)
            
            if show_labels:
                centroid = geom.centroid
                ax.text(centroid.x, centroid.y, room['id'], 
                       ha='center', va='center', fontsize=9, fontweight='bold')
    
    # ========================================================================
    # Plot exterior walls (black)
    # ========================================================================
    if 'exterior_wall' in plan_dict['instances']['structural']:
        for wall in plan_dict['instances']['structural']['exterior_wall']:
            geom = shape(wall['geom'])
            x, y = geom.exterior.xy
            ax.fill(x, y, color='black', alpha=0.9)
    
    # ========================================================================
    # Plot interior walls
    # ========================================================================
    if 'interior_wall' in plan_dict['instances']['structural']:
        for wall in plan_dict['instances']['structural']['interior_wall']:
            geom = shape(wall['geom'])
            x, y = geom.exterior.xy
            
            if wall.get('inferred'):
                # Inferred walls in RED
                ax.fill(x, y, color='red', alpha=0.8, edgecolor='darkred', linewidth=2)
            else:
                # Original walls in GRAY
                ax.fill(x, y, color='gray', alpha=0.6, edgecolor='black', linewidth=1)
    
    # ========================================================================
    # Plot doors (blue lines)
    # ========================================================================
    if 'door' in plan_dict['instances']['structural']:
        for door in plan_dict['instances']['structural']['door']:
            geom = shape(door['geom'])
            if geom.geom_type == 'LineString':
                x, y = geom.xy
                ax.plot(x, y, 'b-', linewidth=3, alpha=0.7)
    
    # ========================================================================
    # Formatting
    # ========================================================================
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3, linestyle='--', linewidth=0.5)
    ax.set_xlabel('X (meters)', fontsize=12)
    ax.set_ylabel('Y (meters)', fontsize=12)
    ax.set_title('Floor Plan with Inferred Interior Walls', fontsize=14, fontweight='bold')
    
    # Legend
    legend_elements = [
        mpatches.Patch(color='red', alpha=0.8, label='Inferred Walls'),
        mpatches.Patch(color='gray', alpha=0.6, label='Original Walls'),
        mpatches.Patch(color='black', alpha=0.9, label='Exterior Walls'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=10)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    print(f"✅ Visualization saved to: {output_path}")
    plt.close()
    
    return output_path


def process_floor_plan(input_json_path, output_json_path=None, 
                       visualize=True, wall_thickness=0.1897):
    """
    Complete workflow: load, infer walls, save, and visualize.
    
    Parameters:
    -----------
    input_json_path : str
        Path to input JSON file
    output_json_path : str, optional
        Path to save output JSON (default: input_path with '_inferred.json' suffix)
    visualize : bool
        Whether to create visualization
    wall_thickness : float
        Wall thickness in meters
    
    Returns:
    --------
    dict : Updated plan_dict with inferred walls
    """
    # Load data
    print(f"Loading: {input_json_path}")
    with open(input_json_path, 'r') as f:
        plan_dict = json.load(f)
    
    # Infer walls
    inferred_walls = infer_missing_interior_walls_GENERAL(
        plan_dict, 
        wall_thickness=wall_thickness
    )
    
    # Add inferred walls to plan_dict
    if inferred_walls:
        if 'interior_wall' not in plan_dict['instances']['structural']:
            plan_dict['instances']['structural']['interior_wall'] = []
        
        plan_dict['instances']['structural']['interior_wall'].extend(inferred_walls)
    
    # Save output
    if output_json_path is None:
        output_json_path = input_json_path.replace('.json', '_inferred.json')
    
    with open(output_json_path, 'w') as f:
        json.dump(plan_dict, f, indent=2)
    print(f"\n✅ Output saved to: {output_json_path}")
    
    # Visualize
    if visualize:
        viz_path = output_json_path.replace('.json', '.png')
        visualize_floor_plan(plan_dict, output_path=viz_path)
    
    # Print statistics
    print(f"\n{'='*80}")
    print("STATISTICS")
    print(f"{'='*80}")
    print(f"Total rooms: {sum(len(rooms) for rooms in plan_dict['instances']['room'].values())}")
    
    if 'interior_wall' in plan_dict['instances']['structural']:
        int_walls = plan_dict['instances']['structural']['interior_wall']
        inferred_count = sum(1 for w in int_walls if w.get('inferred'))
        print(f"Interior walls: {len(int_walls)} (Inferred: {inferred_count})")
    
    if 'exterior_wall' in plan_dict['instances']['structural']:
        print(f"Exterior walls: {len(plan_dict['instances']['structural']['exterior_wall'])}")
    
    return plan_dict


# ============================================================================
# USAGE EXAMPLE
# ============================================================================
if __name__ == "__main__":
    # Example usage
    input_file = "plan_00000_drop_interior_wall.json"
    
    plan_dict = process_floor_plan(
        input_json_path=input_file,
        output_json_path="plan_00000_with_inferred_walls.json",
        visualize=True,
        wall_thickness=0.1897
    )
    
    print("\n✅ Processing complete!")
