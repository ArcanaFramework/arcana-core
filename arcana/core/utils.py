from dataclasses import is_dataclass, fields as dataclass_fields
from typing import Sequence
import subprocess as sp
import importlib_metadata
import pkgutil
import json
import typing as ty
from enum import Enum
import builtins
from copy import copy
import re
import inspect
from importlib import import_module
from inspect import isclass, isfunction
from itertools import zip_longest
import pkg_resources
from pathlib import Path, PosixPath
import tempfile
import tarfile
import logging
import docker
import os.path
from contextlib import contextmanager
from collections.abc import Iterable
import cloudpickle as cp
import attrs
from pydra.engine.core import Workflow, LazyField, TaskBase
from pydra.engine.task import FunctionTask
from pydra.engine.specs import BaseSpec, SpecInfo
from arcana.core.exceptions import ArcanaUsageError

# Avoid arcana.__version__ causing a circular import
from arcana._version import get_versions

__version__ = get_versions()["version"]
del get_versions


logger = logging.getLogger("arcana")


PIPELINE_ANNOTATIONS = "__arcana_pipeline__"
CONVERTER_ANNOTATIONS = "__arcana_converter__"
SWICTH_ANNOTATIONS = "__arcana_switch__"
CHECK_ANNOTATIONS = "__arcana_check__"

ARCANA_SPEC = "__arcana_type__"


PATH_SUFFIX = "_path"
FIELD_SUFFIX = "_field"
CHECKSUM_SUFFIX = "_checksum"

ARCANA_HOME_DIR = Path.home() / ".arcana"

ARCANA_PIP = "git+ssh://git@github.com/australian-imaging-service/arcana.git"

HASH_CHUNK_SIZE = 2**20  # 1MB in calc. checksums to avoid mem. issues


def get_home_dir():
    try:
        home_dir = Path(os.environ["ARCANA_HOME"])
    except KeyError:
        home_dir = ARCANA_HOME_DIR
    if not home_dir.exists():
        home_dir.mkdir()
    return home_dir


def get_config_file_path(name: str):
    """Gets the file path for the configuration file corresponding to `name`

    Parameters
    ----------
    name
        Name of the configuration file to return

    Returns
    -------
    Path
        Path to configuration file
    """
    return get_home_dir() / (name + ".yaml")


# Escape values for invalid characters for Python variable names
PATH_ESCAPES = {
    "_": "_u_",
    "/": "__l__",
    ".": "__o__",
    " ": "__s__",
    "\t": "__t__",
    ",": "__comma__",
    ">": "__gt__",
    "<": "__lt__",
    "-": "__H__",
    "'": "__singlequote__",
    '"': "__doublequote__",
    "(": "__openparens__",
    ")": "__closeparens__",
    "[": "__openbracket__",
    "]": "__closebracket__",
    "{": "__openbrace__",
    "}": "__closebrace__",
    ":": "__colon__",
    ";": "__semicolon__",
    "`": "__tick__",
    "~": "__tilde__",
    "|": "__pipe__",
    "?": "__question__",
    "\\": "__backslash__",
    "$": "__dollar__",
    "@": "__at__",
    "!": "__exclaimation__",
    "#": "__pound__",
    "%": "__percent__",
    "^": "__caret__",
    "&": "__ampersand__",
    "*": "__star__",
    "+": "__plus__",
    "=": "__equals__",
    "XXX": "__tripplex__",
}

PATH_NAME_PREFIX = "XXX"

EMPTY_PATH_NAME = "__empty__"


def path2varname(path):
    """Escape a string (typically a file-system path) so that it can be used as a Python
    variable name by replacing non-valid characters with escape sequences in PATH_ESCAPES.

    Parameters
    ----------
    path : str
        A path containing '/' characters that need to be escaped

    Returns
    -------
    str
        A python safe name
    """
    if not path:
        name = EMPTY_PATH_NAME
    else:
        name = path
        for char, esc in PATH_ESCAPES.items():
            name = name.replace(char, esc)
    if name.startswith("_"):
        name = PATH_NAME_PREFIX + name
    return name


