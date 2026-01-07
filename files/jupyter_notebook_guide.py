# ============================================================================
# GENERAL WALL INFERENCE - JUPYTER NOTEBOOK GUIDE
# ============================================================================
"""
This notebook demonstrates how to use the general wall inference solution
to automatically detect and create missing interior walls in floor plans.

The solution is COMPLETELY GENERAL and works for any floor plan data by:
1. Analyzing spatial relationships between ALL room pairs
2. Detecting gaps between rooms (typical wall thickness ~0.12-0.19m)
3. Determining wall orientation from bounding box overlaps
4. Creating walls at gap midpoints with proper dimensions

NO ASSUMPTIONS about:
- Room IDs or naming conventions
- Wall IDs or hints
- Specific layouts or configurations
- Dataset structure (works for ResPlan, CubiCasa, or any other format)
"""

# ============================================================================
# STEP 1: IMPORTS
# ============================================================================

import json
import sys
from pathlib import Path

# Add your thesis package to path if needed
# sys.path.append('/path/to/your/thesis_package')

# Import the general wall inference module
from general_wall_inference import (
    infer_missing_interior_walls_GENERAL,
    visualize_floor_plan,
    process_floor_plan
)

# ============================================================================
# STEP 2: SIMPLE USAGE - ONE FUNCTION CALL
# ============================================================================

# The easiest way - does everything automatically:
plan_dict = process_floor_plan(
    input_json_path="plan_00000_drop_interior_wall.json",
    output_json_path="plan_00000_inferred.json",
    visualize=True,
    wall_thickness=0.1897  # Adjust based on your dataset
)

"""
This will:
1. Load the JSON file
2. Infer all missing walls
3. Save the result
4. Create a visualization
5. Print statistics
"""

# ============================================================================
# STEP 3: ADVANCED USAGE - STEP BY STEP
# ============================================================================

# 3.1: Load your data
with open("plan_00000_drop_interior_wall.json", 'r') as f:
    plan_dict = json.load(f)

# 3.2: Infer walls with custom parameters
inferred_walls = infer_missing_interior_walls_GENERAL(
    plan_dict,
    wall_thickness=0.1897,  # Default wall thickness (meters)
    max_gap=0.30           # Maximum gap to consider (meters)
)

# 3.3: Add inferred walls to your data
if inferred_walls:
    if 'interior_wall' not in plan_dict['instances']['structural']:
        plan_dict['instances']['structural']['interior_wall'] = []
    
    plan_dict['instances']['structural']['interior_wall'].extend(inferred_walls)

# 3.4: Save the result
with open("plan_output.json", 'w') as f:
    json.dump(plan_dict, f, indent=2)

# 3.5: Visualize
visualize_floor_plan(
    plan_dict,
    output_path="visualization.png",
    figsize=(16, 14),
    show_labels=True
)

# ============================================================================
# STEP 4: BATCH PROCESSING MULTIPLE FILES
# ============================================================================

from pathlib import Path

# Process all JSON files in a directory
input_dir = Path("dataset/json_files")
output_dir = Path("dataset/inferred_walls")
output_dir.mkdir(exist_ok=True)

for json_file in input_dir.glob("*.json"):
    print(f"\n{'='*80}")
    print(f"Processing: {json_file.name}")
    print(f"{'='*80}")
    
    try:
        output_path = output_dir / json_file.name
        
        plan_dict = process_floor_plan(
            input_json_path=str(json_file),
            output_json_path=str(output_path),
            visualize=True,
            wall_thickness=0.1897
        )
        
        print(f"✅ Success: {json_file.name}")
        
    except Exception as e:
        print(f"❌ Error processing {json_file.name}: {e}")
        continue

print(f"\n{'='*80}")
print("BATCH PROCESSING COMPLETE")
print(f"{'='*80}")

# ============================================================================
# STEP 5: CUSTOMIZATION OPTIONS
# ============================================================================

