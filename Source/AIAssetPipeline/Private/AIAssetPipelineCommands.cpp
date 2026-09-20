// Copyright (c) 2025 Alemdar Labs Ltd. All Rights Reserved.

#include "AIAssetPipelineModule.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "Builders/MCTAssetImportBuilder.h"
#include "CommandHandlers/MCTCommandResponse.h"
#include "Dom/JsonObject.h"
#include "Dom/JsonValue.h"
#include "Engine/Texture2D.h"
#include "TextureCompiler.h"
#include "Interfaces/IPluginManager.h"
#include "Async/Async.h"
#include "HAL/FileManager.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/SavePackage.h"
#include "Editor.h"
#include "Blueprint/SlateBlueprintLibrary.h"
#include "Blueprint/WidgetBlueprintLibrary.h"
#include "Blueprint/WidgetLayoutLibrary.h"
#include "Blueprint/UserWidget.h"
#include "Components/PanelWidget.h"
#include "Components/RetainerBox.h"
#include "Components/Image.h"
#include "Engine/World.h"

namespace AIAssetPipeline::Commands
{
namespace
{
using MCPToolkit::CommandHandlers::CreateErrorResponse;
using MCPToolkit::CommandHandlers::CreateSuccessResponse;

struct FManifestOutput
{
	FString ComponentId;
	FString RuntimeFile;
	FString PackagePath;
	FString AssetName;
	FString AssetPath;
	FString TextureType;
	FString Compression;
	FString SourceFormat;
	FString MipGen;
	FString LODGroup;
	FString AddressX;
	FString AddressY;
	FString Filter;
	bool bSRGB = true;
	bool bNeverStream = true;
	int32 TargetWidth = 0;
	int32 TargetHeight = 0;
	int32 ExpectedSourceMipCount = 0;
	TextureCompressionSettings ParsedCompression = TC_EditorIcon;
	TextureMipGenSettings ParsedMipGen = TMGS_NoMipmaps;
	TextureGroup ParsedGroup = TEXTUREGROUP_UI;
	TextureFilter ParsedFilter = TF_Bilinear;
};

struct FManifestData
{
	FString ManifestPath;
	FString Schema;
	FString Version;
	FString TexturePackagePath;
	TArray<FManifestOutput> Outputs;
};

FString ToFullProjectPath(const FString& PathValue)
{
	FString Normalized = PathValue;
	Normalized.ReplaceInline(TEXT("\\"), TEXT("/"));
	if (FPaths::IsRelative(Normalized))
	{
		Normalized = FPaths::Combine(FPaths::ProjectDir(), Normalized);
	}
	return FPaths::ConvertRelativePathToFull(Normalized);
}

bool IsUnderProject(const FString& FullPath)
{
	return FPaths::IsUnderDirectory(FullPath, FPaths::ConvertRelativePathToFull(FPaths::ProjectDir()));
}

bool ReadString(TSharedPtr<FJsonObject> Object, const TCHAR* FieldName, FString& OutValue)
{
	return Object.IsValid() && Object->TryGetStringField(FieldName, OutValue) && !OutValue.IsEmpty();
}

FString ReadStringOrDefault(TSharedPtr<FJsonObject> Object, const TCHAR* FieldName, const FString& DefaultValue = FString())
{
	FString Value;
	return ReadString(Object, FieldName, Value) ? Value : DefaultValue;
}

bool ReadBoolOrDefault(TSharedPtr<FJsonObject> Object, const TCHAR* FieldName, const bool bDefault)
{
	bool bValue = bDefault;
	if (Object.IsValid())
	{
		Object->TryGetBoolField(FieldName, bValue);
	}
	return bValue;
}

bool ReadIntArray2(TSharedPtr<FJsonObject> Object, const TCHAR* FieldName, int32& OutX, int32& OutY)
{
	const TArray<TSharedPtr<FJsonValue>>* Array = nullptr;
	if (!Object.IsValid() || !Object->TryGetArrayField(FieldName, Array) || !Array || Array->Num() != 2)
	{
		return false;
	}

	OutX = static_cast<int32>((*Array)[0]->AsNumber());
	OutY = static_cast<int32>((*Array)[1]->AsNumber());
	return OutX > 0 && OutY > 0;
}

FString NormalizeAssetPath(const FString& PackagePath, const FString& AssetName, const FString& ExistingAssetPath)
{
	if (!ExistingAssetPath.IsEmpty())
	{
		return ExistingAssetPath;
	}
	return PackagePath.IsEmpty() || AssetName.IsEmpty() ? FString() : PackagePath / AssetName;
}

FString ToObjectPath(const FString& AssetPath)
{
	if (AssetPath.Contains(TEXT(".")))
	{
		return AssetPath;
	}
	return FString::Printf(TEXT("%s.%s"), *AssetPath, *FPackageName::GetShortName(AssetPath));
}

FString DiskAssetPath(const FString& PackagePath, const FString& AssetName)
{
	const FString LongPackageName = PackagePath / AssetName;
	return FPackageName::LongPackageNameToFilename(LongPackageName, FPackageName::GetAssetPackageExtension());
}

bool LoadManifest(const FString& ManifestPathValue, FManifestData& OutManifest, FString& OutError)
{
	if (ManifestPathValue.IsEmpty())
	{
		OutError = TEXT("Missing 'manifest_path' parameter");
		return false;
	}

	const FString ManifestPath = ToFullProjectPath(ManifestPathValue);
	if (!IsUnderProject(ManifestPath))
	{
		OutError = TEXT("manifest_path must resolve under the project directory");
		return false;
	}

	FString Text;
	if (!FFileHelper::LoadFileToString(Text, *ManifestPath))
	{
		OutError = FString::Printf(TEXT("Failed to read manifest: %s"), *ManifestPath);
		return false;
	}

	TSharedPtr<FJsonObject> Root;
	TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Text);
	if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
	{
		OutError = FString::Printf(TEXT("Invalid manifest JSON: %s"), *ManifestPath);
		return false;
	}

