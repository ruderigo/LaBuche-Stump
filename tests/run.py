#!/usr/bin/env python3
"""Runs the Stump test suite.

    python3 tests/run.py                 # everything
    python3 tests/run.py plugin_api      # one test
    MICROPYTHON=/path/to/micropython python3 tests/run.py

The firmware tests run under the MicroPython Unix port built with
single-precision floats, like the ESP32-S3 (see tests/README.md), each in
a fresh copy of final_firmware/ (plus the plugins it needs), so nothing
writes into the source tree. Two kinds:

  assert    the script checks itself and ends with "FAILURES: []"
  snapshot  the output, normalised (random nonces, signatures, clock
            values, log lines), must match tests/expected/<name>.txt.
            --update rewrites those files: review the diff before
            committing it.
"""
import importlib.util, os, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FW = ROOT / "final_firmware"
PACKS = ROOT / "packs"
MP = os.environ.get("MICROPYTHON", "micropython")
TEST_PLUGINS = [HERE / "plugins" / n for n in ("dice", "crashy", "future")]
PACK_PLUGINS = [PACKS / "tavern-pack" / "tavern", PACKS / "rnfs-pack" / "rnfs"]

# name, kind, extra plugin folders
TESTS = [
    ("chat_golden", "snapshot", []),
    ("access_golden", "snapshot", []),
    ("mesh_golden", "snapshot", []),
    ("plugin_api", "assert", TEST_PLUGINS),
    ("plugin_web", "assert", TEST_PLUGINS),
    ("safe_mode", "safe", TEST_PLUGINS),
    ("fservbot_storage", "snapshot", []),
    ("packs_chat", "assert", PACK_PLUGINS),
    ("packs_web", "snapshot", PACK_PLUGINS),
    ("relay_fastpath", "assert", []),
    ("chain", "chain", []),
    ("chain_config", "assert", []),
    ("chain_failover", "assert", []),
    ("json_api", "snapshot", []),
    ("latin1_names", "snapshot", []),
    ("tools_sections", "assert", []),
    ("admin_theme_tools", "assert", []),
    ("admin_session_layout", "assert", []),
    ("hardening", "assert", []),
    ("https_voice", "assert", []),
    ("librarian", "assert", []),
    ("mesh_room", "assert", []),
    ("radio_status", "assert", []),
    ("profiles_store", "assert", []),
    ("conn_limits", "assert", []),
    ("same_person", "assert", []),
    ("keepalive_boot", "assert", []),
    ("upload_fair", "assert", []),
    ("identity_matrix", "assert", []),
    ("password_cost", "assert", []),
]

_NORM = [
    (re.compile(r"AUTH-CHALLENGE [0-9a-f]{32}"), "AUTH-CHALLENGE <nonce>"),
    (re.compile(r"(/auth [0-9a-f]{128}) [0-9a-f]{128}"), r"\1 <sig>"),
    (re.compile(r"\b1[6-9]\d{8}(\.\d+)?\b"), "<time>"),
    (re.compile(r"/tmp/[^\s'\"]*?(/_sd\w*)"), r"<tmp>\1"),
    (re.compile(r"guest-[0-9a-f]{4}"), "guest-xxxx"),
]


def annotate(name, text):
    """On GitHub Actions, a failing test shows as an annotation with its
    output: the run's logs can't always be read, the annotations can."""
    if os.environ.get("GITHUB_ACTIONS"):
        body = ("%s failed:\n%s" % (name, text))[-3500:]
        print("::error title=%s::%s" % (name, body.replace("%", "%25").replace("\r", "").replace("\n", "%0A")))


def normalise(text, workdir):
    out = []
    for line in text.replace(str(workdir), "<work>").splitlines():
        if re.match(r"^\[\d+\]\[(WARN|INFO|DEBUG)\]", line):
            continue
        if re.match(r"^\[plugin\] \S+: .* took \d+ ms \(keep callbacks under", line):
            continue                # how fast the machine is, not what the code says
        for rx, rep in _NORM:
            line = rx.sub(rep, line)
        out.append(line.rstrip())
    return "\n".join(out).strip() + "\n"


def tree(extra):
    d = Path(tempfile.mkdtemp(prefix="stump-test-"))
    shutil.copytree(FW, d / "fw", ignore=shutil.ignore_patterns("__pycache__", "third_party", "tools_payload"))
    for p in extra:
        shutil.copytree(p, d / "fw" / p.name, ignore=shutil.ignore_patterns("__pycache__"))
    # A fixed config, not the repository's (which is a real node's): the
    # snapshots must not change because someone renamed their bot.
    shutil.copy(HERE / "config.py", d / "fw" / "config.py")
    return d / "fw"


def run_mp(script, cwd, *args):
    r = subprocess.run([MP, str(HERE / "mp" / script)] + [str(HERE / "mp")] + list(args),
                       cwd=cwd, capture_output=True, text=True, timeout=600)
    return r.stdout + r.stderr


