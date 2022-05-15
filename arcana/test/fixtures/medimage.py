from datetime import datetime
from pathlib import Path
from tempfile import mkdtemp
import pytest
import xnat4tests
from arcana.data.stores.common import FileSystem
from arcana.data.stores.medimage.xnat.api import Xnat
from arcana.data.spaces.medimage import Clinical
from arcana.data.formats.common import Text, Directory
from arcana.data.formats.medimage import NiftiGzX, NiftiGz, Dicom
from arcana.test.stores.medimage.xnat import (
    make_mutable_dataset,
    TestXnatDatasetBlueprint,
    ResourceBlueprint,
    ScanBlueprint,
    DerivBlueprint,
    create_dataset_data_in_repo,
    make_project_name,
    access_dataset
)


@pytest.fixture(scope='session')
def nifti_sample_dir():
    return Path(__file__).parent.parent.parent.parent / 'test-data'/ 'nifti'


TEST_DICOM_DATASET_DIR = Path(__file__).parent / 'test-dataset'

@pytest.fixture(scope='session')
def dicom_dataset(test_dicom_dataset_dir):
    return FileSystem().dataset(
        test_dicom_dataset_dir,
        hierarchy=['session'])


@pytest.fixture(scope='session')
def test_dicom_dataset_dir():
    return TEST_DICOM_DATASET_DIR


# -----------------------
# Test dataset structures
# -----------------------


TEST_XNAT_DATASET_BLUEPRINTS = {
    'basic': TestXnatDatasetBlueprint(  # dataset name
        [1, 1, 3],  # number of timepoints, groups and members respectively
        [ScanBlueprint('scan1',  # scan type (ID is index)
          [ResourceBlueprint(
              'Text', # resource name
              Text,  # Data format
              ['file.txt'])]),  # name files to place within resource
         ScanBlueprint('scan2',
          [ResourceBlueprint(
              'NiftiGzX',
              NiftiGzX,
              ['file.nii.gz', 'file.json'])]),
         ScanBlueprint('scan3',
          [ResourceBlueprint(
              'Directory',
              Directory,
              ['doubledir', 'dir', 'file.dat'])]),
         ScanBlueprint('scan4',
          [ResourceBlueprint('DICOM', Dicom, ['file1.dcm', 'file2.dcm', 'file3.dcm']),
           ResourceBlueprint('NIFTI', NiftiGz, ['file1.nii.gz']),
           ResourceBlueprint('BIDS', None, ['file1.json']),
           ResourceBlueprint('SNAPSHOT', None, ['file1.png'])])],
        [],
        [DerivBlueprint('deriv1', Clinical.timepoint, Text, ['file.txt']),
         DerivBlueprint('deriv2', Clinical.subject, NiftiGzX, ['file.nii.gz', 'file.json']),
         DerivBlueprint('deriv3', Clinical.batch, Directory, ['dir']),
         DerivBlueprint('deriv4', Clinical.dataset, Text, ['file.txt']),
         ]),  # id_inference dict
    'multi': TestXnatDatasetBlueprint(  # dataset name
        [2, 2, 2],  # number of timepoints, groups and members respectively
        [ScanBlueprint('scan1', [ResourceBlueprint('Text', Text, ['file.txt'])])],
        [('subject', r'group(?P<group>\d+)member(?P<member>\d+)'),
         ('session', r'timepoint(?P<timepoint>\d+).*')],  # id_inference dict
        [
         DerivBlueprint('deriv1', Clinical.session, Text, ['file.txt']),
         DerivBlueprint('deriv2', Clinical.subject, NiftiGzX, ['file.nii.gz', 'file.json']),
         DerivBlueprint('deriv3', Clinical.timepoint, Directory, ['doubledir']),
         DerivBlueprint('deriv4', Clinical.member, Text, ['file.txt']),
         DerivBlueprint('deriv5', Clinical.dataset, Text, ['file.txt']),
         DerivBlueprint('deriv6', Clinical.batch, Text, ['file.txt']),
         DerivBlueprint('deriv7', Clinical.matchedpoint, Text, ['file.txt']),
         DerivBlueprint('deriv8', Clinical.group, Text, ['file.txt']),
         ]),
    'concatenate_test': TestXnatDatasetBlueprint(
        [1, 1, 2],
        [
            ScanBlueprint(
                'scan1',
                [ResourceBlueprint('Text', Text, ['file1.txt'])]),
            ScanBlueprint(
                'scan2',
                [ResourceBlueprint('Text', Text, ['file2.txt'])])],
        {},
        [DerivBlueprint('concatenated', Clinical.session, Text, ['concatenated.txt'])])}

GOOD_DATASETS = ['basic.api', 'multi.api', 'basic.cs', 'multi.cs']
MUTABLE_DATASETS = ['basic.api', 'multi.api', 'basic.cs', 'multi.cs']

# ------------------------------------
# Pytest fixtures and helper functions
# ------------------------------------


@pytest.fixture(params=GOOD_DATASETS, scope='session')
def xnat_dataset(xnat_repository, xnat_archive_dir, request):
    dataset_name, access_method = request.param.split('.')
    blueprint = TEST_XNAT_DATASET_BLUEPRINTS[dataset_name]
    with xnat4tests.connect() as login:
        if make_project_name(dataset_name,
                             xnat_repository.run_prefix) not in login.projects:
            create_dataset_data_in_repo(dataset_name, blueprint, xnat_repository.run_prefix)    
    return access_dataset(dataset_name=dataset_name,
                          blueprint=blueprint,
                          xnat_repository=xnat_repository,
                          xnat_archive_dir=xnat_archive_dir,
                          access_method=access_method)    


@pytest.fixture(params=MUTABLE_DATASETS, scope='function')
def mutable_xnat_dataset(xnat_repository, xnat_archive_dir, request):
    dataset_name, access_method = request.param.split('.')
    blueprint = TEST_XNAT_DATASET_BLUEPRINTS[dataset_name]
    return make_mutable_dataset(dataset_name=dataset_name,
                                blueprint=blueprint,
                                xnat_repository=xnat_repository,
                                xnat_archive_dir=xnat_archive_dir,
                                access_method=access_method)


@pytest.fixture(scope='session')
def xnat_root_dir():
    return xnat4tests.config.XNAT_ROOT_DIR


@pytest.fixture(scope='session')
def xnat_archive_dir(xnat_root_dir):
    return xnat_root_dir / 'archive'


@pytest.fixture(scope='session')
def xnat_repository(run_prefix):

    xnat4tests.launch_xnat()

    repository = Xnat(
        server=xnat4tests.config.XNAT_URI,
        user=xnat4tests.config.XNAT_USER,
        password=xnat4tests.config.XNAT_PASSWORD,
        cache_dir=mkdtemp())

    # Stash a project prefix in the repository object
    repository.run_prefix = run_prefix

    yield repository


@pytest.fixture(scope='session')
def run_prefix():
    "A datetime string used to avoid stale data left over from previous tests"
    return datetime.strftime(datetime.now(), '%Y%m%d%H%M%S')


@pytest.fixture(scope='session')
def xnat_respository_uri(xnat_repository):
    return xnat_repository.server