	FString Schema;
	if (!Root->TryGetStringField(TEXT("$schema"), Schema)
		|| (Schema != TEXT("ai-asset-pipeline-manifest-v1") && Schema != TEXT("image2-asset-manifest-v1")))
	{
		OutError = TEXT("$schema must be ai-asset-pipeline-manifest-v1 or image2-asset-manifest-v1");
		return false;
	}

	const TArray<TSharedPtr<FJsonValue>>* OutputsArray = nullptr;
	if (!Root->TryGetArrayField(TEXT("outputs"), OutputsArray) || !OutputsArray || OutputsArray->IsEmpty())
	{
		OutError = TEXT("manifest outputs must be a non-empty array");
		return false;
	}

	const TSharedPtr<FJsonObject>* AlphaContract = nullptr;
	if (!Root->TryGetObjectField(TEXT("alpha_contract"), AlphaContract) || !AlphaContract || !AlphaContract->IsValid())
	{
		OutError = TEXT("manifest alpha_contract is required");
		return false;
	}

	const TArray<FString> RequiredTrueFields = {
		TEXT("all_transparent_corners"),
		TEXT("all_transparent_outer_edges"),
		TEXT("all_no_visible_chroma_key"),
		TEXT("all_no_visible_magenta_fringe"),
		TEXT("all_no_low_alpha_saturated_chroma_fringe"),
		TEXT("all_no_hidden_saturated_chroma"),
		TEXT("all_no_low_alpha_saturated_rgb_artifacts"),
		TEXT("all_no_hidden_saturated_rgb_artifacts")
	};
	for (const FString& FieldName : RequiredTrueFields)
	{
		bool bValue = false;
		if (!(*AlphaContract)->TryGetBoolField(FieldName, bValue) || !bValue)
		{
			OutError = FString::Printf(TEXT("alpha_contract failed or missing: %s"), *FieldName);
			return false;
		}
	}

	double ComponentCount = 0.0;
	if (!(*AlphaContract)->TryGetNumberField(TEXT("component_count"), ComponentCount)
		|| static_cast<int32>(ComponentCount) != OutputsArray->Num())
	{
		OutError = TEXT("alpha_contract component_count does not match outputs length");
		return false;
	}

	FManifestData Manifest;
	double ApprovedCount = 0;
	if ((*AlphaContract)->TryGetNumberField(TEXT("approved_rgba_component_count"), ApprovedCount) && ApprovedCount > 0)
	{
		bool bPreserved = false;
		if (!(*AlphaContract)->TryGetBoolField(TEXT("all_approved_rgba_alpha_preserved"), bPreserved) || !bPreserved)
		{
			OutError = TEXT("approved RGBA alpha preservation contract failed");
			return false;
		}
	}
	Manifest.ManifestPath = ManifestPath;
	Manifest.Schema = Schema;
	Root->TryGetStringField(TEXT("version"), Manifest.Version);
	Root->TryGetStringField(TEXT("texture_package_path"), Manifest.TexturePackagePath);

