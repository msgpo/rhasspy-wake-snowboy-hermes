#!/usr/bin/env bash
set -e

architecture="$1"
version="$2"

if [[ -z "${version}" ]];
then
    echo "Usage: build-pyinstaller.sh architecture version"
    exit 1
fi

# Directory of *this* script
this_dir="$( cd "$( dirname "$0" )" && pwd )"
src_dir="$(realpath "${this_dir}/..")"

package_name="$(basename "${src_dir}")"
python_name="$(basename "${src_dir}" | sed -e 's/-//' | sed -e 's/-/_/g')"

venv="${src_dir}/.venv"
if [[ -d "${venv}" ]]; then
    echo "Using virtual environment at ${venv}"
    source "${venv}/bin/activate"
fi

# -----------------------------------------------------------------------------

dist="${src_dir}/dist"
mkdir -p dist

# Create PyInstaller artifacts
pyinstaller \
    -y \
    --workpath "${src_dir}/pyinstaller/build" \
    --distpath "${src_dir}/pyinstaller/dist" \
    "${python_name}.spec"

# Extract snowboy resources
pyinstaller="${src_dir}/pyinstaller"
download="${src_dir}/download"
mkdir -p "${pyinstaller}/dist/${python_name}/snowboy"
tar -C "${pyinstaller}/dist/${python_name}/snowboy" \
    -xvf "${download}/snowboy-1.3.0.tar.gz" \
    --strip-components 1 snowboy-1.3.0/resources/common.res

# Tar up binary distribution
tar -C "${src_dir}/pyinstaller/dist" \
    -czf \
    "${dist}/${package_name}_${version}_${architecture}.tar.gz" \
    "${python_name}/"

# -----------------------------------------------------------------------------

echo "OK"
