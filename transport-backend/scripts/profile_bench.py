import cProfile, pstats, tempfile
from main import initialize_base

with cProfile.Profile() as pr:
    initialize_base()

with open('profile_results.txt', 'w') as stream:
    stats = pstats.Stats(pr, stream=stream)
    stats.sort_stats('cumtime')
    stats.print_stats()
