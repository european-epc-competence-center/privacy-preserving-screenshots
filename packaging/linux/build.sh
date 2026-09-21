#!/usr/bin/env bash
# Builds dist/eecc-redact-<version>-x86_64.AppImage and its .sha256.
# Needs uv; downloads appimagetool on first use. The build machine's glibc is the
# oldest the result runs on, so build on the oldest distribution you support.
set -euo pipefail
cd "$(dirname "$0")/../.."

version=$(uv run python -c "import eecc_redact; print(eecc_redact.__version__)")
app_id=$(uv run python -c "import eecc_redact; print(eecc_redact.APP_ID)")
echo "Building eecc-redact $version"

QT_QPA_PLATFORM=offscreen uv run python packaging/icon.py "build/$app_id.png"
uv run --group build pyinstaller --noconfirm --clean \
    --distpath dist --workpath build packaging/eecc-redact.spec

# The AppDir: the frozen program, and the entry and icon the AppImage tools expect
# at the root. AppRun is the program itself; the loader resolves the symlinks.
appdir=build/AppDir
rm -rf "$appdir"
mkdir -p "$appdir/usr/bin" "$appdir/usr/lib" "$appdir/usr/share/applications" \
    "$appdir/usr/share/icons/hicolor/256x256/apps"
cp -r dist/eecc-redact "$appdir/usr/lib/eecc-redact"
ln -s ../lib/eecc-redact/eecc-redact "$appdir/usr/bin/eecc-redact"
ln -s usr/bin/eecc-redact "$appdir/AppRun"
cp "build/$app_id.png" "$appdir/usr/share/icons/hicolor/256x256/apps/"
ln -s "usr/share/icons/hicolor/256x256/apps/$app_id.png" "$appdir/$app_id.png"
ln -s "$app_id.png" "$appdir/.DirIcon"
uv run python -c "from eecc_redact.desktop.linux import desktop_entry; print(desktop_entry('eecc-redact'), end='')" \
    > "$appdir/usr/share/applications/$app_id.desktop"
ln -s "usr/share/applications/$app_id.desktop" "$appdir/$app_id.desktop"

tool=build/appimagetool
if [ ! -x "$tool" ]; then
    curl -fsSL -o "$tool" \
        https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage
    chmod +x "$tool"
fi
name="eecc-redact-$version-x86_64.AppImage"
ARCH=x86_64 "$tool" --appimage-extract-and-run --no-appstream "$appdir" "dist/$name"
(cd dist && sha256sum "$name" > "$name.sha256")
echo "Built dist/$name"
