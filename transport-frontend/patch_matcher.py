import sys

with open('../transport-backend/api.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for i, line in enumerate(lines):
    new_lines.append(line)

# Let's replace the lines using line numbers?
# Better to do a string replacement.