def varname2path(name):
    """Unescape a Pythonic name created by `path2varname`

    Parameters
    ----------
    name : str
        the escaped path

    Returns
    -------
    str
        the original path
    """
    if name.startswith(PATH_NAME_PREFIX):
        path = name[len(PATH_NAME_PREFIX) :]
    else:
        path = name  # strip path-name prefix
    if path == EMPTY_PATH_NAME:
        return ""
    # the order needs to be reversed so that "dunder" (double underscore) is
    # unescaped last
    for char, esc in reversed(PATH_ESCAPES.items()):
        path = path.replace(esc, char)
    return path


def func_task(func, in_fields, out_fields, **inputs):
    """Syntactic sugar for creating a FunctionTask

    Parameters
    ----------
    func : Callable
        The function to wrap
    input_fields : ty.List[ty.Tuple[str, type]]
        The list of input fields to create for the task
    output_fields : ty.List[ty.Tuple[str, type]]
        The list of output fields to create for the task
    **inputs
        Inputs to set for the task

    Returns
    -------
    pydra.FunctionTask
        The wrapped task"""
    func_name = func.__name__.capitalize()
    return FunctionTask(
        func,
        input_spec=SpecInfo(name=f"{func_name}In", bases=(BaseSpec,), fields=in_fields),
        output_spec=SpecInfo(
            name=f"{func_name}Out", bases=(BaseSpec,), fields=out_fields
        ),
        **inputs,
    )


def set_loggers(loglevel, pydra_level="warning", depend_level="warning"):
    """Sets loggers for arcana and pydra. To be used in CLI

    Parameters
    ----------
    loglevel : str
        the threshold to produce logs at (e.g. debug, info, warning, error)
    pydra_level : str, optional
        the threshold to produce logs from Pydra at
    depend_level : str, optional
        the threshold to produce logs in dependency packages
    """

    def parse(level):
        if isinstance(level, str):
            level = getattr(logging, level.upper())
        return level

    logging.getLogger("arcana").setLevel(parse(loglevel))
    logging.getLogger("pydra").setLevel(parse(pydra_level))

    # set logging format
    logging.basicConfig(level=parse(depend_level))


def class2str(cls, strip_prefix=None):
    """Records the location of a class so it can be loaded later using
    `str2class`, in the format <module-name>:<class-name>"""
    if not (isclass(cls) or isfunction(cls)):
        cls = type(cls)  # Get the class rather than the object
    module_name = cls.__module__
    if module_name == "builtins":
        return cls.__name__
    if strip_prefix and module_name.startswith(strip_prefix):
        module_name = module_name[len(strip_prefix) :]
    return module_name + ":" + cls.__name__


def str2class(class_str: str, prefixes: Sequence[str] = ()) -> type:
    """
    Resolves a class from a location string in the format "<module-name>:<class-name>"

    Parameters
    ----------
    class_str : str
        Module path and name of class joined by ':', e.g. main_pkg.sub_pkg:MyClass
    prefixes : Sequence[str]
        List of allowable module prefixes to try to append if the fully
        resolved path fails, e.g. ['pydra.tasks'] would allow
        'fsl.preprocess.first.First' to resolve to
        pydra.tasks.fsl.preprocess.first.First
    fallback_to_str : bool
        whether to fallback to a string if the class module can't be loaded
    Returns
    -------
    type:
        The resolved class
    """
    if not isinstance(class_str, str):
        return class_str  # Assume that it is already resolved
    if class_str.startswith("<") and class_str.endswith(">"):
        class_str = class_str[1:-1]
    try:
        module_path, class_name = class_str.split(":")
    except ValueError:
        try:
            return getattr(builtins, class_str)
        except AttributeError:
            raise ValueError(
                f"Class location '{class_str}' should contain a ':' unless it is in the "
                "builtins module"
            ) from None
    module = None
    for prefix in [None] + list(prefixes):
        if prefix is not None:
            mod_name = prefix + ("." if prefix[-1] != "." else "") + module_path
        else:
            mod_name = module_path
        if not mod_name:
            continue
        mod_name = mod_name.strip(".")
        try:
            module = import_module(mod_name)
        except ModuleNotFoundError:
            continue
        else:
            break
    if module is None:
        if STR2CLASS_FALLBACK.permit:
            logger.warning(
                "Did not find module corresponding to %s, but ignoring as "
                "arcana.core.utils.permit_str2class_fallback is set to True",
                class_str,
            )
            return class_str
        else:
            raise ArcanaUsageError(
                "Did not find class at '{}' or any sub paths of '{}'".format(
                    class_str, "', '".join(prefixes)
                )
            )
    try:
        cls = getattr(module, class_name)
    except AttributeError:
        raise ArcanaUsageError(
            f"Did not find '{class_str}' class/function in module '{module.__name__}'"
        )
    return cls


