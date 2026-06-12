// Copyright (c) 2025 Alemdar Labs Ltd. All Rights Reserved.

#include "AIAssetPipelineModule.h"

#include "CommandDispatch/MCTCommandRegistry.h"
#include "Interfaces/IPluginManager.h"
#include "Modules/ModuleManager.h"

namespace AIAssetPipeline::Commands
{
FString HandleStatus(TSharedPtr<FJsonObject> Params);
FString HandleImportManifest(TSharedPtr<FJsonObject> Params);
FString HandleVerifyAssets(TSharedPtr<FJsonObject> Params);
}

DEFINE_LOG_CATEGORY(LogAIAssetPipeline);

namespace
{
MCPToolkit::CommandDispatch::FMCTCommandDescriptor MakeDescriptor(
	const FString& Name,
	const bool bRequiresParams,
	const bool bMutating,
	const int32 TimeoutSeconds,
	const FString& RequiredScope,
	const bool bSupportsDryRun)
{
	MCPToolkit::CommandDispatch::FMCTCommandDescriptor Descriptor;
	Descriptor.Name = Name;
	Descriptor.Category = TEXT("AIAssetPipeline");
	Descriptor.bRequiresParams = bRequiresParams;
	Descriptor.bMutating = bMutating;
	Descriptor.TimeoutSeconds = TimeoutSeconds;
	Descriptor.RequiredScope = RequiredScope;
	Descriptor.bSupportsDryRun = bSupportsDryRun;
	Descriptor.bAsyncCandidate = TimeoutSeconds >= 120;
	return Descriptor;
}
}

void FAIAssetPipelineModule::StartupModule()
{
	UE_LOG(LogAIAssetPipeline, Log, TEXT("AIAssetPipeline module started"));
	FModuleManager::LoadModuleChecked<IModuleInterface>(TEXT("MCPToolkit"));
	RegisterMcpCommands();
}

void FAIAssetPipelineModule::ShutdownModule()
{
	UnregisterMcpCommands();
	UE_LOG(LogAIAssetPipeline, Log, TEXT("AIAssetPipeline module shutdown"));
}

FString FAIAssetPipelineModule::GetPluginDir()
{
	TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIAssetPipeline"));
	return Plugin.IsValid() ? Plugin->GetBaseDir() : FString();
}

FString FAIAssetPipelineModule::GetPythonDir()
{
	const FString PluginDir = GetPluginDir();
	return PluginDir.IsEmpty() ? FString() : FPaths::Combine(PluginDir, TEXT("Resources"), TEXT("Python"));
}

FString FAIAssetPipelineModule::GetSchemasDir()
{
	const FString PluginDir = GetPluginDir();
	return PluginDir.IsEmpty() ? FString() : FPaths::Combine(PluginDir, TEXT("Resources"), TEXT("Schemas"));
}

void FAIAssetPipelineModule::RegisterMcpCommands()
{
	using namespace MCPToolkit::CommandDispatch;

	struct FRegistration
	{
		FMCTCommandDescriptor Descriptor;
		FMCTRegisteredCommandHandler Handler;
	};

	TArray<FRegistration> Registrations;
	Registrations.Add({
		MakeDescriptor(TEXT("asset_pipeline_status"), false, false, 0, TEXT("read"), false),
		[](TSharedPtr<FJsonObject> Params) { return AIAssetPipeline::Commands::HandleStatus(Params); }
	});
	Registrations.Add({
		MakeDescriptor(TEXT("asset_pipeline_import_manifest"), true, true, 300, TEXT("write"), true),
		[](TSharedPtr<FJsonObject> Params) { return AIAssetPipeline::Commands::HandleImportManifest(Params); }
	});
	Registrations.Add({
		MakeDescriptor(TEXT("asset_pipeline_verify_assets"), true, false, 120, TEXT("read"), false),
		[](TSharedPtr<FJsonObject> Params) { return AIAssetPipeline::Commands::HandleVerifyAssets(Params); }
	});

	for (FRegistration& Registration : Registrations)
	{
		FString Error;
		if (FMCTCommandRegistry::RegisterCommand(Registration.Descriptor, MoveTemp(Registration.Handler), Error))
		{
			RegisteredCommandNames.Add(Registration.Descriptor.Name);
		}
		else
		{
			UE_LOG(LogAIAssetPipeline, Error, TEXT("Failed to register command %s: %s"), *Registration.Descriptor.Name, *Error);
		}
	}
}

void FAIAssetPipelineModule::UnregisterMcpCommands()
{
	for (const FString& CommandName : RegisteredCommandNames)
	{
		MCPToolkit::CommandDispatch::FMCTCommandRegistry::UnregisterCommand(CommandName);
	}
	RegisteredCommandNames.Empty();
}

IMPLEMENT_MODULE(FAIAssetPipelineModule, AIAssetPipeline)
