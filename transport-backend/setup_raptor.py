from setuptools import setup, Extension
from Cython.Build import cythonize
import sys

extensions = [
    Extension(
        "raptor_router",
        ["raptor_router.pyx"],
        extra_compile_args=["-O3"],
    )
]

# If the script is run without command-line arguments, default to an in-place
# build so the generated .so files are placed next to the sources. This makes
# developer workflows and local benchmarking simpler (no need to copy from
# build/). You can still pass explicit setup args, e.g.:
#   python3 setup_raptor.py build_ext --inplace
script_args = None
if len(sys.argv) == 1:
    script_args = ["build_ext", "--inplace"]

setup(
    name="raptor_router",
    ext_modules=cythonize(extensions, compiler_directives={"language_level": 3}),
    script_args=script_args,
)