def submodules(package):
    """Iterates all modules within the given package

    Parameters
    ----------
    package : module
        the package to iterate over

    Yields
    ------
    module
        all modules within the package
    """
    for mod_info in pkgutil.iter_modules(
        [str(Path(package.__file__).parent)], prefix=package.__package__ + "."
    ):
        yield import_module(mod_info.name)


def list_subclasses(package, base_class):
    """List all available subclasses of a base class in modules within the given
    package

    Parameters
    ----------
    package : module
        the package to list the subclasses within
    base_class : type
        the base class

    Returns
    -------
    list
        all subclasses of the base-class found with the package
    """
    subclasses = []
    for module in submodules(package):
        for obj_name in dir(module):
            obj = getattr(module, obj_name)
            if isclass(obj) and issubclass(obj, base_class) and obj is not base_class:
                subclasses.append(obj)
    return subclasses


@contextmanager
def set_cwd(path):
    """Sets the current working directory to `path` and back to original
    working directory on exit

    Parameters
    ----------
    path : str
        The file system path to set as the current working directory
    """
    pwd = os.getcwd()
    os.chdir(path)
    try:
        yield path
    finally:
        os.chdir(pwd)


def dir_modtime(dpath):
    """
    Returns the latest modification time of all files/subdirectories in a
    directory
    """
    return max(os.path.getmtime(d) for d, _, _ in os.walk(dpath))


# def parse_single_value(value, datatype=None):
#     """
#     Tries to convert to int, float and then gives up and assumes the value
#     is of type string. Useful when excepting values that may be string
#     representations of numerical values
#     """
#     if isinstance(value, str):
#         try:
#             if value.startswith('"') and value.endswith('"'):
#                 value = str(value[1:-1])
#             elif '.' in value:
#                 value = float(value)
#             else:
#                 value = int(value)
#         except ValueError:
#             value = str(value)
#     elif not isinstance(value, (int, float, bool)):
#         raise ArcanaUsageError(
#             "Unrecognised type for single value {}".format(value))
#     if datatype is not None:
#         value = datatype(value)
#     return value


def parse_value(value):
    """Parses values from string representations"""
    try:
        value = json.loads(
            value
        )  # FIXME: Is this value replace really necessary, need to investigate where it is used again
    except (TypeError, json.decoder.JSONDecodeError):
        pass
    return value


# def parse_value(value, datatype=None):
#     # Split strings with commas into lists
#     if isinstance(value, str):
#         if value.startswith('[') and value.endswith(']'):
#             value = value[1:-1].split(',')
#     else:
#         # Cast all iterables (except strings) into lists
#         try:
#             value = list(value)
#         except TypeError:
#             pass
#     if isinstance(value, list):
#         value = [parse_single_value(v, datatype=datatype) for v in value]
#         # Check to see if datatypes are consistent
#         datatypes = set(type(v) for v in value)
#         if len(datatypes) > 1:
#             raise ArcanaUsageError(
#                 "Inconsistent datatypes in values array ({})"
#                 .datatype(value))
#     else:
#         value = parse_single_value(value, datatype=datatype)
#     return value


def iscontainer(*items):
    """
    Checks whether all the provided items are containers (i.e of class list,
    dict, tuple, etc...)
    """
    return all(isinstance(i, Iterable) and not isinstance(i, str) for i in items)


