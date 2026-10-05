from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from pytest import MonkeyPatch

from fleasion.scripts import build, macos_build


def test_workflow_verifies_nuitka_payload() -> None:
    workflow_source = (
        Path(__file__).resolve().parents[1] / '.github/workflows/build.yml'
    ).read_text(encoding='utf-8')

    assert 'pyi-archive_viewer' not in workflow_source
    assert 'build/nuitka/launcher.dist' in workflow_source
    assert 'module.fleasion.cache.obj_viewer.c' in workflow_source


def _set_reproducible_environment(monkeypatch: MonkeyPatch) -> None:
    for name, value in build.REPRODUCIBLE_ENV.items():
        monkeypatch.setenv(name, value)


def test_build_dispatches_to_macos_release_builder(monkeypatch: MonkeyPatch) -> None:
    _set_reproducible_environment(monkeypatch)
    monkeypatch.setattr(build.sys, 'platform', 'darwin')
    monkeypatch.delenv(build.MACOS_SLICE_BUILD_ENV, raising=False)
    calls: list[None] = []

    def build_macos_release() -> None:
        calls.append(None)

    monkeypatch.setattr(macos_build, 'build_macos_release', build_macos_release)

    assert build.main([]) == 0
    assert calls == [None]


def test_macos_slice_build_runs_nuitka_without_redispatch(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    _set_reproducible_environment(monkeypatch)
    monkeypatch.setattr(build.sys, 'platform', 'darwin')
    monkeypatch.setenv(build.MACOS_SLICE_BUILD_ENV, '1')
    monkeypatch.delenv('MACOS_TARGET_ARCH', raising=False)
    monkeypatch.setattr(build, 'NUITKA_OUTPUT_DIR', tmp_path / 'nuitka')
    monkeypatch.setattr(build, 'DIST_DIR', tmp_path / 'dist')
    monkeypatch.setattr(build, '_require_matching_distribution', lambda: None)
    helper_calls: list[tuple[str, str]] = []
    nuitka_calls: list[tuple[list[str], bool]] = []
    publish_calls: list[tuple[Path, Path]] = []

    def build_helper(source: str, output_name: str) -> None:
        helper_calls.append((source, output_name))

    def run_nuitka(arguments: list[str], *, skip_setup_logging: bool) -> None:
        nuitka_calls.append((arguments, skip_setup_logging))

    def publish(source: Path, destination: Path) -> None:
        publish_calls.append((source, destination))

    monkeypatch.setattr(build, '_build_helper', build_helper)
    monkeypatch.setattr(build, 'run_nuitka', run_nuitka)
    monkeypatch.setattr(build, '_publish', publish)

    assert build.main(['--clean']) == 0

    assert helper_calls == [('src/fleasion/macos_proxy_helper_daemon.py', 'fleasion-proxy-helper')]
    assert len(nuitka_calls) == 1
    arguments, skip_setup_logging = nuitka_calls[0]
    assert skip_setup_logging
    assert arguments[-1] == 'launcher.py'
    assert '--mode=app' in arguments
    assert '--clean-cache=all' in arguments
    assert '--output-folder-name=Fleasion' in arguments
    assert '--macos-app-mode=ui-element' in arguments
    assert '--disable-plugins=options-nanny' in arguments
    assert '--macos-target-arch=' not in ' '.join(arguments)
    assert publish_calls == [
        (tmp_path / 'nuitka' / 'Fleasion.app', tmp_path / 'dist' / 'Fleasion.app')
    ]


def test_windows_nuitka_arguments_match_packaging_contract(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(build.sys, 'platform', 'win32')

    arguments = build._nuitka_arguments(clean=False)

    assert arguments[-1] == 'launcher.py'
    assert '--mode=onefile' in arguments
    assert '--windows-console-mode=disable' in arguments
    assert '--windows-icon-from-ico=src/fleasion/fleasionlogoHR.ico' in arguments
    assert '--include-data-files=src/fleasion/cache/tools/ktx_to_png/ktx.dll=ktx.dll' in arguments
    assert any(argument.endswith('=opengl32sw.dll') for argument in arguments)
    assert '--include-distribution-metadata=fleasion' in arguments
    assert '--enable-plugin=pyqt6' in arguments
    assert '--noinclude-dlls=*qtiff.dll' in arguments
    assert '--include-data-dir=src/fleasion/cache/tools/animpreview=tools/animpreview' in (
        arguments
    )
    for module in ('win32crypt', 'PyQt6.QtWidgets', 'PyQt6.QtOpenGL', 'DracoPy'):
        assert f'--include-module={module}' in arguments
    for package in ('numpy', 'lz4'):
        assert f'--include-package={package}' in arguments
    assert '--nofollow-import-to=numpy.f2py' in arguments
    assert '--nofollow-import-to=numpy.typing.tests' in arguments
    assert '--nofollow-import-to=pytest' in arguments
    for argument in arguments:
        if argument.startswith('--include-package='):
            assert build.find_spec(argument.partition('=')[2]) is not None
    assert '--disable-plugins=options-nanny' not in arguments
    assert '--clean-cache=all' not in arguments


def test_linux_nuitka_arguments_keep_host_audio(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(build.sys, 'platform', 'linux')

    arguments = build._nuitka_arguments(clean=True)

    assert arguments[-1] == 'launcher.py'
    assert '--mode=onefile' in arguments
    assert '--clean-cache=all' in arguments
    assert (
        '--include-data-files=dist/fleasion-linux-proxy-helper=fleasion-linux-proxy-helper'
        in arguments
    )
    assert (
        '--include-data-files=src/fleasion/linux_proxy_helper_daemon.py='
        'linux_proxy_helper_daemon.py' in arguments
    )
    assert '--noinclude-dlls=*libportaudio.so*' not in arguments
    assert '--noinclude-dlls=libportaudio.so*' in arguments
    for package in ('_sounddevice_data', '_soundfile_data'):
        included = f'--include-package={package}' in arguments
        assert included == (build.find_spec(package) is not None)
    assert '--disable-plugins=options-nanny' not in arguments
    assert not any('--macos-' in argument or '--windows-' in argument for argument in arguments)


def test_macos_versions_are_normalized_for_comparison() -> None:
    assert macos_build.MacOSBuilder._version_tuple('11.0') == (11, 0, 0)
    assert macos_build.MacOSBuilder._version_tuple('11.0.0') == (11, 0, 0)


def test_macos_prerelease_paths_use_local_artifact_version(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(macos_build, 'read_project_version', lambda: '2.4.0b1')
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)

    builder = macos_build.MacOSBuilder()

    assert builder.executable_name == 'Fleasion-v2.4.0b1+local'
    assert builder.versioned_app_path == Path('dist/Fleasion-v2.4.0b1+local.app')
    assert builder.zip_path == Path('dist/Fleasion-v2.4.0b1+local-MacOS-Universal.zip')


def test_macos_stable_paths_use_canonical_version(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(macos_build, 'read_project_version', lambda: '2.4.0')
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    monkeypatch.delenv('GITHUB_SHA', raising=False)

    builder = macos_build.MacOSBuilder()

    assert builder.executable_name == 'Fleasion-v2.4.0'
    assert builder.zip_path == Path('dist/Fleasion-v2.4.0-MacOS-Universal.zip')


def test_universal_verification_ignores_helper_symlink_targets(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    builder = object.__new__(macos_build.MacOSBuilder)
    builder.executable_name = 'Fleasion-v1.0.0'
    resources = tmp_path / 'Contents/Resources'
    frameworks = tmp_path / 'Contents/Frameworks'
    resources.mkdir(parents=True)
    frameworks.mkdir(parents=True)

    helper_paths: dict[str, Path] = {}
    framework_helpers: list[Path] = []
    for architecture in ('arm64', 'x86_64'):
        helper_name = f'fleasion-proxy-helper-{architecture}'
        framework_helper = frameworks / helper_name
        framework_helper.touch()
        resource_helper = resources / helper_name
        try:
            resource_helper.symlink_to(framework_helper)
        except OSError as exc:
            pytest.skip(f'helper symlinks require Windows developer-mode privileges: {exc}')
        helper_paths[helper_name] = resource_helper
        framework_helpers.append(framework_helper)

    def require_architectures(_file_path: Path, *_required: str) -> None:
        return None

    def require_payload(
        _app_path: Path,
        relative_path: str,
        _build_label: str,
        *,
        executable: bool = False,
    ) -> Path:
        assert executable
        return helper_paths[relative_path]

    def require_only_architectures(_file_path: Path, *_required: str) -> None:
        return None

    monkeypatch.setattr(builder, '_require_architectures', require_architectures)
    monkeypatch.setattr(builder, '_require_payload', require_payload)
    monkeypatch.setattr(builder, '_require_only_architectures', require_only_architectures)
    monkeypatch.setattr(builder, '_regular_files', lambda _app_path: framework_helpers)

    builder._verify_app_architectures(tmp_path)


def test_single_arch_allowlist_requires_the_expected_architecture(tmp_path: Path) -> None:
    builder = object.__new__(macos_build.MacOSBuilder)
    allowed = (
        ('Contents/MacOS/Cryptodome/Cipher/_raw_aesni.so', {'x86_64'}),
        ('Contents/MacOS/Cryptodome/Cipher/_raw_aesni.abi3.so', {'x86_64'}),
        ('Contents/MacOS/Cryptodome/Hash/_ghash_clmul.so', {'x86_64'}),
        ('Contents/MacOS/_soundfile_data/libsndfile_arm64.dylib', {'arm64'}),
        ('Contents/MacOS/_soundfile_data/libsndfile_x86_64.dylib', {'x86_64'}),
    )
    for relative_path, archs in allowed:
        file_path = tmp_path / relative_path
        matched = builder._is_allowed_single_arch_macho(  # ruff: ignore[private-member-access]
            tmp_path, file_path, archs
        )
        assert matched

    rejected = (
        ('Contents/MacOS/Cryptodome/Cipher/_raw_aesni.so', {'arm64'}),
        ('Contents/MacOS/_soundfile_data/libsndfile_arm64.dylib', {'x86_64'}),
        ('Contents/MacOS/fleasion/thing.so', {'x86_64'}),
    )
    for relative_path, archs in rejected:
        file_path = tmp_path / relative_path
        matched = builder._is_allowed_single_arch_macho(  # ruff: ignore[private-member-access]
            tmp_path, file_path, archs
        )
        assert not matched


def test_arm_build_resolves_for_the_deployment_platform(monkeypatch: MonkeyPatch) -> None:
    builder = object.__new__(macos_build.MacOSBuilder)
    builder.base_environment = {'MACOSX_DEPLOYMENT_TARGET': '11.0'}
    commands: list[tuple[list[str], dict[str, str] | None]] = []
    verified_slices: list[tuple[str, str]] = []

    def subprocess_run(
        command: list[str], *, environment: dict[str, str] | None = None, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append((command, environment))
        return subprocess.CompletedProcess(command, 0, '', '')

    def verify_slice(architecture: str, label: str) -> None:
        verified_slices.append((architecture, label))

    monkeypatch.setattr(builder, '_verify_slice', verify_slice)
    monkeypatch.setattr(macos_build, 'subprocess_run', subprocess_run)

    builder._build_arm64()

    assert commands[0] == (
        [
            'uv',
            'sync',
            '--locked',
            '--python-platform',
            macos_build.ARM64_PYTHON_PLATFORM,
            '--group',
            'dev',
        ],
        builder.base_environment,
    )
    assert commands[1][0] == [
        macos_build.sys.executable,
        '-m',
        'fleasion.scripts.build',
        '--clean',
    ]
    assert commands[1][1] == {
        'MACOSX_DEPLOYMENT_TARGET': '11.0',
        'MACOS_TARGET_ARCH': 'arm64',
        macos_build._SLICE_BUILD_ENV: '1',
    }
    assert verified_slices == [('arm64', 'Build')]


def test_x86_build_uses_the_project_python_pin(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    builder = object.__new__(macos_build.MacOSBuilder)
    builder.x86_environment_path = tmp_path / 'venv-x86'
    builder.x86_uv_path = tmp_path / 'uv-x86_64'
    builder.base_environment = {}
    uv_calls: list[tuple[str, ...]] = []
    commands: list[list[str]] = []
    verified_slices: list[tuple[str, str]] = []

    def x86_uv(*arguments: str, capture_output: bool = False) -> str:
        assert not capture_output
        uv_calls.append(arguments)
        return ''

    def subprocess_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, '', '')

    def ensure_x86_uv() -> None:
        return None

    def verify_slice(architecture: str, label: str) -> None:
        verified_slices.append((architecture, label))

    monkeypatch.setattr(builder, '_ensure_x86_uv', ensure_x86_uv)
    monkeypatch.setattr(builder, '_x86_uv', x86_uv)
    monkeypatch.setattr(builder, '_verify_slice', verify_slice)
    monkeypatch.setattr(macos_build, 'subprocess_run', subprocess_run)

    builder._build_x86_64()

    assert uv_calls == [
        (
            'sync',
            '--locked',
            '--python-platform',
            macos_build.X86_64_PYTHON_PLATFORM,
            '--group',
            'dev',
        )
    ]
    assert commands == [
        ['arch', '-x86_64', str(builder.x86_uv_path), 'run', '--no-sync', 'build', '--clean']
    ]
    assert verified_slices == [('x86_64', 'Intel build')]
