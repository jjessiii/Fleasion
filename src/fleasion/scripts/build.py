"""Build the standalone Fleasion application."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, version as distribution_version
from importlib.util import find_spec
from pathlib import Path

from fleasion.version import build_artifact_version, macos_bundle_version, read_project_version

from ._logger import setup_script_logging
from ._nuitka import run_nuitka

log = logging.getLogger(__name__)


MACOS_SLICE_BUILD_ENV = 'FLEASION_MACOS_SLICE_BUILD'
REPRODUCIBLE_ENV = {
    'PYTHONHASHSEED': '0',
    'SOURCE_DATE_EPOCH': '0',
    'LC_ALL': 'C.UTF-8',
    'TZ': 'UTC',
}

NUITKA_OUTPUT_DIR = Path('build/nuitka')
DIST_DIR = Path('dist')
MACOS_APP_NAME = 'Fleasion'
MACOS_HELPER_NAME = 'fleasion-proxy-helper'
LINUX_HELPER_NAME = 'fleasion-linux-proxy-helper'
_SLICE_ARCHITECTURES = frozenset({'arm64', 'x86_64'})

# Data files shipped at the root of the frozen payload
_SHARED_DATA_FILES = {
    'src/fleasion/fleasionlogoHR.ico': 'fleasionlogoHR.ico',
    'src/fleasion/fleasionlogoHR.icns': 'fleasionlogoHR.icns',
    'src/fleasion/macos_proxy_helper_daemon.py': 'macos_proxy_helper_daemon.py',
    'src/fleasion/modifications/bundled/empty.mp3': 'fleasion/modifications/bundled/empty.mp3',
    'src/fleasion/modifications/bundled/empty.ogg': 'fleasion/modifications/bundled/empty.ogg',
    'src/fleasion/modifications/bundled/empty.mesh': 'fleasion/modifications/bundled/empty.mesh',
    'src/fleasion/modifications/bundled/empty.tex': 'fleasion/modifications/bundled/empty.tex',
}

# Packages that feature code imports dynamically and that must ship whole
_FORCED_PACKAGES = (
    'numpy',
    'lz4',
    'OpenGL.arrays',
)

# Audio data packages that only some platform wheels ship
_OPTIONAL_PACKAGES = (
    '_sounddevice_data',
    '_soundfile_data',
)

# Modules whose static imports are not all reachable from the launcher
_DYNAMIC_MODULES = (
    'DracoPy',
    'certifi',
    'orjson',
    'zstandard',
    'PyQt6.QtCore',
    'PyQt6.QtGui',
    'PyQt6.QtNetwork',
    'PyQt6.QtOpenGL',
    'PyQt6.QtWidgets',
    'OpenGL.platform.glx',
    'OpenGL.platform.egl',
)

# PySide6, the removed mitmproxy stack, and test tooling must never ship
_BASE_EXCLUDES = (
    'PySide6',
    'PyQt5',
    'wsproto',
    'h2',
    'hyperframe',
    'pytest',
    '_pytest',
    'setuptools'
)

_NUMPY_EXCLUDES = (
    'numpy._pyinstaller',
    'numpy.conftest',
    'numpy.f2py',
    'numpy.typing.tests',
)

# Keep Qt collection narrow; the app never uses these modules
_QT_EXCLUDES = (
    'PyQt6.QAxContainer',
    'PyQt6.Qsci',
    'PyQt6.Qt3DAnimation',
    'PyQt6.Qt3DCore',
    'PyQt6.Qt3DExtras',
    'PyQt6.Qt3DInput',
    'PyQt6.Qt3DLogic',
    'PyQt6.Qt3DRender',
    'PyQt6.QtBluetooth',
    'PyQt6.QtCharts',
    'PyQt6.QtDataVisualization',
    'PyQt6.QtDesigner',
    'PyQt6.QtGraphs',
    'PyQt6.QtGraphsWidgets',
    'PyQt6.QtHelp',
    'PyQt6.QtMultimedia',
    'PyQt6.QtMultimediaWidgets',
    'PyQt6.QtNetworkAuth',
    'PyQt6.QtNfc',
    'PyQt6.QtPdf',
    'PyQt6.QtPdfWidgets',
    'PyQt6.QtPositioning',
    'PyQt6.QtPrintSupport',
    'PyQt6.QtQml',
    'PyQt6.QtQuick',
    'PyQt6.QtQuick3D',
    'PyQt6.QtQuickWidgets',
    'PyQt6.QtRemoteObjects',
    'PyQt6.QtSensors',
    'PyQt6.QtSerialPort',
    'PyQt6.QtSpatialAudio',
    'PyQt6.QtSql',
    'PyQt6.QtStateMachine',
    'PyQt6.QtSvg',
    'PyQt6.QtSvgWidgets',
    'PyQt6.QtTest',
    'PyQt6.QtTextToSpeech',
    'PyQt6.QtWebChannel',
    'PyQt6.QtWebEngineCore',
    'PyQt6.QtWebEngineQuick',
    'PyQt6.QtWebEngineWidgets',
    'PyQt6.QtWebSockets',
    'PyQt6.QtXml',
    'PyQt6.uic',
)

# Qt runtime libraries and translations the application never loads
_UNUSED_QT_RUNTIME_DLLS = (
    '*libqpdf.dylib',
    '*libqpdf.so',
    '*libqtiff.dylib',
    '*libqtiff.so',
    '*libQt6Pdf.so.6',
    '*qpdf.dll',
    '*qtiff.dll',
    '*Qt6Pdf.dll',
)
_QT_TRANSLATIONS_DATA = ('*translations/*', '*translations\\*')

# win32 extensions needed for .ROBLOSECURITY cookie decryption
_WINDOWS_MODULES = (
    'win32crypt',
    'win32api',
    'win32con',
    'win32security',
    'pywintypes',
)

# The Linux player uses host audio backends instead of bundled copies
_LINUX_HOST_AUDIO_DLLS = (
    'libportaudio.so*',
    'libasound.so*',
    'libjack.so*',
    'libpulse.so*',
    'libpulsecommon-*',
    'libpipewire-*',
)


class _BuildArgumentParser(argparse.ArgumentParser):
    """Argument parser for the standalone build command."""

    def __init__(self) -> None:
        super().__init__(description='Build the standalone Fleasion application.')
        self.add_argument(
            '--clean',
            action='store_true',
            help='discard Nuitka caches and temporary build files',
        )


def _executable_name() -> str:
    """Returns versioned binary name"""
    executable_name = f'Fleasion-v{build_artifact_version(read_project_version())}'
    if sys.platform == 'win32':
        return f'{executable_name}-Windows.exe'
    if sys.platform.startswith('linux'):
        return f'{executable_name}-Linux'
    return executable_name


def _macos_helper_name() -> str:
    """Returns MacOS helper binary name with arch"""
    target_architecture = os.environ.get('MACOS_TARGET_ARCH')
    if target_architecture in _SLICE_ARCHITECTURES:
        return f'{MACOS_HELPER_NAME}-{target_architecture}'
    return MACOS_HELPER_NAME


def _require_matching_distribution() -> None:
    """Fail when installed metadata no longer matches pyproject.toml."""
    app_version = read_project_version()
    try:
        metadata_version = distribution_version('fleasion')
    except PackageNotFoundError:
        message = 'Fleasion distribution metadata is missing. Run uv sync before building.'
        raise SystemExit(message) from None
    if metadata_version != app_version:
        message = (
            f'Fleasion distribution metadata is {metadata_version}, but pyproject.toml '
            f'declares {app_version}. Run uv sync before building.'
        )
        raise SystemExit(message)


def _pyqt6_runtime_dll(name: str) -> str:
    """Locate a PyQt6 Windows runtime library that Qt loads on demand."""
    package_spec = find_spec('PyQt6')
    if package_spec is None or package_spec.origin is None:
        message = 'PyQt6 package not found; cannot locate Qt runtime libraries.'
        raise SystemExit(message)
    runtime_library = Path(package_spec.origin).parent / 'Qt6' / 'bin' / name
    return runtime_library.as_posix()


def _nuitka_arguments(*, clean: bool) -> list[str]:
    """Return the Nuitka arguments that build the standalone application."""
    application_mode = 'app' if sys.platform == 'darwin' else 'onefile'
    arguments = [
        f'--mode={application_mode}',
        f'--output-dir={NUITKA_OUTPUT_DIR}',
        f'--output-filename={_executable_name()}',
        '--assume-yes-for-downloads',
        '--enable-plugin=pyqt6',
        '--include-distribution-metadata=fleasion',
        *(f'--include-package={package}' for package in _FORCED_PACKAGES),
        *(
            f'--include-package={package}'
            for package in _OPTIONAL_PACKAGES
            if find_spec(package) is not None
        ),
        *(f'--include-module={module}' for module in _DYNAMIC_MODULES),
        *(
            f'--nofollow-import-to={module}'
            for module in (*_BASE_EXCLUDES, *_NUMPY_EXCLUDES, *_QT_EXCLUDES)
        ),
        *(
            f'--include-data-files={source}={destination}'
            for source, destination in _SHARED_DATA_FILES.items()
        ),
        '--include-data-dir=src/fleasion/cache/tools/animpreview=tools/animpreview',
        *(f'--noinclude-dlls={pattern}' for pattern in _UNUSED_QT_RUNTIME_DLLS),
        *(f'--noinclude-data-files={pattern}' for pattern in _QT_TRANSLATIONS_DATA),
    ]
    if clean:
        arguments.append('--clean-cache=all')

    if sys.platform == 'win32':
        arguments.extend(
            (
                '--windows-console-mode=disable',
                '--windows-icon-from-ico=src/fleasion/fleasionlogoHR.ico',
                '--include-data-files=src/fleasion/cache/tools/ktx_to_png/ktx.dll=ktx.dll',
                # Qt loads its software OpenGL fallback from the payload root on demand
                f'--include-data-files={_pyqt6_runtime_dll("opengl32sw.dll")}=opengl32sw.dll',
                *(f'--include-module={module}' for module in _WINDOWS_MODULES),
            )
        )
    elif sys.platform == 'darwin':
        arguments.extend(
            (
                f'--output-folder-name={MACOS_APP_NAME}',
                '--macos-app-mode=ui-element',
                '--macos-app-icon=src/fleasion/fleasionlogoHR.icns',
                f'--macos-app-version={macos_bundle_version(read_project_version())}',
                '--macos-signed-app-name=com.fleasion.app',
                '--include-package=browser_cookie3',
                '--include-package=Cryptodome',
                f'--include-data-files=dist/{_macos_helper_name()}={_macos_helper_name()}',
                # Nuitka's options-nanny treats PyQt6 on macOS as fatal while Qt still bundles
                '--disable-plugins=options-nanny',
            )
        )
        target_architecture = os.environ.get('MACOS_TARGET_ARCH')
        if target_architecture in _SLICE_ARCHITECTURES:
            arguments.append(f'--macos-target-arch={target_architecture}')
        deployment_target = os.environ.get('MACOSX_DEPLOYMENT_TARGET')
        if deployment_target:
            arguments.append(f'--macos-app-macos-min-version={deployment_target}')
    else:
        arguments.extend(
            (
                f'--include-data-files=dist/{LINUX_HELPER_NAME}={LINUX_HELPER_NAME}',
                '--include-data-files=src/fleasion/linux_proxy_helper_daemon.py=linux_proxy_helper_daemon.py',
                *(f'--noinclude-dlls={pattern}' for pattern in _LINUX_HOST_AUDIO_DLLS),
            )
        )

    arguments.append('launcher.py')
    return arguments


def _publish(source: Path, destination: Path) -> None:
    """Move a finished build artifact into the dist directory."""
    if not source.exists():
        message = f'Nuitka did not produce the expected artifact: {source}'
        raise SystemExit(message)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_dir():
        shutil.rmtree(destination)
    else:
        destination.unlink(missing_ok=True)
    shutil.move(str(source), str(destination))


def _build_helper(source: str, output_name: str) -> None:
    """Compile a proxy helper and publish it into dist/."""
    arguments = [
        '--mode=onefile',
        f'--output-dir={NUITKA_OUTPUT_DIR}',
        f'--output-filename={output_name}',
        '--assume-yes-for-downloads',
    ]
    if sys.platform == 'darwin':
        target_architecture = os.environ.get('MACOS_TARGET_ARCH')
        if target_architecture in _SLICE_ARCHITECTURES:
            arguments.append(f'--macos-target-arch={target_architecture}')

    log.info(f'Building {output_name}')
    run_nuitka([*arguments, source], skip_setup_logging=True)
    _publish(NUITKA_OUTPUT_DIR / output_name, DIST_DIR / output_name)


def _build_proxy_helpers() -> None:
    """Compile the proxy helper executables bundled with the application."""
    if sys.platform.startswith('linux'):
        _build_helper('src/fleasion/linux_proxy_helper_daemon.py', LINUX_HELPER_NAME)
    elif sys.platform == 'darwin':
        _build_helper('src/fleasion/macos_proxy_helper_daemon.py', _macos_helper_name())


def main(arguments: list[str] | None = None) -> int:
    """Build Fleasion with Nuitka."""
    options = _BuildArgumentParser().parse_args(arguments)
    setup_script_logging()

    # Environment changes such as PYTHONHASHSEED only apply after an interpreter restart
    if any(os.environ.get(name) != value for name, value in REPRODUCIBLE_ENV.items()):
        environment = os.environ.copy()
        environment.update(REPRODUCIBLE_ENV)

        command = [sys.executable, '-m', 'fleasion.scripts.build']
        if options.clean:
            command.append('--clean')

        log.info('Restarting build with reproducible environment')
        result = subprocess.run(command, cwd=Path.cwd(), env=environment, check=False)
        return result.returncode

    # Build macOS
    # Slice subprocesses bypass orchestration and run Nuitka exactly once
    if sys.platform == 'darwin' and os.environ.get(MACOS_SLICE_BUILD_ENV) != '1':
        from .macos_build import build_macos_release

        build_macos_release()
        return 0
    else:
        # Build Windows and Linux, plus the individual macOS slices
        NUITKA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        _require_matching_distribution()

        log.info(f'Building Fleasion from {Path.cwd()}')
        _build_proxy_helpers()
        run_nuitka(_nuitka_arguments(clean=options.clean), skip_setup_logging=True)

        staged_artifact = (
            NUITKA_OUTPUT_DIR / f'{MACOS_APP_NAME}.app'
            if sys.platform == 'darwin'
            else NUITKA_OUTPUT_DIR / _executable_name()
        )
        _publish(staged_artifact, DIST_DIR / staged_artifact.name)
        log.info(f'Built {DIST_DIR / staged_artifact.name}')
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
