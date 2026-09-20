import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from ai_asset_pipeline.quality import EditorConnection, texture_readback
from ai_asset_pipeline.quality_smoke import create_fixture, verify_fixture_tree
from ai_asset_pipeline.sampling import load_recipe, sha256, validate_bundle, validate_native_readback


class SamplingContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'Test.uproject').write_text('{}')
        self.base, self.recipe_path, _ = create_fixture(self.root, 'Test')
        self.recipe, self.manifest, self.outputs = load_recipe(self.recipe_path, self.root)

    def test_real_fixture_pixels_mips_and_hashes(self):
        self.assertEqual(set(self.outputs), {'atlas', 'rgba'})
        self.assertEqual(self.outputs['atlas']['source_mip_count'], 5)
        from PIL import Image
        for mip in self.outputs['atlas']['source_mips']:
            with Image.open(self.root / mip['png']) as image:
                self.assertEqual(image.getpixel((0, 0)), (255, 0, 0, 255))
                self.assertEqual(image.getpixel((image.width - 1, 0)), (0, 0, 255, 255))

    def test_widget_names_alone_do_not_prove_structure(self):
        nodes = [{'name': 'Root', 'widgetClass': 'Overlay', 'parentName': ''},
                 {'name': 'Image', 'widgetClass': 'Image', 'parentName': 'Root'}]
        tree = {'root': {'parent_class': 'UserWidget', 'root': {'name': 'Root', 'type': 'Overlay',
            'children': [{'name': 'Image', 'type': 'Image'}]}}}
        verify_fixture_tree(tree, nodes)
        tree['root']['root']['children'][0]['type'] = 'TextBlock'
        with self.assertRaisesRegex(ValueError, 'differs from TSpec'):
            verify_fixture_tree(tree, nodes)

    def test_changed_original_rejected(self):
        path = self.root / self.manifest['source_art'][0]['path']
        path.write_bytes(path.read_bytes() + b'changed')
        with self.assertRaisesRegex(ValueError, 'Changed source'):
            load_recipe(self.recipe_path, self.root)

    def test_changed_dds_rejected(self):
        path = self.root / self.outputs['atlas']['runtime_file']
        path.write_bytes(path.read_bytes()[:-1] + b'x')
        with self.assertRaisesRegex(ValueError, 'runtime'):
            load_recipe(self.recipe_path, self.root)

    def test_changed_manifest_rejected(self):
        path = self.root / self.recipe['manifest']
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'Changed manifest'):
            load_recipe(self.recipe_path, self.root)

    def test_changed_mip_rejected(self):
        path = self.root / self.outputs['atlas']['source_mips'][2]['png']
        path.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'mip 2'):
            load_recipe(self.recipe_path, self.root)

    def test_bad_grid_tail_and_data_srgb_rejected(self):
        for mutation, message in [
            (lambda row: row['atlas_mip_contract'].update(grid=[3, 2]), 'partial'),
            (lambda row: row['atlas_mip_contract'].update(max_sampled_lod=5), 'unsafe atlas LOD'),
            (lambda row: row.update(texture_type='packed_mask'), 'linear')]:
            with self.subTest(message=message):
                manifest = copy.deepcopy(self.manifest)
                mutation(next(x for x in manifest['outputs'] if x['component_id'] == 'atlas'))
                with self.assertRaisesRegex(ValueError, message):
                    validate_bundle(manifest, self.root)

    def test_explicit_cell_and_bias_required(self):
        for key in ('cell', 'bias'):
            recipe = copy.deepcopy(self.recipe)
            del recipe['materials'][0]['samples'][0][key]
            self.recipe_path.write_text(json.dumps(recipe))
            with self.assertRaises(ValueError):
                load_recipe(self.recipe_path, self.root)

    def test_project_escape_rejected(self):
        self.recipe['manifest'] = '../outside.json'
        self.recipe_path.write_text(json.dumps(self.recipe))
        with self.assertRaisesRegex(ValueError, 'escapes project'):
            load_recipe(self.recipe_path, self.root)

    def test_platform_resolution_and_runtime_mip_loss_rejected(self):
        data = {key: True for key in ('all_assets_exist', 'all_sizes_match', 'all_settings_match', 'all_source_mips_match')}
        data['assets'] = [dict(component_id=key, runtime_width=128, runtime_height=128, runtime_mip_count=8) for key in self.outputs]
        self.assertTrue(validate_native_readback(data, self.outputs)['ok'])
        data['assets'][0]['runtime_width'] = 64
        with self.assertRaisesRegex(ValueError, 'effective resource'):
            validate_native_readback(data, self.outputs)
        data['assets'][0]['runtime_width'] = 128
        data['assets'][0]['runtime_mip_count'] = 1
        with self.assertRaisesRegex(ValueError, 'too short'):
            validate_native_readback(data, self.outputs)

    def test_wrong_editor_rejected_before_mutations(self):
        with patch.object(EditorConnection, 'call', return_value={'project_dir': str(self.root / 'Other')}) as called:
            with self.assertRaisesRegex(ValueError, 'Wrong editor project'):
                EditorConnection(self.root, 55560)
            self.assertEqual(called.call_args_list[0].args, ('editor_identity',))
            self.assertEqual(called.call_count, 1)

    def test_editor_replacement_rejected_before_mutations(self):
        identities = [{'project_dir': str(self.root), 'editor_id': 'one', 'pid': 10},
                      {'project_dir': str(self.root), 'editor_id': 'two', 'pid': 11}]
        with patch.object(EditorConnection, '_request', side_effect=identities) as called:
            editor = EditorConnection(self.root, 55560)
            with self.assertRaisesRegex(ValueError, 'Editor changed'):
                editor.call('set_asset_property', {}, write=True)
            self.assertTrue(all(call.args[0] == 'editor_identity' for call in called.call_args_list))
            self.assertEqual(called.call_count, 2)

    def test_cold_resources_retry_reads_only(self):
        pending = {key: True for key in ('all_assets_exist', 'all_sizes_match', 'all_settings_match', 'all_source_mips_match')}
        pending['assets'] = [{'runtime_resource_ready': False}]
        ready = copy.deepcopy(pending)
        ready['assets'][0]['runtime_resource_ready'] = True
        connection = Mock()
        connection.call.side_effect = [pending, ready]
        with patch('ai_asset_pipeline.quality.time.sleep'):
            actual, attempts = texture_readback(connection, self.base / 'manifest.json')
        self.assertIs(actual, ready)
        self.assertEqual(attempts, 2)
        self.assertTrue(all(call.args[0] == 'asset_pipeline_verify_assets' and not call.kwargs for call in connection.call.call_args_list))

    def test_pending_resource_timeout_and_real_mismatch_are_not_hidden(self):
        data = {key: True for key in ('all_assets_exist', 'all_sizes_match', 'all_settings_match', 'all_source_mips_match')}
        data['assets'] = [{'runtime_resource_ready': False}]
        connection = Mock()
        connection.call.return_value = data
        with self.assertRaisesRegex(ValueError, 'still compiling'):
            texture_readback(connection, self.base / 'manifest.json', timeout=0)
        data['all_sizes_match'] = False
        actual, attempts = texture_readback(connection, self.base / 'manifest.json', timeout=0)
        self.assertIs(actual, data)
        self.assertEqual(attempts, 1)

    def test_ready_placeholder_waits_but_persistent_cap_fails(self):
        pending = {key: True for key in ('all_assets_exist', 'all_sizes_match', 'all_settings_match', 'all_source_mips_match')}
        pending['assets'] = [{'runtime_resource_ready': True, 'width': 240, 'height': 316,
                              'runtime_width': 4, 'runtime_height': 4}]
        ready = copy.deepcopy(pending)
        ready['assets'][0].update(runtime_width=240, runtime_height=316)
        connection = Mock()
        connection.call.side_effect = [pending, ready]
        with patch('ai_asset_pipeline.quality.time.sleep'):
            actual, attempts = texture_readback(connection, self.base / 'manifest.json')
        self.assertIs(actual, ready)
        self.assertEqual(attempts, 2)
        connection.call.side_effect = None
        connection.call.return_value = pending
        with self.assertRaisesRegex(ValueError, 'persistent resolution cap'):
            texture_readback(connection, self.base / 'manifest.json', timeout=0)

    def test_active_game_rejected_before_import_or_material_mutation(self):
        from ai_asset_pipeline.quality import run_sampling
        connection = Mock()
        connection.call.return_value = {'pie_active': True}
        with patch('ai_asset_pipeline.quality.EditorConnection', return_value=connection):
            with self.assertRaisesRegex(ValueError, 'Stop PIE'):
                run_sampling(self.recipe_path, self.root, 55560, apply=True, save=True, import_textures=True)
        connection.call.assert_called_once_with('pie_status')


if __name__ == '__main__':
    unittest.main()
