import tempfile
import unittest
import pc_config


class PCConfigTest(unittest.TestCase):
    def test_identity_and_role_persist_and_other_pc_defaults_to_viewer(self):
        with tempfile.TemporaryDirectory() as directory:
            first = pc_config.load(directory, key='pc-one')
            self.assertEqual(first['role'], 'viewer')
            first['role'] = 'transfer'
            first['display_name'] = '転送PC'
            first['last_selection'] = dict(machine='AXN3', operator='Ohshiro (TO)', uploader='Sakano (SS)', experiment='Sakano_00', sample='OXT', kind='Sample', selection='Recent_Data', root=r'\\Rackstation\analysis')
            pc_config.save(first, directory)
            self.assertEqual(pc_config.load(directory, key='pc-one'), first)
            other = pc_config.load(directory, key='pc-two')
            self.assertEqual(other['role'], 'viewer')
            self.assertNotEqual(other['pc_id'], first['pc_id'])

    def test_invalid_settings_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            config = pc_config.load(directory, key='test')
            config['display_name'] = ' '
            with self.assertRaises(ValueError):
                pc_config.save(config, directory)
