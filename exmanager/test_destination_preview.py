import tempfile
import unittest
from pathlib import Path

from destination_preview import sample_names


class DestinationPreviewTests(unittest.TestCase):
    def test_lists_only_samples_in_selected_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'experiment-A' / 'sample-2').mkdir(parents=True)
            (root / 'experiment-A' / 'sample-1').mkdir()
            (root / 'experiment-A' / 'notes.txt').touch()
            (root / 'experiment-B' / 'other-sample').mkdir(parents=True)
            self.assertEqual(sample_names(root, 'experiment-A'), ['sample-1', 'sample-2'])
            self.assertEqual(sample_names(root, 'experiment-B'), ['other-sample'])
            self.assertEqual(sample_names(root, 'new-experiment'), [])

    def test_unavailable_root_is_not_reported_as_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                sample_names(Path(directory) / 'offline', 'experiment')

    def test_empty_name_does_not_list_experiments_as_samples(self):
        self.assertEqual(sample_names('not-connected', ''), [])

    def test_path_traversal_is_rejected(self):
        for name in ('..', '../another', 'another/experiment', 'another\\experiment'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                sample_names('.', name)