# Option 1: Adjust wall thickness for different datasets
# -------------------------------------------------------
# ResPlan typically uses ~0.19m
# CubiCasa5k might use different values
# Check your data first!

inferred_walls = infer_missing_interior_walls_GENERAL(
    plan_dict,
    wall_thickness=0.15,  # Thinner walls
    max_gap=0.25         # Stricter gap threshold
)

# Option 2: Custom visualization
# -------------------------------
import matplotlib.pyplot as plt
from shapely.geometry import shape

fig, ax = plt.subplots(figsize=(20, 16))

# Custom room colors
room_colors = {
    'bedroom': '#FFD700',
    'bathroom': '#87CEEB',
    'kitchen': '#FF69B4',
    'living': '#DEB887',
}

for room_type, room_list in plan_dict['instances']['room'].items():
    color = room_colors.get(room_type, '#CCCCCC')
    
    for room in room_list:
        geom = shape(room['geom'])
        x, y = geom.exterior.xy
        ax.fill(x, y, alpha=0.5, color=color, edgecolor='black', linewidth=2)
        
        # Add room labels
        centroid = geom.centroid
        ax.text(centroid.x, centroid.y, room['id'], 
               ha='center', va='center', fontsize=12, fontweight='bold')

# Plot walls with custom styling
for wall in plan_dict['instances']['structural'].get('interior_wall', []):
    geom = shape(wall['geom'])
    x, y = geom.exterior.xy
    
    if wall.get('inferred'):
        ax.fill(x, y, color='red', alpha=0.9, edgecolor='darkred', linewidth=3)
    else:
        ax.fill(x, y, color='gray', alpha=0.7)

ax.set_aspect('equal')
ax.grid(True, alpha=0.3)
plt.title('Custom Floor Plan Visualization', fontsize=16, fontweight='bold')
plt.tight_layout()
plt.savefig('custom_visualization.png', dpi=200)
plt.show()

# ============================================================================
# STEP 6: ANALYSIS AND STATISTICS
# ============================================================================

def analyze_inferred_walls(plan_dict):
    """
    Analyze the characteristics of inferred walls.
    """
    int_walls = plan_dict['instances']['structural'].get('interior_wall', [])
    inferred = [w for w in int_walls if w.get('inferred')]
    original = [w for w in int_walls if not w.get('inferred')]
    
    print(f"\n{'='*80}")
    print("WALL ANALYSIS")
    print(f"{'='*80}")
    print(f"Total interior walls: {len(int_walls)}")
    print(f"  - Original: {len(original)}")
    print(f"  - Inferred: {len(inferred)}")
    
    # Orientation analysis
    if inferred:
        print(f"\nInferred walls by orientation:")
        from collections import Counter
        orientations = Counter(w['props'].get('orientation', 'UNKNOWN') for w in inferred)
        for orient, count in orientations.items():
            print(f"  - {orient}: {count}")
        
        # Length statistics
        lengths = [shape(w['geom']).length for w in inferred]
        print(f"\nLength statistics:")
        print(f"  - Min: {min(lengths):.3f}m")
        print(f"  - Max: {max(lengths):.3f}m")
        print(f"  - Mean: {sum(lengths)/len(lengths):.3f}m")
        
        # Area statistics
        areas = [w['props']['area'] for w in inferred]
        print(f"\nArea statistics:")
        print(f"  - Min: {min(areas):.4f}m²")
        print(f"  - Max: {max(areas):.4f}m²")
        print(f"  - Mean: {sum(areas)/len(areas):.4f}m²")
    
    return {
        'total': len(int_walls),
        'original': len(original),
        'inferred': len(inferred)
    }

# Run analysis
stats = analyze_inferred_walls(plan_dict)

# ============================================================================
# STEP 7: VALIDATION AND QUALITY CHECKS
# ============================================================================

