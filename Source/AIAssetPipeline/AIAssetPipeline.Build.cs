// Copyright (c) 2025 Alemdar Labs Ltd. All Rights Reserved.

using UnrealBuildTool;

public class AIAssetPipeline : ModuleRules
{
	public AIAssetPipeline(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"Json",
			"JsonUtilities",
			"MCPToolkit"
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"AssetRegistry",
			"Projects",
			"UnrealEd"
		});
	}
}