	for (const TSharedPtr<FJsonValue>& OutputValue : *OutputsArray)
	{
		const TSharedPtr<FJsonObject>* OutputObject = nullptr;
		if (!OutputValue.IsValid() || !OutputValue->TryGetObject(OutputObject) || !OutputObject || !OutputObject->IsValid())
		{
			OutError = TEXT("manifest outputs entries must be objects");
			return false;
		}

		FManifestOutput Output;
		Output.ComponentId = ReadStringOrDefault(*OutputObject, TEXT("component_id"));
		Output.RuntimeFile = ReadStringOrDefault(*OutputObject, TEXT("runtime_file"));
		Output.PackagePath = ReadStringOrDefault(*OutputObject, TEXT("ue_package_path"), Manifest.TexturePackagePath);
		Output.AssetName = ReadStringOrDefault(*OutputObject, TEXT("ue_asset_name"));
		Output.AssetPath = NormalizeAssetPath(Output.PackagePath, Output.AssetName, ReadStringOrDefault(*OutputObject, TEXT("ue_asset_path")));
		Output.TextureType = ReadStringOrDefault(*OutputObject, TEXT("texture_type"), TEXT("color"));
		const bool bMaskType = Output.TextureType == TEXT("mask");
		const bool bLinearData = bMaskType || Output.TextureType == TEXT("packed_mask");
		Output.Compression = TEXT("UserInterface2D");
		Output.SourceFormat = TEXT("auto");
		Output.MipGen = TEXT("NoMipmaps");
		Output.LODGroup = TEXT("UI");
		Output.AddressX = TEXT("Clamp");
		Output.AddressY = TEXT("Clamp");
		Output.Filter = TEXT("Bilinear");
		Output.bSRGB = !bLinearData;
		Output.bNeverStream = true;
		const TSharedPtr<FJsonObject>* UETexture = nullptr;
		if ((*OutputObject)->TryGetObjectField(TEXT("ue_texture"), UETexture) && UETexture && UETexture->IsValid())
		{
			Output.Compression = ReadStringOrDefault(*UETexture, TEXT("compression"), Output.Compression);
			Output.SourceFormat = ReadStringOrDefault(*UETexture, TEXT("source_format"), Output.SourceFormat);
			Output.MipGen = ReadStringOrDefault(*UETexture, TEXT("mip_gen"), Output.MipGen);
			Output.LODGroup = ReadStringOrDefault(*UETexture, TEXT("lod_group"), Output.LODGroup);
			Output.AddressX = ReadStringOrDefault(*UETexture, TEXT("address_x"), Output.AddressX);
			Output.AddressY = ReadStringOrDefault(*UETexture, TEXT("address_y"), Output.AddressY);
			Output.Filter = ReadStringOrDefault(*UETexture, TEXT("filter"), Output.Filter);
			Output.bSRGB = ReadBoolOrDefault(*UETexture, TEXT("srgb"), Output.bSRGB);
			Output.bNeverStream = ReadBoolOrDefault(*UETexture, TEXT("never_stream"), Output.bNeverStream);
		}
		const bool bSingleChannelMask = bMaskType && Output.SourceFormat == TEXT("TSF_G8");
		ReadIntArray2(*OutputObject, TEXT("target_size"), Output.TargetWidth, Output.TargetHeight);
		double SourceMipCount = 0;
		(*OutputObject)->TryGetNumberField(TEXT("source_mip_count"), SourceMipCount);
		Output.ExpectedSourceMipCount = static_cast<int32>(SourceMipCount);
		if (SourceMipCount < 0 || SourceMipCount > 32 || SourceMipCount != Output.ExpectedSourceMipCount)
		{
			OutError = TEXT("Invalid source_mip_count");
			return false;
		}

		if (Output.ComponentId.IsEmpty() || Output.RuntimeFile.IsEmpty() || Output.PackagePath.IsEmpty() || Output.AssetName.IsEmpty())
		{
			OutError = TEXT("each manifest output needs component_id, runtime_file, ue_package_path, and ue_asset_name");
			return false;
		}
		if (!Output.PackagePath.StartsWith(TEXT("/Game/")))
		{
			OutError = FString::Printf(TEXT("ue_package_path must start with /Game/: %s"), *Output.PackagePath);
			return false;
		}
		if (!UMCTAssetImportBuilder::ParseTextureSettings(Output.Compression, Output.MipGen, Output.LODGroup,
			Output.ParsedCompression, Output.ParsedMipGen, Output.ParsedGroup, OutError))
		{
			return false;
		}
		FString FilterName = Output.Filter;
		if (!FilterName.StartsWith(TEXT("TF_"))) { FilterName = TEXT("TF_") + FilterName; }
		const int64 FilterValue = StaticEnum<TextureFilter>()->GetValueByNameString(FilterName);
		if (FilterValue == INDEX_NONE || FilterName.EndsWith(TEXT("_MAX")))
		{
			OutError = FString::Printf(TEXT("Unsupported texture filter: %s"), *Output.Filter);
			return false;
		}
		Output.ParsedFilter = static_cast<TextureFilter>(FilterValue);
		if ((Output.ParsedCompression != TC_EditorIcon && Output.ParsedCompression != TC_Grayscale && Output.ParsedCompression != TC_Masks && Output.ParsedCompression != TC_Default)
			|| (Output.ParsedMipGen != TMGS_NoMipmaps && Output.ParsedMipGen != TMGS_LeaveExistingMips)
			|| (Output.ParsedGroup != TEXTUREGROUP_UI && Output.ParsedGroup != TEXTUREGROUP_Project01)
			|| Output.AddressX != TEXT("Clamp")
			|| Output.AddressY != TEXT("Clamp"))
		{
			OutError = FString::Printf(TEXT("unsupported or unsafe ue_texture settings for component: %s"), *Output.ComponentId);
			return false;
		}
		if (bSingleChannelMask && (Output.ParsedCompression != TC_Grayscale || Output.SourceFormat != TEXT("TSF_G8") || Output.bSRGB))
		{
			OutError = FString::Printf(TEXT("single-channel mask ue_texture contract failed for component: %s"), *Output.ComponentId);
			return false;
		}

		const FString RuntimePath = ToFullProjectPath(Output.RuntimeFile);
		if (!IsUnderProject(RuntimePath) || !IFileManager::Get().FileExists(*RuntimePath))
		{
			OutError = FString::Printf(TEXT("runtime_file does not exist under project: %s"), *Output.RuntimeFile);
			return false;
		}

		Manifest.Outputs.Add(MoveTemp(Output));
	}

