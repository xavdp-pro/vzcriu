#!/usr/bin/env python3
"""Offline, static tests for the podmesh-vzcriu-helpers* packaging adaptation.

These tests import node.py/lab.py/replication.py directly and the installed
wrapper shell scripts from packaging/, touching only temp directories,
environment variables and short-lived subprocesses. They never invoke
podman, crun, CRIU, ssh, or root privileges, and never require the real
podmesh-vzcriu runtime package or any .deb to be installed. Run with:

    python3 -m unittest discover -s contrib/nested-podman/tests -p 'test_*.py' -v

or via build-helpers-deb.sh --check.
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent
PACKAGING = KIT / 'packaging'

# node.py/lab.py/replication.py are imported the same way they run in
# production: replication.py does `from lab import ssh, node, ...`, which
# only resolves if the kit directory itself is on sys.path (exactly what
# happens when any of these run as a script, since Python puts a script's
# own directory at sys.path[0]).
sys.path.insert(0, str(KIT))
import node as NODE          # noqa: E402
import lab as LAB             # noqa: E402
import replication as REPLICATION  # noqa: E402

NODE_ENV_VARS = [
    'PODMESH_VZCRIU_RUNTIME',
    'PODMESH_VZCRIU_MANUAL_WRAPPER', 'PODMESH_VZCRIU_MANUAL_REAL', 'PODMESH_VZCRIU_MANUAL_BINDIR',
    'PODMESH_VZCRIU_PACKAGED_WRAPPER', 'PODMESH_VZCRIU_PACKAGED_REAL', 'PODMESH_VZCRIU_PACKAGED_BINDIR',
]
LAB_ENV_VARS = [
    'PODMESH_VZCRIU_RUNTIME', 'PODMESH_VZCRIU_NODE_CMD',
    'PODMESH_VZCRIU_MANUAL_KIT_LIB', 'PODMESH_VZCRIU_PACKAGED_KIT_LIB',
]
ALL_ENV_VARS = sorted(set(NODE_ENV_VARS) | set(LAB_ENV_VARS) | {
    'PODMESH_VZCRIU_NODE_PY_OVERRIDE', 'PODMESH_VZCRIU_LAB_PY_OVERRIDE', 'PODMESH_VZCRIU_REPLICATION_PY_OVERRIDE',
})


def _write_fake_binary(path, marker):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho {marker}\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def _write_fake_node_module(directory, marker):
    """A stand-in for node.py used only to prove which directory a remote
    'sys.path.insert(0, X); import node' bootstrap actually loaded from."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'node.py').write_text(f'MARKER = {marker!r}\n')


class EnvIsolatedTestCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in ALL_ENV_VARS}
        for k in ALL_ENV_VARS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestNodeRuntimeDetection(EnvIsolatedTestCase):
    """node.py's own local autodetection (runs ON a node host); unaffected
    by the controller-side "mode" concept added to lab.py/replication.py."""

    def test_default_matches_original_manual_layout(self):
        runtime = NODE._detect_runtime()
        self.assertEqual(runtime['wrapper'], Path('/opt/vzcriu-kit/bin/criu'))
        self.assertEqual(runtime['real'], Path('/opt/vzcriu-kit/criu.real'))
        self.assertEqual(runtime['bindir'], '/opt/vzcriu-kit/bin')

    def test_autodetect_prefers_manual_kit_when_both_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            manual_real = tmp / 'manual/criu.real'
            packaged_real = tmp / 'packaged/usr/lib/podmesh-vzcriu/criu'
            _write_fake_binary(manual_real, 'manual')
            _write_fake_binary(packaged_real, 'packaged')
            os.environ['PODMESH_VZCRIU_MANUAL_REAL'] = str(manual_real)
            os.environ['PODMESH_VZCRIU_PACKAGED_REAL'] = str(packaged_real)
            runtime = NODE._detect_runtime()
            self.assertEqual(runtime['real'], manual_real)

    def test_autodetect_falls_back_to_packaged_when_manual_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            manual_real = tmp / 'manual/criu.real'  # never created
            packaged_real = tmp / 'packaged/usr/lib/podmesh-vzcriu/criu'
            packaged_wrapper = tmp / 'packaged/usr/bin/podmesh-vzcriu'
            _write_fake_binary(packaged_real, 'packaged-real')
            _write_fake_binary(packaged_wrapper, 'packaged-wrapper')
            os.environ['PODMESH_VZCRIU_MANUAL_REAL'] = str(manual_real)
            os.environ['PODMESH_VZCRIU_PACKAGED_REAL'] = str(packaged_real)
            os.environ['PODMESH_VZCRIU_PACKAGED_WRAPPER'] = str(packaged_wrapper)
            runtime = NODE._detect_runtime()
            self.assertEqual(runtime['real'], packaged_real)
            self.assertEqual(runtime['wrapper'], packaged_wrapper)

    def test_explicit_packaged_mode_dispatches_to_mocked_podmesh_vzcriu_binary(self):
        """Force packaged mode, point it at a fake podmesh-vzcriu executable
        placed on a private PATH directory, and confirm dispatch actually
        invokes that mocked binary rather than any real CRIU/system path."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            packaged_real = tmp / 'usr/lib/podmesh-vzcriu/criu'
            packaged_wrapper = tmp / 'usr/bin/podmesh-vzcriu'
            packaged_bindir = tmp / 'opt/podmesh-vzcriu-kit/bin'
            _write_fake_binary(packaged_real, 'FAKE_CRIU_REAL')
            _write_fake_binary(packaged_wrapper, 'FAKE_PODMESH_VZCRIU_WRAPPER')
            packaged_bindir.mkdir(parents=True)
            shim = packaged_bindir / 'criu'
            shim.write_text(f'#!/bin/sh\nexec {packaged_wrapper} "$@"\n')
            shim.chmod(0o755)

            os.environ['PODMESH_VZCRIU_RUNTIME'] = 'packaged'
            os.environ['PODMESH_VZCRIU_PACKAGED_REAL'] = str(packaged_real)
            os.environ['PODMESH_VZCRIU_PACKAGED_WRAPPER'] = str(packaged_wrapper)
            os.environ['PODMESH_VZCRIU_PACKAGED_BINDIR'] = str(packaged_bindir)

            runtime = NODE._detect_runtime()
            self.assertEqual(runtime['wrapper'], packaged_wrapper)
            self.assertTrue(runtime['real'].is_file())

            out = subprocess.run([str(runtime['wrapper']), '--version'], stdout=subprocess.PIPE, text=True, check=True).stdout
            self.assertIn('FAKE_PODMESH_VZCRIU_WRAPPER', out)
            shim_out = subprocess.run([str(shim), '--version'], stdout=subprocess.PIPE, text=True, check=True).stdout
            self.assertIn('FAKE_PODMESH_VZCRIU_WRAPPER', shim_out)

    def test_explicit_manual_mode_overrides_autodetected_packaged(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            manual_real = tmp / 'manual/criu.real'  # absent on purpose
            packaged_real = tmp / 'packaged/criu'
            _write_fake_binary(packaged_real, 'packaged')
            os.environ['PODMESH_VZCRIU_RUNTIME'] = 'manual'
            os.environ['PODMESH_VZCRIU_MANUAL_REAL'] = str(manual_real)
            os.environ['PODMESH_VZCRIU_PACKAGED_REAL'] = str(packaged_real)
            runtime = NODE._detect_runtime()
            self.assertEqual(runtime['real'], manual_real)
            self.assertFalse(runtime['real'].is_file())

    def test_invalid_mode_raises(self):
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'bogus'
        with self.assertRaises(RuntimeError):
            NODE._detect_runtime()


class TestLabControllerMode(EnvIsolatedTestCase):
    """lab.py's controller-side mode resolution: unchanged 'manual' default
    when nothing is set (source-tree / direct invocation), explicit
    'packaged' when PODMESH_VZCRIU_RUNTIME is set (as the installed
    podmesh-vzcriu-lab/-replication wrappers do internally, unconditionally,
    before the user ever sees a prompt for it)."""

    def test_default_mode_is_manual(self):
        self.assertEqual(LAB.resolved_mode(), 'manual')

    def test_default_remote_command_is_unchanged(self):
        argv = LAB.remote_node_argv('preflight', 'demo')
        self.assertEqual(argv, ['sudo', 'python3', '/opt/vzcriu-kit/node.py', 'preflight', '--name', 'demo'])

    def test_default_remote_command_with_job(self):
        argv = LAB.remote_node_argv('checkpoint', 'demo', 'job-1')
        self.assertEqual(argv, ['sudo', 'python3', '/opt/vzcriu-kit/node.py', 'checkpoint', '--name', 'demo', '--job', 'job-1'])

    def test_default_kit_lib_dir_is_manual(self):
        self.assertEqual(LAB.kit_lib_dir(), '/opt/vzcriu-kit')

    def test_packaged_mode_selects_packaged_node_command_and_lib_dir(self):
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'packaged'
        self.assertEqual(LAB.resolved_mode(), 'packaged')
        argv = LAB.remote_node_argv('preflight', 'demo')
        self.assertEqual(argv, ['sudo', 'podmesh-vzcriu-node', 'preflight', '--name', 'demo'])
        self.assertEqual(LAB.kit_lib_dir(), '/opt/podmesh-vzcriu-kit/lib')

    def test_explicit_node_cmd_overrides_mode_derived_default(self):
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'packaged'
        os.environ['PODMESH_VZCRIU_NODE_CMD'] = 'sudo python3 /custom/node.py'
        argv = LAB.remote_node_argv('preflight', 'demo')
        self.assertEqual(argv, ['sudo', 'python3', '/custom/node.py', 'preflight', '--name', 'demo'])

    def test_invalid_mode_raises(self):
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'bogus'
        with self.assertRaises(ValueError):
            LAB.resolved_mode()


class TestReplicationArgvConstruction(EnvIsolatedTestCase):
    """The remote SNAPSHOT/STOP inline scripts must receive TWO deterministic
    values baked into argv (not environment variables, not a search list):
    argv[1] the lib directory to import node.py from, and argv[2] the
    runtime mode node.py itself should select once imported. Both matter
    because they are two separate decisions (see the comment above SNAPSHOT
    in replication.py): which node.py FILE loads, and what CRIU
    runtime/wrapper that node.py then calls. sudo on the target host
    typically resets the environment, and a host could have both layouts
    present, so neither decision can rely on inherited env vars or on
    node.py's own autodetection once it runs remotely."""

    def test_snippets_bake_in_mode_and_force_it_before_import(self):
        for snippet in (REPLICATION.SNAPSHOT, REPLICATION.STOP):
            self.assertIn('sys.path.insert(0,sys.argv[1])', snippet)
            self.assertIn("os.environ['PODMESH_VZCRIU_RUNTIME']=sys.argv[2]", snippet)
            # The mode must be forced BEFORE `import node`, since node.py
            # reads PODMESH_VZCRIU_RUNTIME at module-import time.
            mode_set_at = snippet.index("os.environ['PODMESH_VZCRIU_RUNTIME']=sys.argv[2]")
            import_at = snippet.index('import node as n')
            self.assertLess(mode_set_at, import_at)
            self.assertNotIn('for _p in', snippet)  # no more insertion-order-dependent search list

    def test_snapshot_argv_default_mode_bakes_manual_lib_dir_and_mode(self):
        argv = REPLICATION.snapshot_argv('demo', 'job-1')
        self.assertEqual(argv, ['sudo', 'python3', '-c', REPLICATION.SNAPSHOT, '/opt/vzcriu-kit', 'manual', 'demo', 'job-1'])

    def test_stop_argv_packaged_mode_bakes_packaged_lib_dir_and_mode(self):
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'packaged'
        argv = REPLICATION.stop_argv('demo')
        self.assertEqual(argv, ['sudo', 'python3', '-c', REPLICATION.STOP, '/opt/podmesh-vzcriu-kit/lib', 'packaged', 'demo'])

    def test_lib_dir_and_mode_are_consistent_for_the_same_resolved_mode(self):
        # A caller must never end up with a lib_dir from one mode and a mode
        # string from another: both snapshot_argv/stop_argv resolve the mode
        # exactly once and derive both values from it.
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'packaged'
        argv = REPLICATION.snapshot_argv('demo', 'job-1')
        lib_dir, mode = argv[4], argv[5]
        self.assertEqual(mode, 'packaged')
        self.assertEqual(lib_dir, LAB.kit_lib_dir('packaged'))

    def test_explicit_lib_dir_argument_overrides_default_but_mode_still_resolved(self):
        argv = REPLICATION.stop_argv('demo', lib_dir='/wherever')
        self.assertEqual(argv[4], '/wherever')
        self.assertEqual(argv[5], 'manual')  # unchanged default mode, independent of lib_dir override

    def test_explicit_mode_argument_overrides_both_lib_dir_default_and_mode(self):
        argv = REPLICATION.stop_argv('demo', mode='packaged')
        self.assertEqual(argv[4], '/opt/podmesh-vzcriu-kit/lib')
        self.assertEqual(argv[5], 'packaged')


