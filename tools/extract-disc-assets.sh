#!/usr/bin/env bash
# extract-disc-assets.sh
#
# Extracts game assets from a raw Homeworld CD image (BIN/CUE, 2352-byte
# sectors) into an output folder:
#
#   1. Converts the raw image to a plain 2048-byte ISO.
#   2. Extracts the ISO contents (7-Zip).
#   3. Extracts Homeworld.big from HWSETUP.EXE (Wise installer) using REWise.
#   4. Optionally extracts Update.big from the official 1.05 patch exe.
#   5. Verifies results against the sha256 checklist in
#      documentation/community/installation-manual.md.
#
# Usage:
#   tools/extract-disc-assets.sh <disc.bin> [-o <output-dir>] [--patch <patch.exe>] [--keep-iso] [-y]
#
# Output layout (<output-dir>, default ./output):
#   iso/          the converted ISO image (kept only with --keep-iso unless extraction fails)
#   disc/         files as they sit on the disc CD root
#   installer/    assets pulled out of HWSETUP.EXE (Homeworld.big)
#   patch/        assets pulled out of the 1.05 patch exe (Update.big)
#
# Dependencies: python3, curl (only when REWise must be downloaded), cmake+build
# toolchain (only when REWise must be built), unzip (only with --patch), 7-Zip
# (7z / 7zz; downloaded automatically if missing).
set -euo pipefail

readonly REWISE_URL="https://codeberg.org/CYBERDEV/REWise.git"

SHA_BIG_USA="af9dcc06e3f99404334a0a8ec17e399876080e85feb5b47858382dc715136040"
SHA_UPDATE_USA="c71b07758ee7696b3a19c1d75c946cbd68830e03b30cd3c2888f2f6d22b7c976"
SHA_VCE_USA="15c4b988adb09b0969b0dc288b21ddc10ca9d42a2064d15b46b53dcf02fc2e44"
SHA_WXD_USA="b909c2cdbc8c8db67be168f6005bf8e8facaa9857263b16d186c824a0c4eed4f"

script_dir="$(cd "$(dirname "$0")" && pwd)"
workdir="$(mktemp -d "${TMPDIR:-/tmp}/hw-extract.XXXXXX")"
trap 'rm -rf "$workdir"' EXIT

die() { echo "extract-disc-assets: $*" >&2; exit 1; }
info() { echo "==> $*" >&2; }

need() { command -v "$1" >/dev/null 2>&1 || die "required tool '$1' not found on PATH"; }

ensure_7zip() {
    if command -v 7z >/dev/null 2>&1; then echo 7z; return; fi
    if command -v 7zz >/dev/null 2>&1; then echo 7zz; return; fi
    if command -v p7zip >/dev/null 2>&1; then echo p7zip; return; fi
    info "7-Zip not found; downloading a portable build"
    local url="https://www.7-zip.org/a/7z2501-linux-x64.tar.xz"
    mkdir -p "$workdir/7zip"
    curl -fsSL "$url" | tar -xJ -C "$workdir/7zip"
    echo "$workdir/7zip/7zz"
}

# REWise: extracts Homeworld.big out of the HWSETUP.EXE Wise installer.
ensure_rewise() {
    if [[ -x "$script_dir/REWise/build/rewise" ]]; then
        echo "$script_dir/REWise/build/rewise"; return
    fi
    info "REWise not found at tools/REWise; cloning and building"
    need cmake; need make; need curl
    git clone --depth=1 "$REWISE_URL" "$workdir/REWise-source"
    cmake -S "$workdir/REWise-source" -B "$workdir/REWise-build" >/dev/null
    cmake --build "$workdir/REWise-build" >/dev/null
    mkdir -p "$script_dir/REWise/build"
    cp "$workdir/REWise-build/rewise" "$script_dir/REWise/build/rewise"
    cp "$workdir/REWise-source"/{COPYING,README.md,CHANGELOG.md} "$script_dir/REWise/" 2>/dev/null || true
    echo "$script_dir/REWise/build/rewise"
}

# Strip raw 2352-byte CD sectors down to 2048-byte user data.
# Handles Mode 1 (user data at sector offset 16) and Mode 2 XA Form 1
# (subheader at 16..23, user data at 24).
raw_to_iso() {
    local src="$1" dst="$2"
    python3 - "$src" "$dst" <<'PY'
import sys
src, dst = sys.argv[1], sys.argv[2]
RAW, USER_MODE1, USER_MODE2 = 2352, 2048, 2048
with open(src, 'rb') as f, open(dst, 'wb') as out:
    sect = f.read(RAW)
    if len(sect) < RAW:
        # Not a raw image; maybe already a 2048 ISO.
        f.seek(0)
        while True:
            b = f.read(1 << 20)
            if not b: break
            out.write(b)
        sys.exit(0)
    mode = sect[15]
    off = 16 if mode == 1 else 24  # Mode 2 XA Form 1
    f.seek(0)
    while True:
        chunk = f.read(RAW)
        if not chunk: break
        m = chunk[15]
        o = 16 if m == 1 else 24
        if len(chunk) >= o + 2048:
            out.write(chunk[o:o + 2048])
        else:
            out.write(chunk[o:])
PY
}

find_first() {
    local root="$1" name="$2"
    find "$root" -type f -iname "$name" | head -n1
}

