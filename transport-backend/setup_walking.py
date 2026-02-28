from setuptools import setup, Extension
from Cython.Build import cythonize
import sys

extensions = [
    Extension(
        "walking",
        ["walking.pyx"],
        extra_compile_args=["-O3"],
    )
]

# Default to an in-place build when the script is invoked without args to
# place compiled extensions next to the source files (convenient for local
# development and benchmarking).
script_args = None
if len(sys.argv) == 1:
    script_args = ["build_ext", "--inplace"]

setup(
    name="walking",
    ext_modules=cythonize(extensions, compiler_directives={"language_level": 3}),
    script_args=script_args,
)
