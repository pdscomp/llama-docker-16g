#!/usr/bin/env python3
"""Pinned upstream llama.cpp Vulkan build and portable multi-vendor runtime."""
import argparse
import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / ".state"
PROJECT = "llama-docker-16g"
DEFAULT_IMAGE_REPOSITORY = "ghcr.io/pdscomp/llama-docker-16g"


def run(args, **kwargs):
    return subprocess.run([str(x) for x in args], check=True, cwd=ROOT, **kwargs)


def output(args, **kwargs):
    return run(args, capture_output=True, text=True, **kwargs).stdout.strip()


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write("\n")
    temporary.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def manifest():
    return json.loads((ROOT / "forks.json").read_text())


def environment():
    env = {}
    dotenv = ROOT / ".env"
    if dotenv.exists():
        for raw in dotenv.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, sep, value = line.partition("=")
            if not sep or not key.replace("_", "").isalnum():
                raise ValueError(f"Invalid .env line: {raw}")
            env[key] = " ".join(shlex.split(value, comments=True))
    env.update(os.environ)
    env["LOCAL_UID"], env["LOCAL_GID"] = str(os.getuid()), str(os.getgid())

    def directory(name, default):
        path = Path(env.get(name, default)).expanduser()
        if not path.is_absolute():
            path = ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        env[name] = str(path.resolve())

    directory("HF_CACHE_DIR", ROOT.parent / "hf-cache")
    directory("RUNTIME_DIR", STATE / "runtime")
    directory("PROMPT_CACHE_DIR", STATE / "prompt-cache")
    directory("LOCAL_MODELS_DIR", STATE / "models")
    Path(env["RUNTIME_DIR"], "home").mkdir(exist_ok=True)
    env["HF_HOME"] = env["HF_CACHE_DIR"]
    directory("HF_HUB_CACHE", Path(env["HF_CACHE_DIR"]) / "hub")
    env["LLAMA_CACHE"] = env["HF_HUB_CACHE"]
    env.setdefault("IMAGE_TAG", "vulkan-latest")
    env.setdefault("IMAGE_REPOSITORY", DEFAULT_IMAGE_REPOSITORY)
    env.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
    token_file = ROOT / ".secrets" / "hf-token"
    if not env.get("HF_TOKEN") and token_file.exists():
        env["HF_TOKEN"] = token_file.read_text().strip()
    return env


