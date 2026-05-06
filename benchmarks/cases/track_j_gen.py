"""Track J-gen — Generated Retrieval Quality Cases.

Generated from benchmarks/generator.py.
Extends hand-crafted Track J with parameter-varied coverage cases.
"""

from benchmarks.generator import generate_track_j_cases

# Generate all J cases (no limit - use all 5030)
TRACK_J_GEN_CASES = generate_track_j_cases()
