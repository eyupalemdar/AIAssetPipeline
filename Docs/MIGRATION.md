# Migration Notes

The first implementation seed was `Scripts/Tools/Image2AssetPipeline`.
AIAssetPipeline keeps compatibility with existing `image2-asset-spec-v1` and
`image2-asset-manifest-v1` fixtures, including the DarkIntegratedPanel V7-V10
specs.

New public specs should use:

- `$schema: "ai-asset-pipeline-spec-v1"`
- `source_art[].provenance.provider`
- `source_art[].provenance.model`
- `source_art[].provenance.generation_id`
- `source_art[].provenance.notes`

The old project-local scripts remain historical workflow artifacts. They should
not be expanded as public API for new asset families.
