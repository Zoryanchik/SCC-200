import re

with open('../transport-backend/api.py', 'r') as f:
    text = f.read()

# I will write a regex to replace the relevant block.
# Specifically the part:
#        origin_match_ok = False
#        if feed_origin_atco:
#            if journey_first_atco and str(journey_first_atco).strip() == str(feed_origin_atco).strip():
#                origin_match_ok = True
#        else:

# Let's check exactly what the text is.
