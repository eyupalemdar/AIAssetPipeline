import copy
import json
from pathlib import Path
import tempfile
import unittest

from ai_asset_pipeline.density import plan_density, write_density_plan, rgba8_mip_bytes
from ai_asset_pipeline.pipeline import package_spec
from ai_asset_pipeline.quality_smoke import create_fixture
from ai_asset_pipeline.sampling import sha256, validate_bundle


class DensityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'Test.uproject').write_text('{}')
        base, _, _ = create_fixture(self.root, 'Density')
        self.spec_path = base / 'fixture.aiasset.json'
        self.spec = json.loads(self.spec_path.read_text())
        self.spec['components'] = [self.spec['components'][1]]
        self.spec['components'][0]['target_size'] = [32, 32]
        self.layout = self.root / 'Content/W_Test.uasset'
        self.layout.parent.mkdir(exist_ok=True)
        self.layout.write_bytes(b'layout fixture')
        self.evidence = {'$schema': 'ai-widget-pixel-footprint-v1', 'ok': True,
            'method': 'ue_local_to_viewport', 'scenario': 'FullHD', 'project_file': 'Test.uproject',
            'viewport_pixels': [1920, 1080], 'layout_assets': [{'path': 'Content/W_Test.uasset', 'sha256': sha256(self.layout)}],
            'samples': [{'component': 'rgba', 'widget': 'Image', 'visible': True, 'sampling': 'full_uv',
                'intermediate_render_target': False, 'pixel_size': [64, 64]}]}
        self.evidence_path = self.root / 'Footprint.json'
        self.policy_path = self.root / 'DensityPolicy.json'
        self.policy = {'$schema': 'ai-texture-density-policy-v1', 'spec': self.spec_path.relative_to(self.root).as_posix(),
            'texels_per_pixel': 1.25, 'max_texture_dimension': 1024, 'max_rgba8_mip_bytes': 1000000,
            'required_scenarios': ['FullHD'], 'allow_downsize': False, 'candidate_package': '/Game/DensityCandidate'}
        self.policy['expected_viewports'] = {'FullHD': [1920, 1080]}
        self.persist()

    def persist(self):
        self.spec_path.write_text(json.dumps(self.spec))
        self.evidence_path.write_text(json.dumps(self.evidence))
        self.policy['spec_sha256'] = sha256(self.spec_path)
        self.policy['evidence'] = [{'path': 'Footprint.json', 'sha256': sha256(self.evidence_path)}]
        self.policy_path.write_text(json.dumps(self.policy))

    def plan(self):
        return plan_density(self.policy_path, self.root)

    def test_physical_pixels_and_direct_source_packaging(self):
        report, candidate = self.plan()
        self.assertEqual(report['components'][0]['candidate_size'], [80, 80])
        self.assertTrue(report['coverage_complete'])
        self.assertEqual(candidate['source_art'], self.spec['source_art'])
        self.assertFalse(report['production_ready'])
        output = write_density_plan(self.policy_path, self.root, 'Candidate')
        packed = package_spec(self.root / output['candidate_spec'], project_root=self.root)
        manifest = json.loads((self.root / packed['manifest']).read_text())
        validate_bundle(manifest, self.root)
        self.assertEqual(manifest['outputs'][0]['target_size'], [80, 80])
        self.assertEqual(manifest['outputs'][0]['source_mip_count'], 5)

    def test_not_misreading_dpi_or_multiplying_repeated_seats(self):
        one, _ = self.plan()
        self.evidence['samples'] *= 4
        self.persist()
        four, _ = self.plan()
        self.assertEqual(one['candidate_rgba8_mip_bytes'], four['candidate_rgba8_mip_bytes'])
        self.assertEqual(four['components'][0]['candidate_size'], [80, 80])

    def test_current_adequate_resolution_is_kept_by_default(self):
        self.evidence['samples'][0]['pixel_size'] = [8, 8]
        self.persist()
        report, _ = self.plan()
        self.assertEqual(report['components'][0]['candidate_size'], [32, 32])
        self.assertEqual(report['components'][0]['status'], 'retain')

    def test_unmeasured_states_never_justify_downsizing(self):
        self.evidence['samples'][0].update(visible=False, pixel_size=[0, 0])
        self.policy['allow_downsize'] = True
        self.persist()
        report, _ = self.plan()
        self.assertFalse(report['coverage_complete'])
        self.assertEqual(report['components'][0]['status'], 'unmeasured_retained')
        self.assertEqual(report['components'][0]['candidate_size'], [32, 32])

    def test_sampling_crop_not_canvas_controls_available_detail(self):
        self.spec['components'][0]['approved_rgba_resize']['sampling_box'] = [0, 0, 48, 48]
        self.persist()
        report, _ = self.plan()
        row = report['components'][0]
        self.assertEqual(row['candidate_size'], [48, 48])
        self.assertTrue(row['source_limited'])
        self.assertFalse(row['meets_target'])

    def test_platform_cap_is_not_reported_as_successful_coverage(self):
        self.policy['max_texture_dimension'] = 40
        self.persist()
        report, _ = self.plan()
        self.assertEqual(report['components'][0]['candidate_size'], [40, 40])
        self.assertFalse(report['coverage_complete'])
        self.assertTrue(report['components'][0]['platform_limited'])

    def test_padding_outside_canvas_does_not_count_as_detail(self):
        self.spec['components'][0]['approved_rgba_resize']['sampling_box'] = [100, 0, 200, 100]
        self.persist()
        report, _ = self.plan()
        self.assertEqual(report['components'][0]['candidate_size'], [28, 28])
        self.assertTrue(report['components'][0]['source_limited'])
        self.spec['components'][0]['approved_rgba_resize']['sampling_box'] = [200, 0, 300, 100]
        self.persist()
        with self.assertRaisesRegex(ValueError, 'no source pixels'):
            self.plan()

    def test_budget_failure_does_not_write_candidate(self):
        self.policy['max_rgba8_mip_bytes'] = 1
        self.persist()
        with self.assertRaisesRegex(ValueError, 'budget'):
            write_density_plan(self.policy_path, self.root, 'Candidate')
        self.assertFalse((self.root / 'Candidate').exists())

    def test_original_layout_and_evidence_changes_rejected(self):
        paths = [self.layout, self.evidence_path, self.spec_path,
                 self.root / self.spec['source_art'][1]['path']]
        for path in paths:
            before = path.read_bytes()
            path.write_bytes(before + b'changed')
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'Changed|hash mismatch'):
                self.plan()
            path.write_bytes(before)

    def test_retainer_zero_nan_and_unknown_role_rejected(self):
        original = copy.deepcopy(self.evidence)
        for update in [{'intermediate_render_target': True}, {'pixel_size': [0, 2]},
                       {'pixel_size': [float('nan'), 4]}, {'component': 'missing'}, {'sampling': 'atlas'}, {'sampling': 'geometry_only'}]:
            self.evidence = copy.deepcopy(original)
            self.evidence['samples'][0].update(update)
            self.persist()
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.plan()

    def test_incomplete_scenarios_and_wrong_project_rejected(self):
        self.policy['required_scenarios'].append('4K')
        self.persist()
        with self.assertRaisesRegex(ValueError, 'Missing'):
            self.plan()
        self.policy['required_scenarios'] = ['FullHD']
        self.evidence['project_file'] = 'Other.uproject'
        self.persist()
        with self.assertRaisesRegex(ValueError, 'another project'):
            self.plan()

    def test_probe_and_existing_namespace_rejected(self):
        self.policy['candidate_package'] = '/Game/UI/_AIProbe/Bad'
        self.persist()
        with self.assertRaisesRegex(ValueError, 'probes'):
            self.plan()
        self.policy['candidate_package'] = '/Game/DensityCandidate'
        self.persist()
        (self.root / 'Content/DensityCandidate').mkdir()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            write_density_plan(self.policy_path, self.root, 'Candidate')

    def test_scenario_name_does_not_prove_viewport_resolution(self):
        self.evidence['viewport_pixels'] = [1280, 720]
        self.persist()
        with self.assertRaisesRegex(ValueError, 'Actual viewport differs'):
            self.plan()

    def test_payload_accounts_for_full_npot_mip_chain(self):
        self.assertEqual(rgba8_mip_bytes([3, 5]), (15 + 2 + 1) * 4)


if __name__ == '__main__':
    unittest.main()
