from setuptools import setup, Extension
from Cython.Build import cythonize
import sys

extensions = [
    Extension(
        "raptor_router",
        ["raptor_router.pyx"],
        extra_compile_args=['-O3'],
    )
]

setup(
    name="raptor_router",
    ext_modules=cythonize(extensions, compiler_directives={'language_level': 3}),
)