	OutManifest = MoveTemp(Manifest);
	return true;
}

bool SaveTexture(UTexture2D* Texture)
{
	if (!Texture)
	{
		return false;
	}
	UPackage* Package = Texture->GetOutermost();
	if (!Package)
	{
		return false;
	}
	const FString LongPackageName = Package->GetName();
	const FString Filename = FPackageName::LongPackageNameToFilename(LongPackageName, FPackageName::GetAssetPackageExtension());
	FSavePackageArgs SaveArgs;
	SaveArgs.TopLevelFlags = RF_Public | RF_Standalone;
	return UPackage::SavePackage(Package, Texture, *Filename, SaveArgs);
}

TSharedPtr<FJsonObject> TextureInfoJson(const FManifestOutput& Output)
{
	TSharedPtr<FJsonObject> Item = MakeShared<FJsonObject>();
	Item->SetStringField(TEXT("component_id"), Output.ComponentId);
	Item->SetStringField(TEXT("asset_path"), Output.AssetPath);
	Item->SetStringField(TEXT("expected_runtime_file"), Output.RuntimeFile);
	Item->SetNumberField(TEXT("expected_width"), Output.TargetWidth);
	Item->SetNumberField(TEXT("expected_height"), Output.TargetHeight);

	UTexture2D* Texture = LoadObject<UTexture2D>(nullptr, *ToObjectPath(Output.AssetPath));
	Item->SetBoolField(TEXT("exists"), Texture != nullptr);
	if (!Texture)
	{
		return Item;
	}

	// Manifest dimensions describe the imported source contract. Newly imported
	// textures can legitimately have no editor platform resource yet (notably
	// single-channel G8 masks), which makes GetSizeX/Y return zero even though
	// the source mip is complete and will be built for the target during cook.
	// Keep runtime/platform dimensions as telemetry, but fail closed against the
	// durable source dimensions.
	const int32 SourceWidth = Texture->Source.IsValid() ? Texture->Source.GetSizeX() : Texture->GetSizeX();
	const int32 SourceHeight = Texture->Source.IsValid() ? Texture->Source.GetSizeY() : Texture->GetSizeY();
	Item->SetNumberField(TEXT("width"), SourceWidth);
	Item->SetNumberField(TEXT("height"), SourceHeight);
	Item->SetNumberField(TEXT("runtime_width"), Texture->GetSizeX());
	Item->SetNumberField(TEXT("runtime_height"), Texture->GetSizeY());
	Item->SetBoolField(TEXT("runtime_resource_ready"), Texture->GetSizeX() > 0 && Texture->GetSizeY() > 0);
	Item->SetBoolField(TEXT("size_matches"), (Output.TargetWidth <= 0 || Output.TargetWidth == SourceWidth)
		&& (Output.TargetHeight <= 0 || Output.TargetHeight == SourceHeight));
	Item->SetBoolField(TEXT("srgb"), Texture->SRGB);
	Item->SetNumberField(TEXT("compression_settings"), static_cast<int32>(Texture->CompressionSettings));
	Item->SetNumberField(TEXT("mip_gen_settings"), static_cast<int32>(Texture->MipGenSettings));
	Item->SetNumberField(TEXT("lod_group"), static_cast<int32>(Texture->LODGroup));
	Item->SetBoolField(TEXT("ui_compression"), Texture->CompressionSettings == TC_EditorIcon);
	Item->SetBoolField(TEXT("no_mipmaps"), Texture->MipGenSettings == TMGS_NoMipmaps);
	Item->SetBoolField(TEXT("ui_lod_group"), Texture->LODGroup == TEXTUREGROUP_UI);
	const FString SourceFormat = Texture->Source.IsValid() ? UEnum::GetValueAsString(Texture->Source.GetFormat()) : TEXT("invalid");
	const bool bSourceFormatMatches = Output.SourceFormat == TEXT("auto") || SourceFormat.EndsWith(Output.SourceFormat);
	const bool bSettingsMatch =
		Texture->CompressionSettings == Output.ParsedCompression
		&& Texture->SRGB == Output.bSRGB
		&& Texture->MipGenSettings == Output.ParsedMipGen
		&& Texture->LODGroup == Output.ParsedGroup
		&& Texture->AddressX == TA_Clamp
		&& Texture->AddressY == TA_Clamp
		&& Texture->Filter == Output.ParsedFilter
		&& Texture->NeverStream == Output.bNeverStream
		&& bSourceFormatMatches;
	Item->SetStringField(TEXT("source_format"), SourceFormat);
	Item->SetStringField(TEXT("expected_source_format"), Output.SourceFormat);
	Item->SetBoolField(TEXT("source_format_matches"), bSourceFormatMatches);
	Item->SetStringField(TEXT("address_x"), UEnum::GetValueAsString(Texture->AddressX));
	Item->SetStringField(TEXT("address_y"), UEnum::GetValueAsString(Texture->AddressY));
	Item->SetStringField(TEXT("filter"), UEnum::GetValueAsString(Texture->Filter));
	Item->SetBoolField(TEXT("never_stream"), Texture->NeverStream);
	Item->SetBoolField(TEXT("settings_match"), bSettingsMatch);
	Item->SetNumberField(TEXT("source_mip_count"), Texture->Source.GetNumMips());
	Item->SetNumberField(TEXT("runtime_mip_count"), Texture->GetNumMips());
	Item->SetBoolField(TEXT("source_mips_match"), Output.ExpectedSourceMipCount == 0 || Texture->Source.GetNumMips() == Output.ExpectedSourceMipCount);
	TArray<TSharedPtr<FJsonValue>> SourceMips;
	for (int32 Index = 0; Index < Texture->Source.GetNumMips(); ++Index)
	{
		TSharedPtr<FJsonObject> Mip = MakeShared<FJsonObject>();
		Mip->SetNumberField(TEXT("index"), Index);
		Mip->SetNumberField(TEXT("width"), FMath::Max(1, Texture->Source.GetSizeX() >> Index));
		Mip->SetNumberField(TEXT("height"), FMath::Max(1, Texture->Source.GetSizeY() >> Index));
		SourceMips.Add(MakeShared<FJsonValueObject>(Mip));
	}
	Item->SetArrayField(TEXT("source_mips"), SourceMips);
	Item->SetStringField(TEXT("requested_mip_gen"), Output.MipGen);
	Item->SetStringField(TEXT("requested_lod_group"), Output.LODGroup);
	Item->SetStringField(TEXT("requested_filter"), Output.Filter);
	Item->SetStringField(TEXT("pixel_format"), GetPixelFormatString(Texture->GetPixelFormat()));
	return Item;
}

TSharedPtr<FJsonObject> BuildVerifyResult(const FManifestData& Manifest)
{
	TArray<TSharedPtr<FJsonValue>> Assets;
	bool bAllExist = true;
	bool bAllSizesMatch = true;
	bool bAllSettingsMatch = true;
	bool bAllSourceMipsMatch = true;

	for (const FManifestOutput& Output : Manifest.Outputs)
	{
		TSharedPtr<FJsonObject> Item = TextureInfoJson(Output);
		bAllExist = bAllExist && Item->GetBoolField(TEXT("exists"));
		bAllSizesMatch = bAllSizesMatch && (!Item->HasField(TEXT("size_matches")) || Item->GetBoolField(TEXT("size_matches")));
		bAllSettingsMatch = bAllSettingsMatch && (!Item->HasField(TEXT("settings_match")) || Item->GetBoolField(TEXT("settings_match")));
		bAllSourceMipsMatch = bAllSourceMipsMatch && (!Item->HasField(TEXT("source_mips_match")) || Item->GetBoolField(TEXT("source_mips_match")));
		Assets.Add(MakeShared<FJsonValueObject>(Item));
	}

	TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
	Data->SetStringField(TEXT("manifest_path"), Manifest.ManifestPath);
	Data->SetStringField(TEXT("schema"), Manifest.Schema);
	Data->SetStringField(TEXT("version"), Manifest.Version);
	Data->SetNumberField(TEXT("component_count"), Manifest.Outputs.Num());
	Data->SetBoolField(TEXT("all_assets_exist"), bAllExist);
	Data->SetBoolField(TEXT("all_sizes_match"), bAllSizesMatch);
	Data->SetBoolField(TEXT("all_settings_match"), bAllSettingsMatch);
	Data->SetBoolField(TEXT("all_source_mips_match"), bAllSourceMipsMatch);
	Data->SetArrayField(TEXT("assets"), Assets);
	return Data;
}

FString ImportOutput(const FManifestOutput& Output, const bool bForce, TSharedPtr<FJsonObject>& OutResult)
{
	const FString ExistingDiskAsset = DiskAssetPath(Output.PackagePath, Output.AssetName);
	if (IFileManager::Get().FileExists(*ExistingDiskAsset) && !bForce)
	{
		OutResult = TextureInfoJson(Output);
		OutResult->SetStringField(TEXT("status"), TEXT("skipped-existing"));
		if (!OutResult->GetBoolField(TEXT("exists")) || !OutResult->GetBoolField(TEXT("settings_match"))
			|| !OutResult->GetBoolField(TEXT("size_matches")) || !OutResult->GetBoolField(TEXT("source_mips_match")))
		{
			return FString::Printf(TEXT("Existing asset does not match the manifest: %s"), *Output.AssetPath);
		}
		return FString();
	}

	FString Error;
	TSharedPtr<FJsonObject> ImportResult = UMCTAssetImportBuilder::ImportTexture(
		ToFullProjectPath(Output.RuntimeFile),
		Output.PackagePath,
		Output.AssetName,
		Output.Compression,
		Output.MipGen,
		Output.LODGroup,
		Output.bSRGB,
		Error);

	if (!ImportResult.IsValid())
	{
		return Error.IsEmpty() ? FString::Printf(TEXT("Failed to import texture: %s"), *Output.AssetName) : Error;
	}

	UTexture2D* Texture = LoadObject<UTexture2D>(nullptr, *ToObjectPath(Output.AssetPath));
	if (!Texture)
	{
		return FString::Printf(TEXT("Imported texture could not be reloaded: %s"), *Output.AssetPath);
	}
	if (Texture->SRGB != Output.bSRGB)
	{
		return TEXT("Requested sRGB conflicts with the preserved source gamma; use a new versioned texture asset.");
	}
	Texture->PreEditChange(nullptr);
	Texture->CompressionSettings = Output.ParsedCompression;
	Texture->SRGB = Output.bSRGB;
	Texture->MipGenSettings = Output.ParsedMipGen;
	Texture->LODGroup = Output.ParsedGroup;
	Texture->AddressX = TA_Clamp;
	Texture->AddressY = TA_Clamp;
	Texture->Filter = Output.ParsedFilter;
	Texture->NeverStream = Output.bNeverStream;
	Texture->PostEditChange();
	UTexture* PendingTextures[] = { Texture };
	FTextureCompilingManager::Get().FinishCompilation(PendingTextures);
	Texture->BlockOnAnyAsyncBuild();
	Texture->UpdateResource();
	Texture->MarkPackageDirty();
	if (!SaveTexture(Texture))
	{
		return FString::Printf(TEXT("Failed to save texture settings: %s"), *Output.AssetPath);
	}

	OutResult = TextureInfoJson(Output);
	if (!OutResult->GetBoolField(TEXT("settings_match")) || !OutResult->GetBoolField(TEXT("size_matches"))
		|| !OutResult->GetBoolField(TEXT("source_mips_match")))
	{
		return FString::Printf(TEXT("Imported asset failed manifest readback: %s"), *Output.AssetPath);
	}
	OutResult->SetStringField(TEXT("component_id"), Output.ComponentId);
	OutResult->SetStringField(TEXT("status"), TEXT("imported"));
	OutResult->SetStringField(TEXT("texture_type"), Output.TextureType);
	return FString();
}

FString RunOnGameThread(TFunction<FString()>&& Work, const TCHAR* TimeoutError, const double TimeoutSeconds)
{
	if (IsInGameThread())
	{
		return Work();
	}

	TSharedPtr<TPromise<FString>> Promise = MakeShared<TPromise<FString>>();
	TFuture<FString> Future = Promise->GetFuture();

	AsyncTask(ENamedThreads::GameThread, [Promise, Work = MoveTemp(Work)]()
	{
		Promise->SetValue(Work());
	});

	Future.WaitFor(FTimespan::FromSeconds(TimeoutSeconds));
	if (!Future.IsReady())
	{
		return CreateErrorResponse(TimeoutError);
	}
	return Future.Get();
}
}