def find_mismatch(first, second, indent=""):
    """
    Finds where two objects differ, iterating down into nested containers
    (i.e. dicts, lists and tuples) They can be nested containers
    any combination of primary formats, str, int, float, dict and lists

    Parameters
    ----------
    first : dict | list | tuple | str | int | float
        The first object to compare
    second : dict | list | tuple | str | int | float
        The other object to compare with the first
    indent : str
        The amount newlines in the output string should be indented. Provide
        the actual indent, i.e. a string of spaces.

    Returns
    -------
    mismatch : str
        Human readable output highlighting where two container differ.
    """

    # Basic case where we are dealing with non-containers
    if not (isinstance(first, type(second)) or isinstance(second, type(first))):
        mismatch = " types: self={} v other={}".format(
            type(first).__name__, type(second).__name__
        )
    elif not iscontainer(first, second):
        mismatch = ": self={} v other={}".format(first, second)
    else:
        sub_indent = indent + "  "
        mismatch = ""
        if isinstance(first, dict):
            if sorted(first.keys()) != sorted(second.keys()):
                mismatch += " keys: self={} v other={}".format(
                    sorted(first.keys()), sorted(second.keys())
                )
            else:
                mismatch += ":"
                for k in first:
                    if first[k] != second[k]:
                        mismatch += "\n{indent}'{}' values{}".format(
                            k,
                            find_mismatch(first[k], second[k], indent=sub_indent),
                            indent=sub_indent,
                        )
        else:
            mismatch += ":"
            for i, (f, s) in enumerate(zip_longest(first, second)):
                if f != s:
                    mismatch += "\n{indent}{} index{}".format(
                        i, find_mismatch(f, s, indent=sub_indent), indent=sub_indent
                    )
    return mismatch


def wrap_text(text, line_length, indent, prefix_indent=False):
    """
    Wraps a text block to the specified line-length, without breaking across
    words, using the specified indent to join the lines

    Parameters
    ----------
    text : str
        The text to wrap
    line_length : int
        The desired line-length for the wrapped text (including indent)
    indent : int
        The number of spaces to use as an indent for the wrapped lines
    prefix_indent : bool
        Whether to prefix the indent to the wrapped text

    Returns
    -------
    wrapped : str
        The wrapped text
    """
    lines = []
    nchars = line_length - indent
    if nchars <= 0:
        raise ArcanaUsageError(
            "In order to wrap text, the indent cannot be larger than the " "line-length"
        )
    while text:
        if len(text) > nchars:
            n = text[:nchars].rfind(" ")
            if n < 1:
                next_space = text[nchars:].find(" ")
                if next_space < 0:
                    # No spaces found
                    n = len(text)
                else:
                    n = nchars + next_space
        else:
            n = nchars
        lines.append(text[:n])
        text = text[(n + 1) :]
    wrapped = "\n{}".format(" " * indent).join(lines)
    if prefix_indent:
        wrapped = " " * indent + wrapped
    return wrapped


class classproperty(object):
    def __init__(self, f):
        self.f = f

    def __get__(self, obj, owner):
        return self.f(owner)


def package_from_module(module: Sequence[str]):
    """Resolves the installed package (e.g. from PyPI) that provides the given
    module.

    Parameters
    ----------
    module: str or module or Sequence[str or module]
        a module or its import path string to retrieve the package for. Can be
        provided as a list of modules/strings, in which case a list of packages
        are returned

    Returns
    -------
    PackageInfo or list[PackageInfo]
        the package info object corresponding to the module. If `module`
        parameter is a list of modules/strings then a set of packages are
        returned
    """
    module_paths = set()
    if isinstance(module, Iterable) and not isinstance(module, str):
        modules = module
        as_tuple = True
    else:
        modules = [module]
        as_tuple = False
    for module in modules:
        try:
            module_path = module.__name__
        except AttributeError:
            module_path = module
        module_paths.add(importlib_metadata.PackagePath(module_path.replace(".", "/")))
    packages = set()
    for pkg in pkg_resources.working_set:
        try:
            paths = importlib_metadata.files(pkg.key)
        except importlib_metadata.PackageNotFoundError:
            continue
        match = False
        for path in paths:
            if path.suffix != ".py":
                continue
            path = path.with_suffix("")
            if path.name == "__init__":
                path = path.parent

            for module_path in copy(module_paths):
                if module_path in ([path] + list(path.parents)):
                    match = True
                    module_paths.remove(module_path)
        if match:
            packages.add(pkg)
            if not module_paths:  # If there are no more modules to find pkgs for
                break
    if module_paths:
        paths_str = "', '".join(str(p) for p in module_paths)
        raise ArcanaUsageError(f"Did not find package for {paths_str}")
    return tuple(packages) if as_tuple else next(iter(packages))


def pkg_versions(modules):
    versions = {p.key: p.version for p in package_from_module(modules)}
    versions["arcana"] = __version__
    return versions