check() {
    local file="$1" want="$2" label="$3"
    if [[ ! -f "$file" ]]; then echo "  MISSING  $label"; return 1; fi
    local got; got="$(sha256sum "$file" | cut -d' ' -f1)"
    if [[ "$got" == "$want" ]]; then
        echo "  OK       $label"
        return 0
    fi
    echo "  MISMATCH $label"
    echo "           expected $want"
    echo "           actual   $got"
    return 1
}

# ---- parse args ----------------------------------------------------------
disc=""
outdir="output"
patch_exe=""
keep_iso=0
assume_yes=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o|--output) outdir="$2"; shift 2 ;;
        -o=*) outdir="${1#*=}"; shift ;;
        --patch) patch_exe="$2"; shift 2 ;;
        --keep-iso) keep_iso=1; shift ;;
        -y|--yes) assume_yes=1; shift ;;
        -h|--help) sed -n '2,25p' "$0"; exit 0 ;;
        -*) die "unknown option: $1" ;;
        *) disc="$1"; shift ;;
    esac
done

[[ -n "$disc" ]] || { sed -n '2,25p' "$0"; exit 1; }
[[ -f "$disc" ]] || die "disc image not found: $disc"

mkdir -p "$outdir"

# ---- 1. raw BIN -> ISO ---------------------------------------------------
iso="$outdir/iso/${disc%.*}.iso"
mkdir -p "$outdir/iso"
info "converting raw image to ISO"
if [[ -f "$iso" && "${SKIP_ISOConv:-}" != "1" ]]; then
    info "  reusing existing $iso (delete it to force rebuild)"
else
    raw_to_iso "$disc" "$iso"
fi

sz7="$(ensure_7zip)"

# ---- 2. extract ISO contents --------------------------------------------
if [[ -d "$outdir/disc" && -n "$(ls -A "$outdir/disc" 2>/dev/null)" && $assume_yes -eq 0 ]]; then
    read -r -p "==> '$outdir/disc' is not empty. Overwrite? [y/N] " reply
    [[ "${reply,,}" == "y" ]] || die "aborted"
fi
rm -rf "$outdir/disc"; mkdir -p "$outdir/disc"
info "extracting disc contents to $outdir/disc"
"$sz7" x -y -o"$outdir/disc" "$iso" >/dev/null

# ---- 3. HWSETUP.EXE -> Homeworld.big -------------------------------------
rew="$(ensure_rewise)"
setup="$(find_first "$outdir/disc" "HWSETUP.EXE")"
if [[ -n "$setup" ]]; then
    info "extracting Homeworld.big from $setup"
    mkdir -p "$outdir/installer"
    "$rew" -s -x "$outdir/installer" "$setup"
    big="$(find "$outdir/installer" -type f -iname 'Homeworld.big' | head -n1)"
    [[ -n "$big" ]] || die "REWise ran but no Homeworld.big appeared"
    [[ "$big" == "$outdir/installer/Homeworld.big" ]] || mv "$big" "$outdir/installer/Homeworld.big"
else
    info "HWSETUP.EXE not found on disc; expecting Homeworld.big directly on disc"
    # some releases carry Homeworld.big on the disc itself; move it into installer/
    big="$(find_first "$outdir/disc" "Homeworld.big")"
    [[ -n "$big" ]] && { mkdir -p "$outdir/installer"; mv "$big" "$outdir/installer/Homeworld.big"; }
fi

# ---- 4. optional 1.05 patch -> Update.big --------------------------------
if [[ -n "$patch_exe" ]]; then
    need unzip
    [[ -f "$patch_exe" ]] || die "patch exe not found: $patch_exe"
    info "extracting Update.big from $patch_exe"
    mkdir -p "$outdir/patch"
    unzip -o -q "$patch_exe" -d "$workdir/patch-stage" '*Update.big' 'Update.big' 2>/dev/null \
        || unzip -o -q "$patch_exe" -d "$workdir/patch-stage"
    upd="$(find "$workdir/patch-stage" -type f -iname 'Update.big' | head -n1)"
    [[ -n "$upd" ]] || die "no Update.big inside patch exe"
    mv "$upd" "$outdir/patch/Update.big"
fi

# ---- 5. verify -----------------------------------------------------------
info "verification"
rc=0
check "$outdir/installer/Homeworld.big" "$SHA_BIG_USA" "Homeworld.big" || rc=1
[[ -f "$outdir/patch/Update.big" ]] && check "$outdir/patch/Update.big" "$SHA_UPDATE_USA" "Update.big" || true
vce="$(find_first "$outdir/disc" "HW_Comp.vce")"
wxd="$(find_first "$outdir/disc" "HW_Music.wxd")"
[[ -n "$vce" ]] && check "$vce" "$SHA_VCE_USA" "HW_Comp.vce" || rc=1
[[ -n "$wxd" ]] && check "$wxd" "$SHA_WXD_USA" "HW_Music.wxd" || rc=1

if [[ $keep_iso -eq 0 && $rc -eq 0 ]]; then
    info "cleaning up $outdir/iso"
    rm -rf "$outdir/iso"
fi

[[ $rc -eq 0 ]] && info "extraction complete: $outdir" || { echo "extraction finished with warnings (see above)" >&2; exit $rc; }