FString HandleStatus(TSharedPtr<FJsonObject> Params)
{
	TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
	Data->SetStringField(TEXT("plugin"), TEXT("AIAssetPipeline"));
	const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIAssetPipeline"));
	Data->SetStringField(TEXT("version"), Plugin.IsValid() ? Plugin->GetDescriptor().VersionName : TEXT("unknown"));
	Data->SetBoolField(TEXT("authored_mips_supported"), true);
	Data->SetBoolField(TEXT("widget_pixel_footprints_supported"), true);
	Data->SetBoolField(TEXT("widget_geometry_only_supported"), true);
	Data->SetStringField(TEXT("plugin_dir"), FAIAssetPipelineModule::GetPluginDir());
	Data->SetStringField(TEXT("python_dir"), FAIAssetPipelineModule::GetPythonDir());
	Data->SetStringField(TEXT("schemas_dir"), FAIAssetPipelineModule::GetSchemasDir());
	Data->SetBoolField(TEXT("model_generation_supported"), false);
	Data->SetBoolField(TEXT("wbp_mutation_supported"), false);
	Data->SetStringField(TEXT("policy"), TEXT("Ingest source art and manifests; import texture assets only with write scope."));
	return CreateSuccessResponse(Data);
}

FString HandleImportManifest(TSharedPtr<FJsonObject> Params)
{
	if (!Params.IsValid())
	{
		return CreateErrorResponse(TEXT("Missing 'params' object"));
	}

	const FString ManifestPath = ReadStringOrDefault(Params, TEXT("manifest_path"));
	const bool bForce = ReadBoolOrDefault(Params, TEXT("force"), false);

	FManifestData Manifest;
	FString Error;
	if (!LoadManifest(ManifestPath, Manifest, Error))
	{
		return CreateErrorResponse(Error);
	}

	return RunOnGameThread([Manifest, bForce]()
	{
		TArray<TSharedPtr<FJsonValue>> Results;
		for (const FManifestOutput& Output : Manifest.Outputs)
		{
			TSharedPtr<FJsonObject> Result;
			const FString ImportError = ImportOutput(Output, bForce, Result);
			if (!ImportError.IsEmpty())
			{
				return CreateErrorResponse(FString::Printf(TEXT("Import failed for component '%s': %s"), *Output.ComponentId, *ImportError));
			}
			Results.Add(MakeShared<FJsonValueObject>(Result));
		}

		TSharedPtr<FJsonObject> Data = BuildVerifyResult(Manifest);
		Data->SetStringField(TEXT("manifest_path"), Manifest.ManifestPath);
		Data->SetBoolField(TEXT("force"), bForce);
		Data->SetArrayField(TEXT("results"), Results);
		return CreateSuccessResponse(Data);
	}, TEXT("AIAssetPipeline manifest import timed out"), 300.0);
}

