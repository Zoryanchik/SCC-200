from setuptools import setup, Extension
from Cython.Build import cythonize

extensions = [
    Extension(
        "walking",
        ["walking.pyx"],
        extra_compile_args=["-O3"],
    )
]

setup(
    name="walking",
    ext_modules=cythonize(extensions, compiler_directives={"language_level": 3}),
)
