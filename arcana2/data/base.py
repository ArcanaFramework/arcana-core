from enum import Enum
from .file_format import FileFormat
from copy import copy
from logging import getLogger
from arcana2.exceptions import ArcanaUsageError
from future.types import newstr
from arcana2.utils import PATH_SUFFIX, FIELD_SUFFIX, CHECKSUM_SUFFIX
from .tree import TreeLevel
logger = getLogger('arcana')
    

class DataMixin(object):
    """Base class for all Data related classes
    """

    def __init__(self, tree_level, namespace):
        if not tree_level in TreeLevel:
            try:
                tree_level = TreeLevel[tree_level]
            except KeyError:
                raise ArcanaUsageError(
                    f"Invalid value for tree_level, {tree_level}, passed to "
                    f"initialisation of {type(self)}")
        self.tree_level = tree_level
        self.namespace = namespace

    def __eq__(self, other):
        return (self.tree_level == other.tree_level
                and self.namespace == other.namespace)

    def __hash__(self):
        return hash(self.tree_level) ^ hash(self.namespace)

    def find_mismatch(self, other, indent=''):
        if self != other:
            mismatch = "\n{}{} != {}".format(indent,
                                             type(self).__name__,
                                             type(other).__name__)
        else:
            mismatch = ''
        sub_indent = indent + '  '
        if self.tree_level != other.tree_level:
            mismatch += ('\n{}tree_level: self={} v other={}'
                         .format(sub_indent, self.tree_level,
                                 other.tree_level))
        if self.namespace != other.namespace:
            mismatch += ('\n{}tree_level: self={} v other={}'
                         .format(sub_indent, self.namespace,
                                 other.namespace))
        return mismatch

    def __ne__(self, other):
        return not (self == other)

    def initkwargs(self):
        return {'tree_level': self.tree_level,
                'namespace': self.namespace}


class FileGroupMixin(DataMixin):
    f"""
    An abstract base class representing either an acquired file_group or the
    specification for a derived file_group.

    Parameters
    ----------
    format : FileFormat
        The file format used to store the file_group. Can be one of the
        recognised formats
    tree_level : TreeLevel
        The level within the dataset tree that the file group sits, i.e. 
        per 'session', 'subject', 'visit', 'group_visit', 'group' or 'dataset'
    namespace : str
        The namespace within the tree node that the file-group is placed. Used
        to separate derivatives generated by different pipelines and analyses
    """

    is_file_group = True

    def __init__(self, format, tree_level, namespace):
        super().__init__(tree_level, namespace)
        self.format = format

    def __eq__(self, other):
        return (super().__eq__(other) and self.format == other.format)

    def __hash__(self):
        return (super().__hash__() ^ hash(self.format))

    def find_mismatch(self, other, indent=''):
        mismatch = super().find_mismatch(other, indent)
        sub_indent = indent + '  '
        if self.format != other.format:
            mismatch += ('\n{}format: self={} v other={}'
                         .format(sub_indent, self.format,
                                 other.format))
        return mismatch

    def __repr__(self):
        return ("{}(format={}, tree_level='{}')"
                .format(self.__class__.__name__, self.format, self.tree_level))

    def initkwargs(self):
        dct = super().initkwargs()
        dct['format'] = self.format
        return dct


class FieldMixin(DataMixin):
    """
    An abstract base class representing either an acquired value or the
    specification for a derived value.

    Parameters
    ----------
    dtype : type
        The datatype of the value. Can be one of (float, int, str)
    tree_level : TreeLevel
        The level within the dataset tree that the field sits, i.e. 
        per 'session', 'subject', 'visit', 'group_visit', 'group' or 'dataset'
    namespace : str
        The namespace within the tree node that the field is placed. Used to
        separate derivatives generated by different pipelines and analyses
    array : bool
        Whether the field contains scalar or array data
    """

    is_field = True

    dtypes = (int, float, str)

    def __init__(self, dtype, tree_level, namespace, array):
        super().__init__(tree_level, namespace)
        if dtype not in self.dtypes + (newstr, None):
            raise ArcanaUsageError(
                "Invalid dtype {}, can be one of {}".format(
                    dtype, ', '.join((d.__name__ for d in self.dtypes))))
        self.dtype = dtype
        self.array = array

    def __eq__(self, other):
        return (super().__eq__(other) and
                self.dtype == other.dtype and
                self.array == other.array)

    def __hash__(self):
        return (super().__hash__() ^ hash(self.dtype) ^ hash(self.array))

    def __repr__(self):
        return ("{}(dtype={}, tree_level='{}', array={})"
                .format(self.__class__.__name__, self.dtype,
                        self.tree_level, self.array))

    def find_mismatch(self, other, indent=''):
        mismatch = super(FieldMixin, self).find_mismatch(other, indent)
        sub_indent = indent + '  '
        if self.dtype != other.dtype:
            mismatch += ('\n{}dtype: self={} v other={}'
                         .format(sub_indent, self.dtype,
                                 other.dtype))
        if self.array != other.array:
            mismatch += ('\n{}array: self={} v other={}'
                         .format(sub_indent, self.array,
                                 other.array))
        return mismatch

    def initkwargs(self):
        dct = super(FieldMixin, self).initkwargs()
        dct['dtype'] = self.dtype
        dct['array'] = self.array
        return dct
