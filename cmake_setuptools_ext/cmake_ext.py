import os
import sys
import re
import subprocess
import multiprocessing
import shutil
import glob
from typing import Callable

from setuptools import Extension
from setuptools.command.build_ext import build_ext


def auto_determine_jobs() -> int:
    """Auto-determine number of jobs to use for cmake build

    Returns
    -------
    int
        cpu count, returns number of jobs as max(min(int(cpu_count/2), 8),1)
    """
    cpu_count = multiprocessing.cpu_count()
    return max([min([int(cpu_count / 2), 8]), 1])


class CMakeExtension(Extension):
    def __init__(self, name: str, cmakelists: str, jobs: int = 1, include: Callable = None, exclude: Callable = None):
        """Extension class for CMake builds

        Parameters
        ----------
        name : str
            name of extension (full python path (e.g. module.extension))
        cmakelists : str, optional
            path to CMakeLists.txt file
        jobs : int, optional
            number of jobs to use during make stage, by default 1
        include : Callable, optional
            a callable that takes in a lib name as input and returns a boolean on whether it should be included
            during install
        exclude : Callable, optional
            a callable that takes in a lib name as input and returns a boolean on whether it should be excluded
            during install

        Raises
        ------
        AssertionError
            raised when not passing in valid cmakelists
        """
        if "CMakeLists.txt" not in cmakelists:
            raise AssertionError("'cmakelists' must be a path to a 'CMakeLists.txt' file")
        # Set the path to the CMakeLists.txt file
        self.cmakelists = cmakelists
        # Set the number of build jobs to use
        self.jobs = jobs
        # Set include/exclude functions
        self.include = include
        self.exclude = exclude
        # We only need to set the name from the Extension class
        # because the sources should be set in the CMakeLists.txt
        Extension.__init__(self, name, sources=[])


class CMakeBuild(build_ext):
    def run(self):
        # check if cmake exists, setting 'cmake' as a requires in pyproject.toml
        # should satisfy this check.
        try:
            subprocess.run(["cmake", "--version"], check=True)
        except OSError:
            raise RuntimeError(
                "CMake must be installed to build the following extensions: "
                + ", ".join(e.name for e in self.extensions)
            )

        # always use fresh build directory
        # this seems to be necessary with editable builds
        build_directory = os.path.abspath(self.build_temp)
        shutil.rmtree(build_directory, ignore_errors=True)
        os.makedirs(build_directory, exist_ok=True)

        # create list of cmake args to pass
        cmake_args = list()

        # always set the python executable to the version of the current calling interpreter
        cmake_args += ["-DPYTHON_EXECUTABLE=" + sys.executable]

        # set the install directory
        cmake_args += ["-DCMAKE_INSTALL_PREFIX=" + os.path.join(build_directory, "release")]

        # get any arguments to add from CMAKE_ARGS environment variable
        cmake_args += os.environ.get("CMAKE_ARGS", "").split(" ")
        cmake_args = cmake_args[:-1] if cmake_args[-1] == "" else cmake_args

        # initialize list for build arguments
        build_args = list()

        # set number of jobs to use during build
        build_args = ["-j{}".format(self.extensions[0].jobs)]

        # get any arguments to add from CMAKE_BUILD_ARGS environment variable
        build_args += os.environ.get("CMAKE_BUILD_ARGS", "").split(" ")
        build_args = build_args[:-1] if build_args[-1] == "" else build_args

        # CMakeLists.txt is in the same directory as this setup.py file
        subprocess.run(
            ["cmake", os.path.dirname(self.extensions[0].cmakelists)] + cmake_args, cwd=build_directory, check=True
        )

        # build the C++ libraries
        cmake_cmd = ["cmake", "--build", "."] + build_args
        subprocess.run(cmake_cmd, cwd=build_directory, check=True)

        # install the C++ libraries
        subprocess.run(["cmake", "--install", "."], cwd=build_directory, check=True)
        self.move_libs(self.extensions[0])

    def move_libs(self, ext):
        print("Moving libraries to specified module path...")
        # setup directory names
        build_temp = os.path.abspath(self.build_temp)
        dest_ext = self.get_ext_fullpath(ext.name)
        source_dir = os.path.join(build_temp, "release", "lib")
        dest_dir = os.path.dirname(dest_ext)
        os.makedirs(dest_dir, exist_ok=True)

        # make __init__.py
        with open(os.path.join(dest_dir, "__init__.py"), "w"):
            pass

        # move libs to destination path
        for f in glob.glob(os.path.join(source_dir, "*.so*")):
            f = os.path.basename(f)
            source_path = os.path.join(source_dir, f)
            dest_path = os.path.join(dest_dir, f)
            # we filter libraries by include if specified
            if ext.include is not None:
                if not ext.include(f):
                    continue
            # we filter libraries by exclude if specified
            if ext.exclude is not None:
                if ext.exclude(f):
                    continue
            # else we copy the file from source_path to dest_path
            self.copy_file(source_path, dest_path)
