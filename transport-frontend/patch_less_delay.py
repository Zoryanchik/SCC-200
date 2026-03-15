import re

with open('../transport-backend/api.py', 'r') as f:
    text = f.read()

old_str = """            if d > MAX_VISIBLE_DELAY_S:
                # Skip storing this journey entirely
                continue
            # Store integer seconds
            temp_map[int(j_id)] = d"""

new_str = """            if d > MAX_VISIBLE_DELAY_S:
                # Skip storing this journey entirely
                continue
            # Store integer seconds, keeping the one with less delay if multiple map to same journey
            j_id_int = int(j_id)
            if j_id_int in temp_map:
                if d < temp_map[j_id_int]:
                    temp_map[j_id_int] = d
            else:
                temp_map[j_id_int] = d"""

if old_str in text:
    with open('../transport-backend/api.py', 'w') as f:
        f.write(text.replace(old_str, new_str))
    print("PATCHED")
else:
    print("NOT FOUND")
