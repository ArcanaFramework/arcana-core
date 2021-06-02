import weakref
from itertools import itemgetter
import logging
from collections import defaultdict
from itertools import chain
from collections import OrderedDict
from .item import UnresolvedFileGroup, UnresolvedField
from arcana2.exceptions import (
    ArcanaError, ArcanaNameError, ArcanaDataTreeConstructionError,
    ArcanaUsageError)

logger = logging.getLogger('arcana')


class Dataset():
    """
    A representation of a "dataset", the complete collection of data
    (file-sets and fields) to be used in an analysis.

    Parameters
    ----------
    name : str
        The name-name_path that uniquely identifies the datset within the
        repository it is stored
    repository : Repository
        The repository the dataset is stored into. Can be the local file
        system by providing a FileSystemDir repo.
    frequency_enum : type
        The enum that describes the tree structure of the dataset. See
        `arcana2.data.enum.DataFrequency`.
    include_ids : Dict[str, List[str]]
        The IDs to be included in the dataset for each frequency. E.g. can be
        used to limit the subject IDs in a project to the sub-set that passed
        QC. If a frequency is omitted or its value is None, then all available
        will be used
    """

    def __init__(self, name, repository, frequency_enum, include_ids=None):
        self.name = name
        self.repository = repository
        self.frequency_enum = frequency_enum
        self.include_ids = {f: None for f in frequency_enum}
        for freq, ids in include_ids:
            try:
                self.include_ids[frequency_enum[freq]] = list(ids)
            except KeyError:
                raise ArcanaUsageError(
                    f"Unrecognised data frequency '{freq}' (valid "
                    f"{', '.join(self.frequency_enum)})")
        # Add root node for tree
        self.root_node = DataNode(self.root_frequency, {}, self)

    def __repr__(self):
        return (f"Dataset(name='{self.name}', repository={self.repository}, "
                f"include_ids={self.include_ids})")

    def __eq__(self, other):
        return (self.name == other.name
                and self.repository == other.repository
                and self.include_ids == other.include_ids
                and self.root_node == other.root_node
                and self.frequency_enum == other.frequency_enum)

    def __hash__(self):
        return (hash(self.name)
                ^ hash(self.repository)
                ^ hash(self.include_ids)
                ^ hash(self.root_node)
                ^ hash(self.frequency_enum))

    def __getitem__(self, key):
        if key == self.frequency_enum(0):
            return self.root_node
        else:
            return self.root_node.subnodes[key]

    @property
    def prov(self):
        return {
            'name': self.name,
            'repository': self.repository.prov,
            'ids': {str(freq): tuple(ids) for freq, ids in self.nodes.items()}}

    @property
    def root_frequency(self):
        return self.frequency_enum(0)

    def __ne__(self, other):
        return not (self == other)

    def node(self, frequency, **ids):
        # Parse str to frequency enums
        frequency = self.frequency_enum[str(frequency)]
        if frequency == self.root_freq:
            if ids:
                raise ArcanaUsageError(
                    f"Root nodes don't have any IDs ({ids})")
            return self.root_node
        ids_tuple = self._ids_tuple(ids)
        try:
            return self.root_node.subnodes[frequency][ids_tuple]
        except KeyError:
            raise ArcanaNameError(
                ids_tuple,
                f"{ids_tuple} not present in data tree "
                "({})".format(
                    str(i) for i in self.root_node.subnodes[frequency]))

    def add_node(self, frequency, ids):
        """Adds a node to the dataset, creating references to upper and lower
        layers in the data tree.

        Parameters
        ----------
        frequency : DataFrequency
            The frequency of the data_node
        ids : Dict[DataFrequency, str]
            The IDs of the node and all branching points the data tree
            above it. The keys should match the Enum used provided for the
            'frequency

        Raises
        ------
        ArcanaDataTreeConstructionError
            If frequency is not of self.frequency.cls
        ArcanaDataTreeConstructionError
            If inserting a multiple IDs of the same class within the tree if
            one of their ids is None
        """
        if not isinstance(frequency, self.frequency_enum):
            raise ArcanaDataTreeConstructionError(
                f"Provided frequency {frequency} is not of "
                f"{self.frequency_enum} type")
        # Check conversion to frequency cls
        ids = {self.frequency_enum[str(f)]: i for f, i in ids.items()}
        # Create new data node
        node = DataNode(frequency, ids, self)
        basis_ids = {ids[f] for f in frequency.layers if f in ids}
        ids_tuple = tuple(basis_ids.items())
        node_dict = self.root_node.subnodes[frequency]
        if node_dict:
            if ids_tuple in node_dict:
                raise ArcanaDataTreeConstructionError(
                    f"ID clash ({ids_tuple}) between nodes inserted into data "
                    "tree")
            existing_tuple = next(iter(node_dict))
            if not ids_tuple or not existing_tuple:
                raise ArcanaDataTreeConstructionError(
                    f"IDs provided for some {frequency} nodes but not others"
                    f"in data tree ({ids_tuple} and {existing_tuple})")
            new_freqs = tuple(zip(ids_tuple))[0]
            exist_freqs = tuple(zip(existing_tuple))[0]
            if new_freqs != exist_freqs:
                raise ArcanaDataTreeConstructionError(
                    f"Inconsistent IDs provided for nodes in {frequency} "
                    f"in data tree ({ids_tuple} and {existing_tuple})")
        node_dict[ids_tuple] = node
        node._supranodes[self.frequency_enum(0)] = weakref.ref(self.root_node)
        # Insert nodes for basis layers if not already present and link them
        # with inserted node
        for supra_freq in frequency.layers:
            # Select relevant IDs from those provided
            supra_ids = {
                str(f): ids[f] for f in supra_freq.layers if f in ids}
            sub_ids = tuple((f, i) for f, i in ids_tuple
                            if f not in supra_freq.layers)
            try:
                supranode = self.node(supra_freq, **supra_ids)
            except ArcanaNameError:
                supranode = self.add_node(supra_freq, **supra_ids)
            # Set reference to level node in new node
            node.__supranodes[supra_freq] = weakref.ref(supranode)
            supranode.subnodes[frequency][sub_ids] = node
        return node

    def _ids_tuple(self, ids):
        """Generates a tuple in consistent order from the passed ids that can
        be used as a key in a dictionary

        Parameters
        ----------
        ids : Dict[DataFrequency | str, str]
            A dictionary with IDs for each frequency that specifies the
            nodes position within the data tree

        Returns
        -------
        Tuple[(DataFrequency, str)]
            A tuple sorted in order of provided frequencies
        """
        try:
            return tuple((self.frequency_enum[str(f)], i)
                         for f, i in sorted(ids.items(), key=itemgetter(1)))
        except KeyError:
            raise ArcanaUsageError(
                    f"Unrecognised data frequencies in ID dict '{ids}' (valid "
                    f"{', '.join(self.frequency_enum)})")


