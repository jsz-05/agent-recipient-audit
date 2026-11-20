"""Fetch pinned official AppWorld bundles and unpack them locally.

Only public GET requests are used. Nothing is uploaded and no agent is run.
Keep the unpacked tree private as required by AppWorld's distribution license.
"""

import argparse
import ast
import base64
import hashlib
import io
import json
import urllib.request
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms
from cryptography.hazmat.decrepit.ciphers.modes import CFB
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

HERE = Path(__file__).resolve().parent


def acquire(url, path, digest, blob=False):
    """Reuse a verified bundle or download and verify the pinned public source."""
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
        return
    request = urllib.request.Request(
        url, headers={"User-Agent": "recipient-audit-reproduction"}
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        data = response.read()
    if blob:
        data = base64.b64decode(json.loads(data)["content"])
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Source digest mismatch: " + path.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def unpack(path, destination, key):
    raw = path.read_bytes()
    decryptor = Cipher(algorithms.AES(key), CFB(raw[:16])).decryptor()
    with zipfile.ZipFile(
        io.BytesIO(decryptor.update(raw[16:]) + decryptor.finalize())
    ) as archive:
        if sum(member.file_size for member in archive.infolist()) > 4_000_000_000:
            raise ValueError("Unexpected expanded size")
        for member in archive.infolist():
            if (
                not (destination / member.filename)
                .resolve()
                .is_relative_to(destination.resolve())
            ):
                raise ValueError("Unsafe archive member")
        destination.mkdir(parents=True, exist_ok=True)
        archive.extractall(destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "inputs")
    args = parser.parse_args()
    cache = args.output / "bundles"
    cache.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((HERE / "source-manifest.json").read_text())
    constant_source = manifest["public_constants"]
    constant_file = cache / "public-constants.py"
    acquire(constant_source["url"], constant_file, constant_source["sha256"], True)
    constants = {}
    for node in ast.parse(constant_file.read_text()).body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in ["PASSWORD", "SALT"]
        ):
            constants[node.targets[0].id] = ast.literal_eval(node.value)
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(), length=32, salt=constants["SALT"], iterations=100000
    ).derive(constants["PASSWORD"].encode())
    source = cache / "data-0.1.0.bundle"
    acquire(manifest["official_task_data_url"], source, manifest["task_data_sha256"])
    unpack(source, args.output / "task-data", key)
    # Every frozen supported task is in the challenge split.
    for bundle in manifest["baseline_bundles"]:
        if not bundle["name"].endswith("_test_challenge"):
            continue
        source = cache / (bundle["name"] + ".bundle")
        acquire(bundle["url"], source, bundle["sha256"])
        unpack(source, args.output / "runs", key)
        print("Prepared", bundle["name"], flush=True)
    print(
        "Inputs ready. Run run_study.py with --data",
        args.output / "task-data/data",
        "--runs",
        args.output / "runs",
    )


if __name__ == "__main__":
    main()
