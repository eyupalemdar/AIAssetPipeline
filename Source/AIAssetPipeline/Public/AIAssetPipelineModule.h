// Copyright (c) 2025 Alemdar Labs Ltd. All Rights Reserved.

#pragma once

#include "CoreMinimal.h"
#include "Modules/ModuleManager.h"

DECLARE_LOG_CATEGORY_EXTERN(LogAIAssetPipeline, Log, All);

class FAIAssetPipelineModule : public IModuleInterface
{
public:
	virtual void StartupModule() override;
	virtual void ShutdownModule() override;

	static FString GetPluginDir();
	static FString GetPythonDir();
	static FString GetSchemasDir();

private:
	void RegisterMcpCommands();
	void UnregisterMcpCommands();

	TArray<FString> RegisteredCommandNames;
};