def validate_inferred_walls(plan_dict, min_length=0.3, max_length=10.0):
    """
    Validate inferred walls for quality issues.
    """
    issues = []
    
    int_walls = plan_dict['instances']['structural'].get('interior_wall', [])
    inferred = [w for w in int_walls if w.get('inferred')]
    
    for wall in inferred:
        wall_id = wall['id']
        geom = shape(wall['geom'])
        length = geom.length
        area = wall['props']['area']
        
        # Check for too short walls
        if length < min_length:
            issues.append(f"⚠️  {wall_id}: Too short ({length:.3f}m)")
        
        # Check for too long walls
        if length > max_length:
            issues.append(f"⚠️  {wall_id}: Too long ({length:.3f}m)")
        
        # Check for very thin walls
        if area / length < 0.05:
            issues.append(f"⚠️  {wall_id}: Too thin (area/length ratio)")
    
    if issues:
        print(f"\n{'='*80}")
        print("VALIDATION ISSUES")
        print(f"{'='*80}")
        for issue in issues:
            print(issue)
    else:
        print(f"\n✅ All inferred walls pass validation!")
    
    return issues

# Run validation
issues = validate_inferred_walls(plan_dict)

# ============================================================================
# KEY PRINCIPLES OF THE ALGORITHM
# ============================================================================
"""
The algorithm works by:

1. ROOM PAIR ANALYSIS
   - Checks ALL possible room pairs
   - No assumptions about specific room types or IDs

2. GAP DETECTION
   - Measures distance between room polygons
   - Typical wall thickness: 0.12-0.20m
   - Only considers gaps within threshold (default 0.30m)

3. ORIENTATION DETECTION (BBOX METHOD)
   - Calculates X and Y overlaps between room bounding boxes
   - HORIZONTAL wall: Large X overlap (>0.5m) + Small/No Y overlap
   - VERTICAL wall: Large Y overlap (>0.5m) + Small/No X overlap
   - L-CORNER: Both overlaps significant
   
4. WALL PLACEMENT
   - Places wall at MIDPOINT of gap between rooms
   - Uses square caps and mitre joins for clean corners
   - Default thickness: 0.1897m (adjustable)

5. DUPLICATE AVOIDANCE
   - Checks overlap with existing walls
   - Skips if >50% overlap detected

This approach is COMPLETELY GENERAL and requires:
- NO room ID patterns
- NO wall ID hints
- NO specific dataset conventions
- ONLY geometric relationships between rooms

It will work for:
✅ ResPlan dataset
✅ CubiCasa5k dataset
✅ Any floor plan with room polygons separated by gaps
✅ Different layouts (apartments, houses, offices)
✅ L-shaped, U-shaped, or complex room configurations
"""

# ============================================================================
# TROUBLESHOOTING
# ============================================================================
"""
If walls are not being detected:

1. Check gap distances:
   - Print room-to-room distances
   - Adjust max_gap parameter if needed
   
2. Check room polygons:
   - Ensure rooms are NOT overlapping
   - Ensure rooms are separated by actual gaps
   
3. Check wall thickness:
   - Measure existing walls in your dataset
   - Adjust wall_thickness parameter accordingly

4. Visualize before/after:
   - Always check visualization
   - Verify wall placement makes sense

Example debugging:
"""

from shapely.geometry import shape

def debug_room_distances(plan_dict):
    """Debug: Print all room-to-room distances"""
    rooms = {}
    for room_type, room_list in plan_dict['instances']['room'].items():
        for room in room_list:
            rooms[room['id']] = shape(room['geom'])
    
    print("\nRoom-to-Room Distances:")
    for id_a, geom_a in rooms.items():
        for id_b, geom_b in rooms.items():
            if id_a >= id_b:
                continue
            dist = geom_a.distance(geom_b)
            if 0.01 < dist < 0.5:
                print(f"{id_a} ↔ {id_b}: {dist:.4f}m")

# debug_room_distances(plan_dict)

print("\n" + "="*80)
print("✅ NOTEBOOK GUIDE COMPLETE")
print("="*80)