def asdict(obj, omit: ty.Iterable[str] = (), required_modules: set = None):
    """Serialises an object of a class defined with attrs to a dictionary

    Parameters
    ----------
    obj
        The Arcana object to asdict. Must be defined using the attrs
        decorator
    omit: Iterable[str]
        the names of attributes to omit from the dictionary
    required_modules: set
        modules required to reload the serialised object into memory"""

    def filter(atr, value):
        return atr.init and atr.metadata.get("asdict", True)

    if required_modules is None:
        required_modules = set()
        include_versions = True  # Assume top-level dictionary so need to include
    else:
        include_versions = False

    def serialise_class(klass):
        required_modules.add(klass.__module__)
        return "<" + class2str(klass) + ">"

    def value_asdict(value):
        if isclass(value):
            value = serialise_class(value)
        elif hasattr(value, "asdict"):
            value = value.asdict(required_modules=required_modules)
        elif attrs.has(value):  # is class with attrs
            value_class = serialise_class(type(value))
            value = attrs.asdict(
                value,
                recurse=False,
                filter=filter,
                value_serializer=lambda i, f, v: value_asdict(v),
            )
            value["class"] = value_class
        elif isinstance(value, Enum):
            value = serialise_class(type(value)) + "[" + str(value) + "]"
        elif isinstance(value, Path):
            value = "file://" + str(value.resolve())
        elif isinstance(value, (tuple, list, set, frozenset)):
            value = [value_asdict(x) for x in value]
        elif isinstance(value, dict):
            value = {value_asdict(k): value_asdict(v) for k, v in value.items()}
        elif is_dataclass(value):
            value = [
                value_asdict(getattr(value, f.name)) for f in dataclass_fields(value)
            ]
        return value

    dct = attrs.asdict(
        obj,
        recurse=False,
        filter=lambda a, v: filter(a, v) and a.name not in omit,
        value_serializer=lambda i, f, v: value_asdict(v),
    )

    dct["class"] = serialise_class(type(obj))
    if include_versions:
        dct["pkg_versions"] = pkg_versions(required_modules)

    return dct


def fromdict(dct: dict, **kwargs):
    """Unserialise an object from a dict created by the `asdict` method

    Parameters
    ----------
    dct : dict
        A dictionary containing a serialsed Arcana object such as a data store
        or dataset definition
    omit: Iterable[str]
        key names to ignore when unserialising
    **kwargs : dict[str, Any]
        Additional initialisation arguments for the object when it is reinitialised.
        Overrides those stored"""
    # try:
    #     arcana_version = dct["pkg_versions"]["arcana"]
    # except (TypeError, KeyError):
    #     pass
    #     else:
    #         if packaging.version.parse(arcana_version) < packaging.version.parse(MIN_SERIAL_VERSION):
    #             raise ArcanaVersionError(
    #                 f"Serialised version ('{arcana_version}' is too old to be "
    #                 f"read by this version of arcana ('{__version__}'), the minimum "
    #                 f"version is {MIN_SERIAL_VERSION}")

    def field_filter(klass, field_name):
        if attrs.has(klass):
            return field_name in (f.name for f in attrs.fields(klass))
        else:
            return field_name != "class"

    def fromdict(value):
        if isinstance(value, dict):
            if "class" in value:
                klass = str2class(value["class"])
                if hasattr(klass, "fromdict"):
                    return klass.fromdict(value)
            value = {fromdict(k): fromdict(v) for k, v in value.items()}
            if "class" in value:
                value = klass(
                    **{k: v for k, v in value.items() if field_filter(klass, k)}
                )
        elif isinstance(value, str):
            if match := re.match(r"<(.*)>$", value):  # Class location
                value = str2class(match.group(1))
            elif match := re.match(r"<(.*)>\[(.*)\]$", value):  # Enum
                value = str2class(match.group(1))[match.group(2)]
            elif match := re.match(r"file://(.*)", value):
                value = Path(match.group(1))
        elif isinstance(value, Sequence):
            value = [fromdict(x) for x in value]
        return value

    klass = str2class(dct["class"])

    kwargs.update(
        {
            k: fromdict(v)
            for k, v in dct.items()
            if field_filter(klass, k) and k not in kwargs
        }
    )

    return klass(**kwargs)


extract_import_re = re.compile(r"\s*(?:from|import)\s+([\w\.]+)")