FString HandleVerifyAssets(TSharedPtr<FJsonObject> Params)
{
	if (!Params.IsValid())
	{
		return CreateErrorResponse(TEXT("Missing 'params' object"));
	}

	FManifestData Manifest;
	FString Error;
	if (!LoadManifest(ReadStringOrDefault(Params, TEXT("manifest_path")), Manifest, Error))
	{
		return CreateErrorResponse(Error);
	}

	return RunOnGameThread([Manifest]()
	{
		return CreateSuccessResponse(BuildVerifyResult(Manifest));
	}, TEXT("AIAssetPipeline asset verification timed out"), 120.0);
}

FString HandleMeasureWidgets(TSharedPtr<FJsonObject> Params)
{
	FString ClassPath;
	const TArray<TSharedPtr<FJsonValue>>* Bindings = nullptr;
	if (!ReadString(Params, TEXT("widget_class"), ClassPath) || !ClassPath.StartsWith(TEXT("/Game/"))
		|| !Params->TryGetArrayField(TEXT("components"), Bindings) || !Bindings || Bindings->IsEmpty() || Bindings->Num() > 256)
	{
		return CreateErrorResponse(TEXT("Expected explicit /Game widget_class and 1..256 component bindings"));
	}
	return RunOnGameThread([Params, ClassPath]()
	{
		UWorld* World = GEditor ? GEditor->PlayWorld : nullptr;
		if (!World || World->WorldType != EWorldType::PIE)
			return CreateErrorResponse(TEXT("An active, settled PIE viewport is required"));
		UClass* Class = LoadClass<UUserWidget>(nullptr, *ClassPath);
		if (!Class) return CreateErrorResponse(TEXT("Widget class could not be loaded"));
		TArray<UUserWidget*> Owners;
		UWidgetBlueprintLibrary::GetAllWidgetsOfClass(World, Owners, Class, false);
		if (Owners.IsEmpty()) return CreateErrorResponse(TEXT("No live widget instances for the requested class"));
		const FVector2D Viewport = UWidgetLayoutLibrary::GetViewportSize(World);
		if (Viewport.X <= 0 || Viewport.Y <= 0) return CreateErrorResponse(TEXT("PIE viewport is not arranged"));
		auto Pair = [](double X, double Y)
		{
			return TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(X), MakeShared<FJsonValueNumber>(Y)};
		};
		TArray<TSharedPtr<FJsonValue>> Samples;
		int32 VisibleCount = 0;
		for (UUserWidget* Owner : Owners)
		{
			for (const TSharedPtr<FJsonValue>& Value : Params->GetArrayField(TEXT("components")))
			{
				const TSharedPtr<FJsonObject>* BindingPtr = nullptr;
				if (!Value->TryGetObject(BindingPtr) || !BindingPtr) return CreateErrorResponse(TEXT("Invalid component binding"));
				const TSharedPtr<FJsonObject> Binding = *BindingPtr;
				FString Component, Sampling;
				const TArray<TSharedPtr<FJsonValue>>* Names = nullptr;
				if (!ReadString(Binding, TEXT("component"), Component) || !ReadString(Binding, TEXT("sampling"), Sampling)
					|| (Sampling != TEXT("full_uv") && Sampling != TEXT("geometry_only")) || !Binding->TryGetArrayField(TEXT("widgets"), Names)
					|| !Names || Names->IsEmpty() || Names->Num() > 64)
					return CreateErrorResponse(TEXT("Component requires full_uv or geometry_only and explicit widget names"));
				for (const TSharedPtr<FJsonValue>& NameValue : *Names)
				{
					FString Name;
					if (!NameValue->TryGetString(Name)) return CreateErrorResponse(TEXT("Widget name must be a string"));
					UWidget* Widget = Owner->GetWidgetFromName(FName(*Name));
					if (!Widget) return CreateErrorResponse(TEXT("Widget not found: ") + Name);
					const UImage* Image = Cast<UImage>(Widget);
					if (Sampling == TEXT("full_uv") && (!Image || Image->GetBrush().DrawAs != ESlateBrushDrawType::Image
						|| Image->GetBrush().Tiling != ESlateBrushTileType::NoTile
						|| FBox2f(Image->GetBrush().GetUVRegion()).bIsValid))
						return CreateErrorResponse(TEXT("Widget is not an untiled full-UV Image; bind its sampling regions separately: ") + Name);
					bool bVisible = true, bIntermediate = false;
					UWidget* Cursor = Widget;
					int32 Depth = 0;
					for (; Cursor && Depth < 100; ++Depth)
					{
						bVisible &= Cursor->IsVisible() && Cursor->GetRenderOpacity() > 0;
						bIntermediate |= Cast<URetainerBox>(Cursor) != nullptr;
						Cursor = Cursor->GetParent() ? static_cast<UWidget*>(Cursor->GetParent()) : Cursor->GetTypedOuter<UUserWidget>();
					}
					if (Cursor) return CreateErrorResponse(TEXT("Unresolved widget ancestor chain"));
					// Keep FGeometry entirely in native code; do not round-trip it through Python reflection.
					const FGeometry& Geometry = Widget->GetCachedGeometry();
					const FVector2D Local = Geometry.GetLocalSize();
					FVector2D Origin, XEnd, YEnd, Unused;
					USlateBlueprintLibrary::LocalToViewport(World, Geometry, FVector2D::ZeroVector, Origin, Unused);
					USlateBlueprintLibrary::LocalToViewport(World, Geometry, FVector2D(Local.X, 0), XEnd, Unused);
					USlateBlueprintLibrary::LocalToViewport(World, Geometry, FVector2D(0, Local.Y), YEnd, Unused);
					const FVector2D Pixels((XEnd - Origin).Size(), (YEnd - Origin).Size());
					const bool bPainted = Pixels.X > 0 && Pixels.Y > 0 && FMath::IsFinite(Pixels.X) && FMath::IsFinite(Pixels.Y);
					bVisible &= bPainted;
					VisibleCount += bVisible ? 1 : 0;
					TSharedPtr<FJsonObject> Row = MakeShared<FJsonObject>();
					Row->SetStringField(TEXT("component"), Component);
					Row->SetStringField(TEXT("widget"), Widget->GetPathName());
					Row->SetStringField(TEXT("owner"), Owner->GetPathName());
					Row->SetStringField(TEXT("sampling"), Sampling);
					Row->SetBoolField(TEXT("visible"), bVisible);
					Row->SetBoolField(TEXT("painted"), bPainted);
					Row->SetBoolField(TEXT("intermediate_render_target"), bIntermediate);
					Row->SetArrayField(TEXT("pixel_size"), Pair(Pixels.X, Pixels.Y));
					Row->SetArrayField(TEXT("local_size"), Pair(Local.X, Local.Y));
					Row->SetArrayField(TEXT("pixel_origin"), Pair(Origin.X, Origin.Y));
					Samples.Add(MakeShared<FJsonValueObject>(Row));
				}
			}
		}
		if (VisibleCount == 0) return CreateErrorResponse(TEXT("No visible painted samples; settle the intended PIE screen"));
		TSharedPtr<FJsonObject> Data = MakeShared<FJsonObject>();
		Data->SetBoolField(TEXT("ok"), true);
		Data->SetStringField(TEXT("method"), TEXT("ue_local_to_viewport"));
		Data->SetStringField(TEXT("project_file"), FPaths::GetCleanFilename(FPaths::GetProjectFilePath()));
		Data->SetArrayField(TEXT("viewport_pixels"), Pair(Viewport.X, Viewport.Y));
		Data->SetArrayField(TEXT("samples"), Samples);
		return CreateSuccessResponse(Data);
	}, TEXT("AIAssetPipeline widget measurement timed out"), 30.0);
}
}