class DataNode():
    """A "node" in a data tree where file-groups and fields can be placed, e.g.
    a session or subject.

    Parameters
    ----------
    frequency : DataFrequency
        The frequency of the node
    ids : Dict[DataFrequency, str]
        The ids for each provided frequency need to specify the data node
        within the tree
    root : DataNode
        A reference to the root of the data tree
    """

    def __init__(self, frequency, ids, dataset):
        self.ids = ids
        self.frequency = frequency
        self._file_groups = OrderedDict()
        self._fields = OrderedDict()
        self._provenances = OrderedDict()
        self.subnodes = defaultdict(dict)
        self._supranodes = {}  # Refs to level (e.g. session -> subject)
        self._dataset = weakref.ref(dataset)
        

    def __eq__(self, other):
        if not (isinstance(other, type(self))
                or isinstance(self, type(other))):
            return False
        return (tuple(self._file_groups) == tuple(other._file_groups)
                and tuple(self._fields) == tuple(other._fields)
                and tuple(self._provenances) == tuple(other._provenances))

    def __hash__(self):
        return (hash(tuple(self._file_groups)) ^ hash(tuple(self._fields))
                ^ hash(tuple(self._provenances)))

    def add_file_group(self, name_path, *args, **kwargs):
        self._file_groups[name_path] = UnresolvedFileGroup(
            name_path, *args, data_node=self, **kwargs)

    def add_field(self, name_path, *args, **kwargs):
        self._fields[name_path] = UnresolvedField(name_path, *args, data_node=self,
                                             **kwargs)

    def file_group(self, name_path, file_format=None):
        """
        Gets the file_group with the ID 'id' produced by the Analysis named
        'analysis' if provided. If a spec is passed instead of a str to the
        name argument, then the analysis will be set from the spec iff it is
        derived

        Parameters
        ----------
        name_path : str
            The name_path to the file_group within the tree node, e.g. anat/T1w
        file_format : FileFormat | Sequence[FileFormat] | None
            A file format, or sequence of file formats, which are used to
            resolve the format of the file-group

        Returns
        -------
        FileGroup | UnresolvedFileGroup
            The file-group corresponding to the given name_path. If a, or
            multiple, candidate file formats are provided then the format of
            the file-group is resolved and a FileGroup object is returned.
            Otherwise, an UnresolvedFileGroup is returned instead.
        """
        try:
            file_group = self._file_groups[name_path]
        except KeyError:
            raise ArcanaNameError(
                name_path,
                (f"{self} doesn't have a file_group at the name_path {name_path} "
                 "(available '{}')".format("', '".join(self.file_groups))))
        else:
            if file_format is not None:
                file_group = file_group.resolve_format(file_format)
        return file_group

    def field(self, name_path):
        """
        Gets the field named 'name' produced by the Analysis named 'analysis'
        if provided. If a spec is passed instead of a str to the name argument,
        then the analysis will be set from the spec iff it is derived

        Parameters
        ----------
        name_path : str
            The name_path of the field within the node
        """
        # if isinstance(name, FieldMixin):
        #     if namespace is None and name.derived:
        #         namespace = name.analysis.name
        #     name = name.name
        try:
            return self._fields[name_path]
        except KeyError:
            raise ArcanaNameError(
                name_path, ("{} doesn't have a field named '{}' "
                       "(available '{}')").format(
                           self, name_path, "', '".join(self._fields)))

    def provenance(self, name_path):
        """
        Returns the provenance provenance for a given pipeline

        Parameters
        ----------
        name_path : str
            The name of the pipeline that generated the provenance

        Returns
        -------
        provenance : arcana2.data.item.Provenance
            The provenance provenance generated by the specified pipeline
        """
        try:
            return self._provenances[name_path]
        except KeyError:
            raise ArcanaNameError(
                name_path,
                ("{} doesn't have a provenance provenance for '{}' "
                 "(found {})".format(self, name_path, '; '.join(self.provenances))))

    def supranode(self, frequency):
        node = self.__supranodes[frequency]()
        if node is None:
            raise ArcanaError(
                f"Node referenced by {self} for {frequency} no longer exists")
        return node

    @property
    def file_groups(self):
        return self._file_groups.values()
    
    @property
    def fields(self):
        return self._fields.values()


    @property
    def provenances(self):
        return self._provenances.values()

    @property
    def data(self):
        return chain(self.file_groups, self.fields)

    @property
    def dataset(self):
        dataset = self._dataset()
        if dataset is None:
            raise ArcanaError(
                "Dataset referenced by data node no longer exists")
        return dataset

    def __ne__(self, other):
        return not (self == other)

    def find_mismatch(self, other, indent=''):
        """
        Highlights where two nodes differ in a human-readable form

        Parameters
        ----------
        other : TreeNode
            The node to compare
        indent : str
            The white-space with which to indent output string

        Returns
        -------
        mismatch : str
            The human-readable mismatch string
        """
        if self != other:
            mismatch = "\n{}{}".format(indent, type(self).__name__)
        else:
            mismatch = ''
        sub_indent = indent + '  '
        if len(list(self.file_groups)) != len(list(other.file_groups)):
            mismatch += ('\n{indent}mismatching summary file_group lengths '
                         '(self={} vs other={}): '
                         '\n{indent}  self={}\n{indent}  other={}'
                         .format(len(list(self.file_groups)),
                                 len(list(other.file_groups)),
                                 list(self.file_groups),
                                 list(other.file_groups),
                                 indent=sub_indent))
        else:
            for s, o in zip(self.file_groups, other.file_groups):
                mismatch += s.find_mismatch(o, indent=sub_indent)
        if len(list(self.fields)) != len(list(other.fields)):
            mismatch += ('\n{indent}mismatching summary field lengths '
                         '(self={} vs other={}): '
                         '\n{indent}  self={}\n{indent}  other={}'
                         .format(len(list(self.fields)),
                                 len(list(other.fields)),
                                 list(self.fields),
                                 list(other.fields),
                                 indent=sub_indent))
        else:
            for s, o in zip(self.fields, other.fields):
                mismatch += s.find_mismatch(o, indent=sub_indent)
        if len(list(self.provenances)) != len(list(other.provenances)):
            mismatch += ('\n{indent}mismatching summary provenance lengths '
                         '(self={} vs other={}): '
                         '\n{indent}  self={}\n{indent}  other={}'
                         .format(len(list(self.provenances)),
                                 len(list(other.provenances)),
                                 list(self.provenances),
                                 list(other.provenances),
                                 indent=sub_indent))
        else:
            for s, o in zip(self.provenances, other.provenances):
                mismatch += s.find_mismatch(o, indent=sub_indent)
        return mismatch