NOTHING_STR = "__PIPELINE_INPUT__"


def pydra_asdict(
    obj: TaskBase, required_modules: ty.Set[str], workflow: Workflow = None
) -> dict:
    """Converts a Pydra Task/Workflow into a dictionary that can be serialised

    Parameters
    ----------
    obj : pydra.engine.core.TaskBase
        the Pydra object to convert to a dictionary
    required_modules : set[str]
        a set of modules that are required to load the pydra object back
        out from disk and run it
    workflow : pydra.Workflow, optional
        the containing workflow that the object to serialised is part of

    Returns
    -------
    dict
        the dictionary containing the contents of the Pydra object
    """
    dct = {"name": obj.name, "class": "<" + class2str(obj) + ">"}
    if isinstance(obj, Workflow):
        dct["nodes"] = [
            pydra_asdict(n, required_modules=required_modules, workflow=obj)
            for n in obj.nodes
        ]
        dct["outputs"] = outputs = {}
        for outpt_name, lf in obj._connections:
            outputs[outpt_name] = {"task": lf.name, "field": lf.field}
    else:
        if isinstance(obj, FunctionTask):
            func = cp.loads(obj.inputs._func)
            module = inspect.getmodule(func)
            dct["class"] = "<" + module.__name__ + ":" + func.__name__ + ">"
            required_modules.add(module.__name__)
            # inspect source for any import lines (should be present in function
            # not module)
            for line in inspect.getsourcelines(func)[0]:
                if match := extract_import_re.match(line):
                    required_modules.add(match.group(1))
            # TODO: check source for references to external modules that aren't
            #       imported within function
        elif type(obj).__module__ != "pydra.engine.task":
            pkg = package_from_module(type(obj).__module__)
            dct["package"] = pkg.key
            dct["version"] = pkg.version
        if hasattr(obj, "container"):
            dct["container"] = {"type": obj.container, "image": obj.image}
    dct["inputs"] = inputs = {}
    for inpt_name in obj.input_names:
        if not inpt_name.startswith("_"):
            inpt_value = getattr(obj.inputs, inpt_name)
            if isinstance(inpt_value, LazyField):
                inputs[inpt_name] = {"field": inpt_value.field}
                # If the lazy field comes from the workflow lazy in, we omit
                # the "task" item
                if workflow is None or inpt_value.name != workflow.name:
                    inputs[inpt_name]["task"] = inpt_value.name
            elif inpt_value == attrs.NOTHING:
                inputs[inpt_name] = NOTHING_STR
            else:
                inputs[inpt_name] = inpt_value
    return dct


def lazy_field_fromdict(dct: dict, workflow: Workflow):
    """Unserialises a LazyField object from a dictionary"""
    if "task" in dct:
        inpt_task = getattr(workflow, dct["task"])
        lf = getattr(inpt_task.lzout, dct["field"])
    else:
        lf = getattr(workflow.lzin, dct["field"])
    return lf


def pydra_fromdict(dct: dict, workflow: Workflow = None, **kwargs) -> TaskBase:
    """Recreates a Pydra Task/Workflow from a dictionary object created by
    `pydra_asdict`

    Parameters
    ----------
    dct : dict
        dictionary representations of the object to recreate
    name : str
        name to give the object
    workflow : pydra.Workflow, optional
        the containing workflow that the object to recreate is connected to
    **kwargs
        additional keyword arguments passed to the pydra Object init method

    Returns
    -------
    pydra.engine.core.TaskBase
        the recreated Pydra object
    """
    klass = str2class(dct["class"])
    # Resolve lazy-field references to workflow fields
    inputs = {}
    for inpt_name, inpt_val in dct["inputs"].items():
        if inpt_val == NOTHING_STR:
            continue
        # Check for 'field' key in a dictionary val and convert to a
        # LazyField object
        if isinstance(inpt_val, dict) and "field" in inpt_val:
            inpt_val = lazy_field_fromdict(inpt_val, workflow=workflow)
        inputs[inpt_name] = inpt_val
    kwargs.update((k, v) for k, v in inputs.items() if k not in kwargs)
    if klass is Workflow:
        obj = Workflow(name=dct["name"], input_spec=list(dct["inputs"]), **kwargs)
        for node_dict in dct["nodes"]:
            obj.add(pydra_fromdict(node_dict, workflow=obj))
        obj.set_output(
            [
                (n, lazy_field_fromdict(f, workflow=obj))
                for n, f in dct["outputs"].items()
            ]
        )
    else:
        obj = klass(name=dct["name"], **kwargs)
    return obj