def main():
    update = "--update" in sys.argv
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    failed = []
    for name, kind, extra in TESTS:
        if only and name not in only:
            continue
        cwd = tree(extra)
        try:
            if kind == "chain":
                # Four MicroPython processes: a three-Stump chain plus an intruder.
                logs = {}
                procs = {}
                for r in ("A", "B", "C", "E"):
                    procs[r] = subprocess.Popen([MP, "-X", "heapsize=16M", str(HERE / "mp" / "chain_node.py"),
                                                 str(HERE / "mp"), r, str(cwd)], cwd=cwd,
                                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                    time.sleep(0.5)
                # A PC joining B with the chain secret, and one with the wrong one.
                time.sleep(3)
                pc_ok = subprocess.run([sys.executable, str(ROOT / "scripts" / "chain_hello.py"), "127.0.5.1",
                                        "chain-test-secret", "--bind=127.0.8.2", "--once"],
                                       capture_output=True, text=True, timeout=30)
                pc_bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "chain_hello.py"), "127.0.5.1",
                                         "wrong", "--bind=127.0.9.2", "--once"],
                                        capture_output=True, text=True, timeout=30)
                for r, p in procs.items():
                    try:
                        logs[r] = p.communicate(timeout=200)[0]
                    except subprocess.TimeoutExpired:
                        p.kill()
                        logs[r] = p.communicate()[0] + "\nTIMEOUT"
                out = "\n".join(logs[r] for r in ("A", "B", "C", "E"))
                out += "\nPC with the secret: %s\nPC without: %s" % (pc_ok.stdout.strip(), pc_bad.stdout.strip())
                ok = ("A DONE file arrived intact" in out and "C DONE sent" in out
                      and "children: 127.0.6.2, 127.0.8.2\n" in out + "\n" and "FAIL" not in out
                      and "Traceback" not in out and pc_ok.returncode == 0 and pc_bad.returncode == 1)
                detail = "\n".join(l for l in out.splitlines() if not l.startswith("[")) [:3000]
                print(("ok    " if ok else "FAIL  ") + name)
                if not ok:
                    print(detail)
                    failed.append(name)
                    annotate(name, detail)
                continue
            if kind == "safe":
                seq = ["reset", "culprit", "normal", "reset", "running", "normal"]
                out = "".join("== %s\n" % m + "\n".join(l for l in run_mp(name + ".py", cwd, m).splitlines()
                                                       if l.startswith(("RUNNING", "SAFE ", "OFF", "MARKER", "[plugin] SAFE")))
                              + "\n" for m in seq)
                kind = "snapshot"
            else:
                out = run_mp(name + ".py", cwd)
            if kind == "assert":
                ok = re.search(r"^FAILURES:? \[\]$", out, re.M) is not None and "Traceback" not in out
                detail = "\n".join(l for l in out.splitlines() if l.startswith("FAIL") or "Error" in l)[:2000]
            else:
                got = normalise(out, cwd)
                exp_file = HERE / "expected" / (name + ".txt")
                if update:
                    exp_file.write_text(got)
                want = exp_file.read_text() if exp_file.exists() else ""
                ok = got == want
                if not ok:
                    import difflib
                    detail = "".join(difflib.unified_diff(want.splitlines(True), got.splitlines(True),
                                                          "expected", "got", n=1))[:3000]
            print(("ok    " if ok else "FAIL  ") + name)
            if not ok:
                print(detail)
                failed.append(name)
                annotate(name, detail)
        finally:
            shutil.rmtree(cwd.parent, ignore_errors=True)
    if not only or "provisioner" in only:
        r = subprocess.run([sys.executable, str(HERE / "provisioner_test.py")], capture_output=True, text=True)
        ok = "FAILURES []" in r.stdout
        print(("ok    " if ok else "FAIL  ") + "provisioner")
        if not ok:
            print((r.stdout + r.stderr)[-3000:])
            failed.append("provisioner")
            annotate("provisioner", "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                                              if l.startswith("FAIL") or "Error" in l)[-3000:])
    # Stump on a computer (docs/PC.md): the same firmware on regular Python.
    # Needs the cryptography and pyserial packages (CI installs them).
    for name, script in (("pc_rnode_serial", "rnode_serial_test.py"), ("pc_rnode_ble", "rnode_ble_test.py"),
                         ("pc_radio_link", "radio_link_test.py"),
                         ("pc_lora_radio", "lora_radio_test.py"),
                         ("pc_chain", "pc_chain_test.py"),
                         ("pc_joins_cam", "pc_joins_cam_test.py"), ("pc_two_stumps", "pc_two_stumps_test.py"),
                         ("pc_parent_moves", "pc_parent_moves_test.py"), ("pc_power", "pc_power_test.py"),
                         ("pc_settings", "settings_test.py"),
                         ("pc_discovery", "discovery_test.py"), ("pc_storage", "storage_test.py"),
                         ("pc_conn", "conn_test.py"), ("pc_watchdog", "watchdog_test.py"),
                         ("pc_radio_only", "pc_radio_only_test.py"), ("pc_chain_radio", "pc_chain_radio_test.py")):
        if only and name not in only:
            continue
        missing = [m for m in ("cryptography", "serial") if importlib.util.find_spec(m) is None]
        if missing:
            print("skip  %s (needs: pip install cryptography pyserial)" % name)
            continue
        if name in ("pc_rnode_serial", "pc_radio_link") and os.name == "nt":
            print("skip  %s (uses a pseudo-terminal: Linux and macOS)" % name)
            continue
        env = dict(os.environ, MICROPYTHON=MP)
        r = subprocess.run([sys.executable, str(HERE / "pc" / script)], capture_output=True, text=True,
                           env=env, timeout=600)
        ok = r.returncode == 0 and "FAILURES []" in r.stdout
        print(("ok    " if ok else "FAIL  ") + name)
        if not ok:
            print("\n".join(l for l in (r.stdout + r.stderr).splitlines() if not l.startswith("["))[-3000:])
            failed.append(name)
            annotate(name, "\n".join(l for l in (r.stdout + r.stderr).splitlines()
                                      if l.startswith("FAIL") or "Error" in l)[-3000:])
    print("\n%d failed: %s" % (len(failed), ", ".join(failed)) if failed else "\nall passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
