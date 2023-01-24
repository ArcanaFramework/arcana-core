import os
import os.path
from tempfile import mkdtemp
import hashlib
from pathlib import Path
import operator as op
from functools import reduce
from arcana.core.data.set import Dataset
from arcana.dirtree.data import DirTree
from arcana.core.data.store import DataStore


def test_find_rows(dataset: Dataset):
    blueprint = dataset.__annotations__["blueprint"]
    for freq in dataset.space:
        # For all non-zero bases in the row_frequency, multiply the dim lengths
        # together to get the combined number of rows expected for that
        # row_frequency
        num_rows = reduce(
            op.mul, (ln for ln, b in zip(blueprint.dim_lengths, freq) if b), 1
        )
        assert (
            len(dataset.rows(freq)) == num_rows
        ), f"{freq} doesn't match {len(dataset.rows(freq))} vs {num_rows}"


def test_get_items(dataset: Dataset):
    blueprint = dataset.__annotations__["blueprint"]
    source_files = {}
    for fg_name, exp_datatypes in blueprint.expected_datatypes.items():
        for exp in exp_datatypes:
            source_name = fg_name + exp.datatype.class_name()
            dataset.add_source(source_name, path=fg_name, datatype=exp.datatype)
            source_files[source_name] = set(exp.filenames)
    for row in dataset.rows(dataset.leaf_freq):
        for source_name, files in source_files.items():
            item = row[source_name]
            item.get()
            assert set(os.path.basename(p) for p in item.fspaths) == files


def test_put_items(dataset: Dataset):
    blueprint = dataset.__annotations__["blueprint"]
    all_checksums = {}
    all_fspaths = {}
    for deriv in blueprint.derivatives:  # name, freq, datatype, files
        dataset.add_sink(
            name=deriv.name, datatype=deriv.datatype, row_frequency=deriv.row_frequency
        )
        deriv_tmp_dir = Path(mkdtemp())
        # Create test files, calculate checksums and recorded expected paths
        # for inserted files
        all_checksums[deriv.name] = checksums = {}
        all_fspaths[deriv.name] = fspaths = []
        for fname in deriv.filenames:
            test_file = DirTree().create_test_data_item(fname, deriv_tmp_dir)
            fhash = hashlib.md5()
            with open(deriv_tmp_dir / test_file, "rb") as f:
                fhash.update(f.read())
            try:
                rel_path = str(test_file.relative_to(deriv.filenames[0]))
            except ValueError:
                rel_path = ".".join(test_file.suffixes)[1:]
            checksums[rel_path] = fhash.hexdigest()
            fspaths.append(deriv_tmp_dir / test_file.parts[0])
        # Test inserting the new item into the store
        for row in dataset.rows(deriv.row_frequency):
            item = row[deriv.name]
            item.put(*fspaths)

    def check_inserted():
        """Check that the inserted items are present in the dataset"""
        for deriv in blueprint.derivatives:  # name, freq, datatype, _
            for row in dataset.rows(deriv.row_frequency):
                item = row[deriv.name]
                item.get_checksums()
                assert isinstance(item, deriv.datatype)
                assert item.checksums == all_checksums[deriv.name]
                item.get()
                assert all(p.exists() for p in item.fspaths)

    check_inserted()  # Check that cached objects have been updated
    dataset.refresh()  # Clear object cache
    check_inserted()  # Check that objects can be recreated from store


def test_singletons():
    assert sorted(DataStore.singletons()) == ["file"]