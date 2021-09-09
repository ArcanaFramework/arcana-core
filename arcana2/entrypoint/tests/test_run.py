import os.path
from unittest.mock import Mock
import pytest
from argparse import ArgumentParser
from arcana2.entrypoint.run import RunAppCmd


def test_run_app(test_data):
    parser = ArgumentParser()
    RunAppCmd.construct_parser(parser)
    args = parser.parse_args([
        'pydra.tasks.dcm2niix.Dcm2Niix',
        os.path.join(test_data, 'test-repo'),
        '--repository', 'file_system',
        '--input', 'in_dir', 'sample-dicom', 'dicom',
        '--output', 'out_file', 'output-nifti', 'niftix_gz',
        '--dimensions', 'clinical.Clinical',
        '--hierarchy', 'session',
        # '--dry_run',
        '--frequency', 'session'
        # '--ids', None,
        # '--container', None,
        # '--id_inference', None,
        # '--included', [],
        # '--excluded', [],
        # '--workflow_format', [],
        # '--app_arg', []
        ])
    workflow = RunAppCmd().run(args)
    workflow.pickle_task()
    # workflow()