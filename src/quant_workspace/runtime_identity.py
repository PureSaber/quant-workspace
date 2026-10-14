"""Read-only import identity probes, including interpreters predating this tool."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path

# Keep this probe stdlib-only: the separately frozen Python need not install Workspace.
PROBE = """
import hashlib, importlib, importlib.metadata, json, os, sys
from pathlib import Path
names = json.loads(sys.argv[1])
result = {'pid': os.getpid(), 'python': sys.executable, 'python_version': sys.version,
          'packages': {}}
for name in names:
    module = importlib.import_module(name.replace('-', '_'))
    distribution = importlib.metadata.distribution(name)
    path = module.__file__
    direct = distribution.read_text('direct_url.json')
    tree = hashlib.sha256()
    for source in sorted(Path(path).parent.rglob('*.py')):
        tree.update(source.relative_to(Path(path).parent).as_posix().encode())
        tree.update(source.read_bytes())
    result['packages'][name] = {'version': distribution.version, 'source': path,
        'sha256': hashlib.sha256(open(path, 'rb').read()).hexdigest(),
        'source_tree_sha256': tree.hexdigest(),
        'installation': json.loads(direct) if direct else None}
print(json.dumps(result))
"""


def hidden_options() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def current_identity(packages=("quant-studio", "quant-workspace")):
    result = {
        "pid": os.getpid(),
        "python": sys.executable,
        "python_version": sys.version,
        "packages": {},
    }
    for name in packages:
        module = importlib.import_module(name.replace("-", "_"))
        distribution = importlib.metadata.distribution(name)
        direct = distribution.read_text("direct_url.json")
        tree = hashlib.sha256()
        for source in sorted(Path(module.__file__).parent.rglob("*.py")):
            tree.update(source.relative_to(Path(module.__file__).parent).as_posix().encode())
            tree.update(source.read_bytes())
        result["packages"][name] = {
            "version": distribution.version,
            "source": module.__file__,
            "sha256": hashlib.sha256(Path(module.__file__).read_bytes()).hexdigest(),
            "source_tree_sha256": tree.hexdigest(),
            "installation": json.loads(direct) if direct else None,
        }
    return result


def probe_python(python, packages=("quant-studio", "quant-workspace")) -> dict:
    result = subprocess.run(
        [str(python), "-I", "-X", "utf8", "-c", PROBE, json.dumps(list(packages))],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        timeout=30,
        **hidden_options(),
    )
    if result.returncode:
        raise ValueError("Interpreter import verification failed: " + result.stderr[-2000:])
    return json.loads(result.stdout)


def validate_settings(python, path):
    """Reject incompatible settings using the target's own reader before stopping anything."""
    code = (
        "import json,sys; from pathlib import Path; "
        "from quant_studio.setup import validate_shape; "
        "validate_shape(json.loads(Path(sys.argv[1]).read_text('utf-8-sig')))"
    )
    result = subprocess.run(
        [str(python), "-I", "-X", "utf8", "-c", code, str(path)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        **hidden_options(),
    )
    if result.returncode:
        raise ValueError("Target settings are incompatible: " + result.stderr[-2000:])
