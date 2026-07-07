from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MACOS_APP_ROOT = PROJECT_ROOT / "macos_app"


def test_packaging_scaffold_files_exist() -> None:
    assert (MACOS_APP_ROOT / "Lulu.xcodeproj" / "project.pbxproj").is_file()
    assert (MACOS_APP_ROOT / "Info.plist").is_file()
    assert (MACOS_APP_ROOT / "Lulu.entitlements").is_file()
    assert (MACOS_APP_ROOT / "Packaging" / "assemble_backend_bundle.sh").is_file()
    assert (MACOS_APP_ROOT / "Packaging" / "package_macos_app.sh").is_file()
    assert (MACOS_APP_ROOT / "Packaging" / "notarize_macos_app.sh").is_file()


def test_info_plist_declares_microphone_usage() -> None:
    info_plist = (MACOS_APP_ROOT / "Info.plist").read_text(encoding="utf-8")

    assert "NSMicrophoneUsageDescription" in info_plist
    assert "wake detection" in info_plist


def test_xcode_project_reuses_existing_swift_sources() -> None:
    project_text = (
        MACOS_APP_ROOT / "Lulu.xcodeproj" / "project.pbxproj"
    ).read_text(encoding="utf-8")

    assert "Sources/LuluApp" in project_text
    assert "com.apple.product-type.application" in project_text
    assert "INFOPLIST_FILE = Info.plist;" in project_text
    assert "CODE_SIGN_ENTITLEMENTS = Lulu.entitlements;" in project_text


def test_package_script_assembles_bundled_backend_runtime() -> None:
    script_text = (MACOS_APP_ROOT / "Packaging" / "package_macos_app.sh").read_text(
        encoding="utf-8"
    )

    assert 'assemble_backend_bundle.sh' in script_text
    assert "LULU_SKIP_SIGNING" in script_text
    assert 'xcodebuild' in script_text
    assert 'hdiutil create' in script_text