class TestRemoteBootstrapDeterminismUnderSudoLikeEnv(EnvIsolatedTestCase):
    """End-to-end simulation of WHICH node.py FILE loads on the node host:
    both a manual-layout and a packaged-layout node.py stub (a stand-in
    module, not the real node.py) exist simultaneously, the controller
    resolves one mode, and the resulting argv is executed with the
    environment wiped (env -i), simulating sudo stripping
    PODMESH_VZCRIU_RUNTIME/PATH/etc. The correct module must load every
    time, driven only by argv[1], never by insertion order or by any
    inherited environment variable.

    This class only proves file-selection (argv[1]); it says nothing about
    what runtime MODE the real node.py itself would then select once
    imported, since these stub modules don't have a _detect_runtime() to
    get wrong. That is a second, separate decision, covered against the
    REAL, unmocked node.py by TestRealNodeRuntimeModeSelectionOverSudo
    below (this split mirrors a real bug: an earlier revision fixed file
    selection via argv[1] but forgot to also force node.py's own
    PODMESH_VZCRIU_RUNTIME once imported, so it silently fell back to
    node.py's manual-preferring autodetection regardless of the mode the
    controller had actually resolved)."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.manual_dir = Path(self._tmp.name) / 'manual-kit'
        self.packaged_dir = Path(self._tmp.name) / 'packaged-kit'
        _write_fake_node_module(self.manual_dir, 'manual')
        _write_fake_node_module(self.packaged_dir, 'packaged')
        os.environ['PODMESH_VZCRIU_MANUAL_KIT_LIB'] = str(self.manual_dir)
        os.environ['PODMESH_VZCRIU_PACKAGED_KIT_LIB'] = str(self.packaged_dir)

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def _run_bootstrap(self, lib_dir, name_arg='demo'):
        # Mirrors SNAPSHOT/STOP's own bootstrap line exactly; env -i strips
        # the *entire* environment (a stricter stand-in than plain sudo) to
        # prove nothing but argv[1] determines which module loads.
        script = 'import sys\nsys.path.insert(0,sys.argv[1])\nimport node as n\nprint(n.MARKER)\n'
        return subprocess.run(
            ['env', '-i', sys.executable, '-c', script, lib_dir, name_arg],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        ).stdout.strip()

    def test_packaged_mode_wins_deterministically_with_both_layouts_present(self):
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'packaged'
        argv = REPLICATION.stop_argv('demo')
        lib_dir = argv[4]
        self.assertEqual(lib_dir, str(self.packaged_dir))
        self.assertEqual(self._run_bootstrap(lib_dir), 'packaged')

    def test_manual_mode_wins_deterministically_with_both_layouts_present(self):
        # No PODMESH_VZCRIU_RUNTIME set: unchanged default, must still pick
        # manual even though the packaged stub also exists on disk.
        argv = REPLICATION.stop_argv('demo')
        lib_dir = argv[4]
        self.assertEqual(lib_dir, str(self.manual_dir))
        self.assertEqual(self._run_bootstrap(lib_dir), 'manual')

    def test_packaged_only_layout_with_manual_absent(self):
        # Manual directory genuinely does not exist on this host: only the
        # packaged stub is present. Packaged mode must still resolve and
        # load correctly.
        missing_manual = Path(self._tmp.name) / 'no-such-manual-kit'
        os.environ['PODMESH_VZCRIU_MANUAL_KIT_LIB'] = str(missing_manual)
        os.environ['PODMESH_VZCRIU_RUNTIME'] = 'packaged'
        argv = REPLICATION.stop_argv('demo')
        lib_dir = argv[4]
        self.assertEqual(lib_dir, str(self.packaged_dir))
        self.assertEqual(self._run_bootstrap(lib_dir), 'packaged')
        # And manual mode against the same (now packaged-only) host must
        # fail loudly rather than silently falling back to packaged.
        with self.assertRaises(subprocess.CalledProcessError):
            self._run_bootstrap(str(missing_manual))


class TestRealNodeRuntimeModeSelectionOverSudo(EnvIsolatedTestCase):
    """Reproduces and proves the fix for the bug the human reviewer found:
    argv[1] (file selection) alone is not enough. The REAL, unmocked node.py
    (imported from this repository, not a stand-in stub) must ALSO select
    the runtime mode (manual vs packaged -> which CRIU binary/wrapper it
    calls, via node.py's own _detect_runtime()/RUNTIME) that the controller
    actually resolved -- not whatever node.py's own autodetection would
    otherwise pick once it's running remotely with a environment that sudo
    has reset.

    Fixture: BOTH a manual-layout CRIU binary and a packaged-layout CRIU
    binary exist on disk at once (mirroring node.py's own
    _detect_runtime(): the manual candidate is keyed off
    PODMESH_VZCRIU_MANUAL_REAL, the packaged candidate off
    PODMESH_VZCRIU_PACKAGED_REAL/_WRAPPER -- see node.py's _candidate()).
    The whole subprocess environment is wiped with `env -i`, exactly as in
    TestRemoteBootstrapDeterminismUnderSudoLikeEnv, so the ONLY way
    PODMESH_VZCRIU_RUNTIME can reach node.py's module-level
    `RUNTIME = _detect_runtime()` is through the bootstrap forcing it from
    argv[2] (the SNAPSHOT/STOP prefix under test), never through inheritance."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        # A single shared kit lib directory containing the REAL node.py is
        # sufficient here: this class is about runtime MODE selection, not
        # file selection (already covered above), so both "manual" and
        # "packaged" bootstrap runs import the same real module.
        self.manual_real = tmp / 'manual/criu.real'
        self.packaged_real = tmp / 'packaged/usr/lib/podmesh-vzcriu/criu'
        self.packaged_wrapper = tmp / 'packaged/usr/bin/podmesh-vzcriu'
        _write_fake_binary(self.manual_real, 'MANUAL_CRIU')
        _write_fake_binary(self.packaged_real, 'PACKAGED_CRIU')
        _write_fake_binary(self.packaged_wrapper, 'PACKAGED_WRAPPER')

    def tearDown(self):
        self._tmp.cleanup()
        super().tearDown()

    def _resolved_real_path(self, mode):
        """Runs the ACTUAL SNAPSHOT bootstrap prefix (sys.path.insert +
        forcing PODMESH_VZCRIU_RUNTIME from argv[2] + `import node as n`),
        against the real node.py in this repo, under a fully wiped
        environment re-populated only with the fixture-location env vars
        (which stand in for node.py's real, hardcoded /opt and /usr paths --
        in production those would need no env var at all; here they only
        relocate the fixtures so the test needs no root). Returns which CRIU
        binary node.py's own RUNTIME actually resolved to."""
        prefix = REPLICATION.SNAPSHOT.split("import node as n", 1)[0] + "import node as n\n"
        self.assertIn('sys.path.insert(0,sys.argv[1])', prefix)
        self.assertIn("os.environ['PODMESH_VZCRIU_RUNTIME']=sys.argv[2]", prefix)
        script = prefix + "print(n.RUNTIME['real'])\n"
        result = subprocess.run(
            [
                'env', '-i',
                f'PODMESH_VZCRIU_MANUAL_REAL={self.manual_real}',
                f'PODMESH_VZCRIU_PACKAGED_REAL={self.packaged_real}',
                f'PODMESH_VZCRIU_PACKAGED_WRAPPER={self.packaged_wrapper}',
                sys.executable, '-c', script, str(KIT), mode, 'demo', 'job-1',
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )
        return result.stdout.strip()

    def test_packaged_mode_selects_packaged_criu_with_both_present(self):
        resolved = self._resolved_real_path('packaged')
        self.assertEqual(resolved, str(self.packaged_real))

    def test_manual_mode_selects_manual_criu_with_both_present(self):
        resolved = self._resolved_real_path('manual')
        self.assertEqual(resolved, str(self.manual_real))

    def test_argv_mode_matches_controller_resolved_mode_for_both_cases(self):
        """Ties it together end to end: whatever lab.py/replication.py
        resolve as the controller mode is exactly what real node.py selects
        remotely, for both values, with both layouts present at once."""
        for mode in ('manual', 'packaged'):
            os.environ['PODMESH_VZCRIU_RUNTIME'] = mode
            argv = REPLICATION.stop_argv('demo')
            self.assertEqual(argv[5], mode)  # what the controller baked into argv
            expected_real = self.manual_real if mode == 'manual' else self.packaged_real
            self.assertEqual(self._resolved_real_path(argv[5]), str(expected_real))
            del os.environ['PODMESH_VZCRIU_RUNTIME']

    def test_without_the_fix_this_would_have_defaulted_to_manual(self):
        """Regression guard: a bootstrap that imports node.py WITHOUT first
        forcing PODMESH_VZCRIU_RUNTIME from argv (the exact bug reported)
        falls back to node.py's own autodetection, which prefers manual --
        so requesting packaged mode would silently resolve to the manual
        CRIU binary. This is the failure mode the fix in SNAPSHOT/STOP
        (and this test class) closes."""
        broken_script = (
            "import sys,os\n"
            "sys.path.insert(0,sys.argv[1])\n"
            "import node as n\n"  # no os.environ['PODMESH_VZCRIU_RUNTIME'] = ... before import
            "print(n.RUNTIME['real'])\n"
        )
        result = subprocess.run(
            [
                'env', '-i',
                f'PODMESH_VZCRIU_MANUAL_REAL={self.manual_real}',
                f'PODMESH_VZCRIU_PACKAGED_REAL={self.packaged_real}',
                f'PODMESH_VZCRIU_PACKAGED_WRAPPER={self.packaged_wrapper}',
                # Deliberately including a stray PODMESH_VZCRIU_RUNTIME=packaged
                # here would prove nothing (env -i args ARE the environment,
                # not something sudo stripped); the point is that WITHOUT
                # argv-based forcing, the caller's intended "packaged" is
                # simply never communicated, so autodetection runs instead.
                sys.executable, '-c', broken_script, str(KIT), 'unused-mode-arg',
            ],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True,
        )
        self.assertEqual(result.stdout.strip(), str(self.manual_real))


class TestInstalledWrapperScripts(EnvIsolatedTestCase):
    """Executes the *actual* packaging/podmesh-vzcriu-{node,lab,replication}
    shell scripts that get installed as /usr/bin/podmesh-vzcriu-* by the
    .deb, proving the installed commands select packaged mode with NO
    environment variables set by the caller (the exact requirement: a clean
    target with only the Debian packages installed must work out of the
    box), while still letting an explicit override through."""

    WRAPPERS_AND_OVERRIDES = [
        ('podmesh-vzcriu-node', 'PODMESH_VZCRIU_NODE_PY_OVERRIDE'),
        ('podmesh-vzcriu-lab', 'PODMESH_VZCRIU_LAB_PY_OVERRIDE'),
        ('podmesh-vzcriu-replication', 'PODMESH_VZCRIU_REPLICATION_PY_OVERRIDE'),
    ]

    def _probe_script(self, tmp):
        probe = Path(tmp) / 'probe.py'
        probe.write_text("import os\nprint(os.environ.get('PODMESH_VZCRIU_RUNTIME', ''))\n")
        return probe

    def test_all_wrappers_default_to_packaged_with_no_env_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._probe_script(tmp)
            for wrapper_name, override_var in self.WRAPPERS_AND_OVERRIDES:
                wrapper = PACKAGING / wrapper_name
                self.assertTrue(wrapper.is_file(), wrapper)
                env = {k: v for k, v in os.environ.items() if k not in ALL_ENV_VARS}
                env[override_var] = str(probe)
                out = subprocess.run(['sh', str(wrapper)], env=env, stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
                self.assertEqual(out, 'packaged', f'{wrapper_name} did not default to packaged mode')

    def test_wrappers_respect_explicit_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            probe = self._probe_script(tmp)
            for wrapper_name, override_var in self.WRAPPERS_AND_OVERRIDES:
                wrapper = PACKAGING / wrapper_name
                env = {k: v for k, v in os.environ.items() if k not in ALL_ENV_VARS}
                env[override_var] = str(probe)
                env['PODMESH_VZCRIU_RUNTIME'] = 'manual'
                out = subprocess.run(['sh', str(wrapper)], env=env, stdout=subprocess.PIPE, text=True, check=True).stdout.strip()
                self.assertEqual(out, 'manual', f'{wrapper_name} did not honor an explicit override')

    def test_criu_shim_forwards_to_podmesh_vzcriu(self):
        shim = PACKAGING / 'criu-shim'
        self.assertTrue(shim.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin = Path(tmp) / 'usr/bin'
            fake_bin.mkdir(parents=True)
            _write_fake_binary(fake_bin / 'podmesh-vzcriu', 'FAKE_PODMESH_VZCRIU')
            env = dict(os.environ, PATH=str(fake_bin) + ':' + os.environ.get('PATH', ''))
            # The shim hardcodes /usr/bin/podmesh-vzcriu (by design: it is
            # only ever installed alongside the real package), so this test
            # only exercises its syntax/exec behavior via shellcheck-style
            # invocation rather than redirecting the target.
            result = subprocess.run(['sh', '-n', str(shim)])
            self.assertEqual(result.returncode, 0)
            self.assertIn('exec /usr/bin/podmesh-vzcriu "$@"', shim.read_text())


class TestShellSyntax(unittest.TestCase):
    def test_all_shell_scripts_parse(self):
        scripts = list(KIT.glob('build*.sh')) + list(KIT.glob('check*.sh')) + list(PACKAGING.glob('*'))
        self.assertTrue(scripts)
        for script in scripts:
            if not script.is_file():
                continue
            result = subprocess.run(['bash', '-n', str(script)], stderr=subprocess.PIPE, text=True)
            self.assertEqual(result.returncode, 0, f'{script}: {result.stderr}')


if __name__ == '__main__':
    unittest.main()