@contextlib.contextmanager
def locked(name):
    STATE.mkdir(exist_ok=True)
    with open(STATE / f"{name}.lock", "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError(f"{name}: another operation is running")
        yield


def source_path():
    return ROOT / "sources" / "upstream"


def clean_source(path):
    if output(["git", "-C", path, "status", "--porcelain", "--untracked-files=no"]):
        raise ValueError(f"Tracked source changes in {path}")


def fetch(update=False):
    specs = manifest()
    spec = specs["upstream"]
    if update:
        spec["commit"] = output(["git", "ls-remote", spec["url"], "refs/heads/" + spec["branch"]]).split()[0]
        atomic_json(ROOT / "forks.json", specs)
    path = source_path()
    path.parent.mkdir(exist_ok=True)
    if not path.exists():
        run(["git", "init", path], stdout=sys.stderr)
        run(["git", "-C", path, "remote", "add", "origin", spec["url"]], stdout=sys.stderr)
    clean_source(path)
    found = subprocess.run(["git", "-C", path, "cat-file", "-e", spec["commit"] + "^{commit}"],
                           capture_output=True)
    if found.returncode:
        run(["git", "-C", path, "fetch", "--depth=1", "origin", spec["commit"]], stdout=sys.stderr)
    run(["git", "-C", path, "checkout", "--detach", spec["commit"]], stdout=sys.stderr)
    run(["git", "-C", path, "submodule", "update", "--init", "--recursive", "--depth=1"], stdout=sys.stderr)
    if update:
        print("Updated forks.json; review and commit the new upstream pin.")
    return path


def image_exists(image):
    result = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
    if result.returncode == 0:
        return True
    if "no such" in result.stderr.lower():
        return False
    raise ValueError(result.stderr.strip())


def build(args):
    env, spec = environment(), manifest()["upstream"]
    env.setdefault("BUILD_JOBS", "8")
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y.%m.%d-%H.%M.%S")
    tag = f"vulkan-{stamp}-g{spec['commit'][:12]}"
    repository = env["IMAGE_REPOSITORY"]
    image = f"{repository}:{tag}"
    with locked("upstream"):
        path = fetch()
        devops = path / ".devops"
        devops.mkdir(exist_ok=True)
        shutil.copyfile(ROOT / "docker/vulkan-server.Dockerfile", devops / "vulkan-server.Dockerfile")
        shutil.copyfile(ROOT / "docker/vulkan-server.Dockerfile.dockerignore",
                        devops / "vulkan-server.Dockerfile.dockerignore")
        config_revision = output(["git", "rev-parse", "HEAD"])
        labels = {
            "org.opencontainers.image.source": spec["url"],
            "org.opencontainers.image.revision": spec["commit"],
            "org.opencontainers.image.created": dt.datetime.now(dt.timezone.utc).isoformat(),
            "io.llama-docker-16g.backend": "vulkan",
            "io.llama-docker-16g.config-revision": config_revision,
            "io.llama-docker-16g.dockerfile-sha256": digest(ROOT / "docker/vulkan-server.Dockerfile"),
        }
        overlay = STATE / "build-input.json"
        atomic_json(overlay, {"target": {"upstream": {"tags": [image], "labels": labels}}})
        command = ["docker", "buildx", "bake", "-f", ROOT / "docker-bake.hcl", "-f", overlay]
        resolved = json.loads(output(command + ["--print", "upstream"], env=env))
        if args.print:
            print(json.dumps(resolved, indent=2))
            return
        builder = env.get("BUILDX_BUILDER", "llama-vulkan-builder")
        if subprocess.run(["docker", "buildx", "inspect", builder], capture_output=True).returncode:
            run(["docker", "buildx", "create", "--name", builder, "--driver", "docker-container"])
        history = STATE / "builds" / f"{stamp}.json"
        atomic_json(history, {"status": "building", "image": image, "resolved": resolved})
        try:
            run(command + ["--builder", builder, "--progress=plain", "upstream"], env=env)
            image_id = output(["docker", "image", "inspect", "--format", "{{.Id}}", image])
            tags = [image, f"{repository}:vulkan-latest", f"{repository}:latest"]
            for alias in tags[1:]:
                run(["docker", "tag", image, alias])
            atomic_json(history, {"status": "success", "image": image, "image_id": image_id,
                                  "tags": tags, "resolved": resolved})
        except BaseException:
            atomic_json(history, {"status": "failed", "image": image, "resolved": resolved})
            raise


def hardware_override(env):
    service = {"environment": {}}
    dri = Path("/dev/dri")
    if dri.is_dir():
        service["devices"] = ["/dev/dri:/dev/dri"]
        gids = sorted({str(entry.stat().st_gid) for entry in dri.iterdir()})
        if gids:
            service["group_add"] = gids
    try:
        runtimes = json.loads(output(["docker", "info", "--format", "{{json .Runtimes}}"]))
    except (json.JSONDecodeError, subprocess.CalledProcessError):
        runtimes = {}
    has_nvidia = "nvidia" in runtimes and shutil.which("nvidia-smi") is not None
    if has_nvidia:
        probe = subprocess.run(["nvidia-smi", "-L"], capture_output=True, text=True)
        has_nvidia = probe.returncode == 0 and bool(probe.stdout.strip())
    if has_nvidia:
        service["deploy"] = {"resources": {"reservations": {"devices": [{
            "driver": "nvidia", "count": "all", "capabilities": ["gpu"]
        }]}}}
        service["environment"].update(
            NVIDIA_VISIBLE_DEVICES="all",
            NVIDIA_DRIVER_CAPABILITIES="all",
        )
    if not dri.is_dir() and not has_nvidia:
        raise ValueError("No /dev/dri devices or NVIDIA container runtime were detected")
    path = STATE / "hardware.json"
    atomic_json(path, {"services": {"llama-server": service}})
    return path


def compose_files(env, include_command=False):
    files = [ROOT / "compose.yml", hardware_override(env)]
    command = STATE / "runtime-command.json"
    if include_command and command.exists():
        files.append(command)
    result = ["docker", "compose", "--project-name", PROJECT]
    for path in files:
        result += ["-f", path]
    return result


def discover_devices(env):
    override = env.get("VULKAN_DEVICES", "").strip()
    if override:
        return [item.strip() for item in override.split(",") if item.strip()], "configured"
    probe_script = "/app/llama-server --list-devices; echo __VULKANINFO__; vulkaninfo --summary"
    cmd = compose_files(env) + ["run", "--rm", "--no-deps", "--entrypoint", "/bin/sh",
                                "llama-server", "-c", probe_script]
    result = subprocess.run([str(x) for x in cmd], cwd=ROOT, env=env, capture_output=True, text=True)
    report = result.stdout + result.stderr
    if result.returncode:
        raise ValueError("Vulkan device probe failed:\n" + report.strip())
    devices = []
    descriptions = {}
    for line in report.splitlines():
        match = re.search(r"\b(Vulkan\d+)\s*:\s*(.*)", line)
        if match and not any(word in match.group(2).lower() for word in ("llvmpipe", "lavapipe", "cpu")):
            devices.append(match.group(1))
            descriptions[match.group(1)] = match.group(2)
    devices = list(dict.fromkeys(devices))
    if not devices:
        raise ValueError("No hardware Vulkan devices found. Probe output:\n" + report.strip())

    # llama.cpp and vulkaninfo both enumerate through the Vulkan loader. Prefer
    # one discrete GPU so an integrated GPU cannot throttle an automatic layer
    # split. Users can explicitly request one or several devices with
    # VULKAN_DEVICES when their loader uses unusual numbering or multi-GPU is
    # intentional.
    types = {}
    current = None
    in_vulkaninfo = False
    for line in report.splitlines():
        if "__VULKANINFO__" in line:
            in_vulkaninfo = True
            continue
        if not in_vulkaninfo:
            continue
        match = re.match(r"GPU(\d+):", line.strip())
        if match:
            current = f"Vulkan{match.group(1)}"
            continue
        match = re.search(r"deviceType\s*=\s*(PHYSICAL_DEVICE_TYPE_[A-Z_]+)", line)
        if current and match:
            types[current] = match.group(1)

    discrete = [device for device in devices
                if types.get(device) == "PHYSICAL_DEVICE_TYPE_DISCRETE_GPU"]
    candidates = discrete or devices

    def memory_mib(device):
        match = re.search(r"(\d+)\s+MiB", descriptions.get(device, ""))
        return int(match.group(1)) if match else 0

    selected = [max(candidates, key=memory_mib)]
    reason = "discrete GPU" if discrete else "first/best available hardware GPU"
    selection = f"Selected {selected[0]} ({reason}); set VULKAN_DEVICES to override."
    full_report = report.rstrip() + "\n" + selection + "\n"
    atomic_json(STATE / "vulkan-devices.json", {
        "available": devices, "types": types, "selected": selected, "probe": report,
    })
    return selected, full_report


def resolved_command(env, args, devices):
    config = json.loads(output(compose_files(env) + ["config", "--format", "json"], env=env))
    command = config["services"]["llama-server"]["command"]

    def remove_pair(flag):
        while flag in command:
            index = command.index(flag)
            del command[index:index + 2]

    if args.model:
        for flag in ("--hf-repo", "--hf-file", "-hf"):
            remove_pair(flag)
        mounts = {"/hf-cache": env["HF_CACHE_DIR"], "/models": env["LOCAL_MODELS_DIR"]}
        valid = False
        for mount, host in mounts.items():
            try:
                relative = Path(args.model).relative_to(mount)
            except ValueError:
                continue
            candidate = (Path(host) / relative).resolve()
            valid = candidate.is_relative_to(Path(host).resolve()) and candidate.exists()
        if not valid:
            raise ValueError("--model must name an existing file under /hf-cache or /models")
        command += ["-m", args.model]
    if args.offline:
        command.append("--offline")
    command += ["--device", ",".join(devices)]
    if args.extra_args:
        command += args.extra_args
    return config, command


def runtime(args):
    env = environment()
    if args.action in {"stop", "status", "logs"}:
        control = STATE / "control.json"
        if not control.exists():
            raise ValueError("No recorded service; use ./rc start first")
        command = ["docker", "compose", "--project-name", PROJECT, "-f", control]
        operation = {"stop": ["down"], "status": ["ps"],
                     "logs": ["logs", "--tail", "100", "-f", "llama-server"]}[args.action]
        run(command + operation, env=env)
        return
    image = f"{env['IMAGE_REPOSITORY']}:{env['IMAGE_TAG']}"
    if not image_exists(image):
        print(f"Pulling {image} ...", file=sys.stderr)
        run(["docker", "pull", image])
    devices, report = discover_devices(env)
    config, command = resolved_command(env, args, devices)
    if args.action == "config":
        config["services"]["llama-server"]["command"] = command
        if "HF_TOKEN" in config["services"]["llama-server"].get("environment", {}):
            config["services"]["llama-server"]["environment"]["HF_TOKEN"] = "[redacted]"
        print(json.dumps(config, indent=2))
        return
    with locked("runtime"):
        atomic_json(STATE / "runtime-command.json", {"services": {"llama-server": {"command": command}}})
        full = compose_files(env, include_command=True)
        run(full + ["up", "-d", "--no-build", "--pull", "never", "--force-recreate"], env=env)
        container = f"{PROJECT}-llama-server-1"
        atomic_json(STATE / "control.json", {"services": {"llama-server": {"image": image,
                    "container_name": container}}})
        atomic_json(STATE / "active.json", {"image": image, "devices": devices, "command": command,
                    "user": f"{env['LOCAL_UID']}:{env['LOCAL_GID']}",
                    "started_at": dt.datetime.now(dt.timezone.utc).isoformat(), "probe": report})
        print(f"Started {image} on {','.join(devices)} at http://localhost:{env.get('HOST_PORT','8080')}")


def hf_python():
    python = ROOT / ".venv/bin/python"
    marker = ROOT / ".venv/requirements.sha256"
    expected = digest(ROOT / "requirements-hf.txt")
    if not python.exists():
        run([sys.executable, "-m", "venv", ROOT / ".venv"])
    if not marker.exists() or marker.read_text().strip() != expected:
        run([python, "-m", "pip", "install", "--disable-pip-version-check", "-r", ROOT / "requirements-hf.txt"])
        marker.parent.mkdir(exist_ok=True)
        marker.write_text(expected + "\n")
    return python


def download(args):
    env = environment()
    result = run([hf_python(), ROOT / "scripts/hf.py", args.repo, *args.files,
                  "--revision", args.revision], env=env, capture_output=True, text=True)
    record = json.loads(result.stdout.splitlines()[-1])
    record["container_path"] = str(Path("/hf-cache") / Path(record["path"]).relative_to(env["HF_CACHE_DIR"]))
    atomic_json(STATE / "downloads" / (args.repo.replace("/", "--") + ".json"), record)
    print(json.dumps(record, indent=2))


def images(_args):
    repository = environment()["IMAGE_REPOSITORY"]
    run(["docker", "image", "ls", repository,
         "--format", "table {{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedSince}}\t{{.Size}}"])


def publish(args):
    env = environment()
    repository = env["IMAGE_REPOSITORY"]
    if args.tag:
        images = [f"{repository}:{args.tag}"]
    else:
        successful = None
        for path in sorted((STATE / "builds").glob("*.json"), reverse=True):
            record = json.loads(path.read_text())
            if record.get("status") == "success":
                successful = record
                break
        if not successful:
            raise ValueError("No successful build record; run ./rc build first")
        images = [image for image in successful["tags"] if image.startswith(repository + ":")]
    for image in images:
        if not image_exists(image):
            raise ValueError(f"Missing local image {image}; run ./rc build first")
        run(["docker", "push", image])


def pull(args):
    env = environment()
    tag = args.tag or env["IMAGE_TAG"]
    run(["docker", "pull", f"{env['IMAGE_REPOSITORY']}:{tag}"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for action in ("fetch", "update"):
        item = sub.add_parser(action)
        item.set_defaults(func=lambda args, update=action == "update": fetch(update))
    item = sub.add_parser("build")
    item.add_argument("--print", action="store_true")
    item.set_defaults(func=build)
    for action in ("start", "config"):
        item = sub.add_parser(action, help=(
            "pull if needed, discover GPUs, and start the default Qwen profile"
            if action == "start" else "print the resolved runtime configuration"))
        item.add_argument("--tag", help="override IMAGE_TAG")
        item.add_argument("--model", help="existing GGUF under /hf-cache or /models")
        item.add_argument("--offline", action="store_true", help="disable runtime network access")
        item.add_argument("--extra-args", nargs=argparse.REMAINDER,
                          help="additional llama-server arguments; must be last")
        item.set_defaults(func=runtime)
    for action in ("stop", "status", "logs"):
        item = sub.add_parser(action)
        item.set_defaults(func=runtime)
    item = sub.add_parser("devices")
    item.set_defaults(func=lambda _args: print(discover_devices(environment())[1]))
    item = sub.add_parser("download")
    item.add_argument("repo")
    item.add_argument("files", nargs="*")
    item.add_argument("--revision", default="main")
    item.set_defaults(func=download)
    item = sub.add_parser("images")
    item.set_defaults(func=images)
    item = sub.add_parser("publish")
    item.add_argument("--tag", help="push one local tag; defaults to the latest successful build's tags")
    item.set_defaults(func=publish)
    item = sub.add_parser("pull")
    item.add_argument("--tag")
    item.set_defaults(func=pull)
    args = parser.parse_args()
    if getattr(args, "tag", None):
        os.environ["IMAGE_TAG"] = args.tag
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except KeyboardInterrupt:
        raise SystemExit(130)