def pydra_eq(a: TaskBase, b: TaskBase):
    """Compares two Pydra Task/Workflows for equality

    Parameters
    ----------
    a : pydra.engine.core.TaskBase
        first object to compare
    b : pydra.engine.core.TaskBase
        second object to compare

    Returns
    -------
    bool
        whether the two objects are equal
    """
    if type(a) != type(b):
        return False
    if a.name != b.name:
        return False
    if sorted(a.input_names) != sorted(b.input_names):
        return False
    if a.output_spec.fields != b.output_spec.fields:
        return False
    for inpt_name in a.input_names:
        a_input = getattr(a.inputs, inpt_name)
        b_input = getattr(b.inputs, inpt_name)
        if isinstance(a_input, LazyField):
            if a_input.field != b_input.field or a_input.name != b_input.name:
                return False
        elif a_input != b_input:
            return False
    if isinstance(a, Workflow):
        a_node_names = [n.name for n in a.nodes]
        b_node_names = [n.name for n in b.nodes]
        if a_node_names != b_node_names:
            return False
        for node_name in a_node_names:
            if not pydra_eq(getattr(a, node_name), getattr(b, node_name)):
                return False
    else:
        if isinstance(a, FunctionTask):
            if a.inputs._func != b.inputs._func:
                return False
    return True


def show_workflow_errors(
    pipeline_cache_dir: Path, omit_nodes: ty.List[str] = None
) -> str:
    """Extract nodes with errors and display results

    Parameters
    ----------
    pipeline_cache_dir : Path
        the path container the pipeline cache directories
    omit_nodes : list[str], optional
        The names of the nodes to omit from the error message

    Returns
    -------
    str
        a string displaying the error messages
    """
    # PKL_FILES = ["_task.pklz", "_result.pklz", "_error.pklz"]
    out_str = ""

    def load_contents(fpath):
        contents = None
        if fpath.exists():
            with open(fpath, "rb") as f:
                contents = cp.load(f)
        return contents

    for path in pipeline_cache_dir.iterdir():
        if not path.is_dir():
            continue
        if "_error.pklz" in [p.name for p in path.iterdir()]:
            task = load_contents(path / "_task.pklz")
            if task.name in omit_nodes:
                continue
            if task:
                out_str += f"{task.name} ({type(task)}):\n"
                out_str += "    inputs:"
                for inpt_name in task.input_names:
                    out_str += (
                        f"\n        {inpt_name}: {getattr(task.inputs, inpt_name)}"
                    )
                try:
                    out_str += "\n\n    cmdline: " + task.cmdline
                except Exception:
                    pass
            else:
                out_str += "Anonymous task:\n"
            error = load_contents(path / "_error.pklz")
            out_str += "\n\n    errors:\n"
            for k, v in error.items():
                if k == "error message":
                    indent = "            "
                    out_str += (
                        "        message:\n"
                        + indent
                        + "".join(ln.replace("\n", "\n" + indent) for ln in v)
                    )
                else:
                    out_str += f"        {k}: {v}\n"
    return out_str


@attrs.define
class ObjectConverter:

    klass: type
    allow_none: bool = False
    accept_metadata: bool = False

    def __call__(self, value):
        self._create_object(value)

    def _create_object(self, value, **kwargs):
        if value is None:
            if self.allow_none:
                return None
            else:
                raise ValueError(
                    f"None values not accepted in automatic conversion to {self.klass}"
                )
        elif isinstance(value, dict):
            if self.accept_metadata:
                klass_attrs = set(attrs.fields_dict(self.klass))
                value_kwargs = {k: v for k, v in value.items() if k in klass_attrs}
                value_kwargs["metadata"] = {
                    k: v for k, v in value.items() if k not in klass_attrs
                }
            else:
                value_kwargs = value
            value_kwargs.update(kwargs)
            obj = value(**value_kwargs)
        elif isinstance(value, (list, tuple)):
            obj = self.klass(*value, **kwargs)
        elif isinstance(value, self.klass):
            obj = copy(value)
            for k, v in kwargs.items():
                setattr(obj, k, v)
        else:
            raise ValueError(f"Cannot convert {value} into {self.klass}")
        return obj


@attrs.define
class ObjectListConverter(ObjectConverter):
    def __call__(self, value):
        converted = []
        if isinstance(value, dict):
            for name, item in value.items():
                converted.append(self._create_object(item, name=name))
        else:
            for item in value:
                converted.append(self._create_object(item))
        return converted


def named_objects2dict(objs: list, **kwargs) -> dict:
    dct = {}
    for obj in objs:
        obj_dict = attrs.asdict(obj, **kwargs)
        dct[obj_dict.pop("name")] = obj_dict
    return dct


# @attrs.define
# class DictObjectConverter:

#     klass: type

#     def __call__(self, dct):
#         converted = {}
#         for key, val in dct.items():
#             if "name" in attrs.fields_dict(self.klass):
#                 val = copy(val)
#                 val["name"] = key
#             if isinstance(val, dict):
#                 val = self.klass(**val)
#             elif not isinstance(val, self.klass):
#                 raise ValueError(f"Cannot convert {val} into {self.klass}")
#             converted[key] = val
#         return converted


def str2datatype(datatype, **kwargs):
    from arcana.core.data.type import DataType
    from arcana.core.data.row import DataRow

    if isinstance(datatype, str):
        datatype = str2class(datatype, prefixes=["arcana.data.types"], **kwargs)
    elif not issubclass(datatype, (DataType, DataRow)):
        raise ValueError(f"Cannot resolve {datatype} to datatype")
    return datatype


def data_space_resolver(space, **kwargs):
    from arcana.core.data.space import DataSpace

    if isinstance(space, str):
        space = str2class(space, prefixes=["arcana.data.spaces"], **kwargs)
    elif not issubclass(space, DataSpace):
        raise ValueError(f"Cannot resolve {space} to data space")
    return space


def str2task(task, **kwargs):
    from pydra.engine.task import TaskBase

    if isinstance(task, str):
        task = str2class(task, prefixes=["arcana.tasks"], **kwargs)
    elif not isinstance(task, TaskBase):
        raise ValueError(f"Cannot resolve {task} to data space")
    return task


def extract_file_from_docker_image(
    image_tag, file_path: PosixPath, out_path: Path = None
) -> Path:
    """Extracts a file from a Docker image onto the local host

    Parameters
    ----------
    image_tag : str
        the name/tag of the image to extract the file from
    file_path : PosixPath
        the path to the file inside the image

    Returns
    -------
    Path or None
        path to the extracted file or None if image doesn't exist
    """
    tmp_dir = Path(tempfile.mkdtemp())
    if out_path is None:
        out_path = tmp_dir / "extracted-dir"
    dc = docker.from_env()
    try:
        dc.api.pull(image_tag)
    except docker.errors.APIError as e:
        if e.response.status_code in (404, 500):
            return None
        else:
            raise
    else:
        container = dc.containers.get(dc.api.create_container(image_tag)["Id"])
        try:
            tarfile_path = tmp_dir / "tar-file.tar.gz"
            with open(tarfile_path, mode="w+b") as f:
                try:
                    stream, _ = dc.api.get_archive(container.id, str(file_path))
                except docker.errors.NotFound:
                    pass
                else:
                    for chunk in stream:
                        f.write(chunk)
                    f.flush()
        finally:
            container.remove()
        with tarfile.open(tarfile_path) as f:
            f.extractall(out_path)
    return out_path


# Minimum version of Arcana that this version can read the serialisation from
MIN_SERIAL_VERSION = "0.0.0"


DOCKER_HUB = "docker.io"

# Global flag to allow references to classes to be missing from the


@attrs.define
class Str2ClassFallbackContext:

    permit: bool = False

    def __enter__(self):
        self.permit = True

    def __exit__(self, exception_type, exception_value, traceback):
        self.permit = False


STR2CLASS_FALLBACK = Str2ClassFallbackContext()

package_dir = os.path.join(os.path.dirname(__file__), "..")

try:
    HOSTNAME = sp.check_output("hostname").strip().decode("utf-8")
except sp.CalledProcessError:
    HOSTNAME = None
JSON_ENCODING = {"encoding": "utf-8"}
