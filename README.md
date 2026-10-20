# llama-docker-16g

A small Docker harness for building pinned upstream `llama.cpp` with Vulkan and
running it on mixed Linux GPU hosts. It exposes AMD Radeon render nodes from
`/dev/dri` and, when installed, enables the NVIDIA container runtime with the
graphics capability required by NVIDIA's Vulkan ICD. Before each start it asks
the built `llama-server` to list devices, filters software Vulkan devices, and
passes every hardware device explicitly with `--device Vulkan0,Vulkan1,...`.

The default runtime is tuned for the 16 GiB Qwen 3.8 setup described in the
[LocalLLaMA configuration post](https://www.reddit.com/r/LocalLLaMA/comments/1vqrt86/):
Unsloth `UD-Q3_K_XL`, 73,728 context, Q4_1 KV, native MTP plus modified n-gram
speculation, and one slot. Settings live in Compose and `.env`, so this is also
a general upstream Vulkan harness.

## What is included

- Exact upstream source pin in `forks.json`; source clones are ignored under
  `sources/upstream`.
- A shared Vulkan Dockerfile copied into upstream's `.devops` directory before
  every build.
- Buildx Bake with a reusable local cache at
  `.buildx-cache/upstream/vulkan`.
- Unique dated image tags containing the upstream Git SHA, plus movable
  `vulkan-latest` and `latest` aliases after a successful build.
- Shared Hugging Face cache, optional authenticated downloads, exact snapshot
  recording, local/offline model loading, caller UID/GID, health checks, capped
  logs, and saved active runtime state.
- Automatic AMD and NVIDIA Vulkan device exposure and explicit llama.cpp
  device selection. Software `llvmpipe`/`lavapipe` devices are ignored.

The image follows upstream's current Vulkan requirements: `GGML_VULKAN=ON`,
`libvulkan-dev`, `glslc`, and `spirv-headers`. Runtime Mesa drivers provide AMD
Vulkan; NVIDIA libraries and ICD data come from the host's NVIDIA container
runtime. See upstream's [build guide](https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md)
and [Vulkan Dockerfile](https://github.com/ggml-org/llama.cpp/blob/master/.devops/vulkan.Dockerfile).

## Host requirements

- Linux, Docker Engine, Compose v2, and Buildx.
- For Radeon: a working host Vulkan driver and `/dev/dri` render nodes. The
  wrapper passes the whole `/dev/dri` directory and its numeric group IDs.
- For NVIDIA: the proprietary driver plus NVIDIA Container Toolkit. The wrapper
  enables the `nvidia` runtime only when it is installed and GPUs are present.
- Enough disk for source, the image, BuildKit cache, and model weights.

You do not need CUDA or ROCm inside this image. Both GPU vendors use Vulkan.

## Build and run

```bash
cd /home/paul/docker/llama-docker-16g
cp .env.example .env

./rc fetch
./rc build
./rc devices
```

Download the pinned model into the shared cache:

```bash
./rc download unsloth/Qwen3.8-27B-GGUF \
  Qwen3.8-27B-UD-Q3_K_XL.gguf \
  --revision 430473d9d0e975450ce1f445642b6527cb4faea1
```

The download command prints and records an exact `container_path`. Start with
the file path below to avoid network resolution at runtime:

```bash
./rc start \
  --model /hf-cache/hub/models--unsloth--Qwen3.8-27B-GGUF/snapshots/430473d9d0e975450ce1f445642b6527cb4faea1/Qwen3.8-27B-UD-Q3_K_XL.gguf \
  --offline
```

The OpenAI-compatible API and web UI are then available at
`http://localhost:8080`, with model alias `Qwen3.8-27B`.

Useful commands:

```bash
./rc config --model /models/model.gguf --offline  # resolved config, no start
./rc status
./rc logs
./rc images
./rc stop
./rc start --tag vulkan-2026.10.20-12.34.56-gabcdef123456 ...
```

`./rc build --print` prepares the pinned source and prints Bake's resolved build
without compiling. `./rc update` advances the upstream pin in `forks.json`; that
change should be reviewed and committed before building.

## GPU selection

By default, `./rc start` runs `llama-server --list-devices` inside the same
container and with the same hardware mounts used by the service. Every hardware
Vulkan device is then passed to llama.cpp. Inspect the detected mapping with:

```bash
./rc devices
```

To select a subset or change order, set this in `.env`:

```dotenv
VULKAN_DEVICES=Vulkan0,Vulkan2
```

Device numbering belongs to the container's Vulkan loader. Always use the names
reported by `./rc devices`, especially on mixed AMD/NVIDIA hosts.

## Configuration

`.env.example` documents runtime overrides. The common tuning controls are
`CTX_SIZE`, `BATCH_SIZE`, `UBATCH_SIZE`, `CACHE_K`, `CACHE_V`, `THREADS`, and
`THREADS_BATCH`. Extra native server flags can be appended after
`--extra-args`, which must be last.

All containers run as the invoking numeric UID/GID. The HF cache therefore stays
writable from both the host and containers. Secrets are read from `HF_TOKEN` or
can be supplied through the ignored `.env`; Compose config output redacts the
token. Never pass credentials as Docker build arguments.

The wrapper intentionally manages one service. Starting a new configuration
force-recreates `llama-docker-16g-llama-server-1`, preventing two large model
servers from accidentally competing for the same GPUs.

## Repository layout

| Path | Purpose |
| --- | --- |
| `forks.json` | Upstream URL, branch, and exact source commit. |
| `docker/vulkan-server.Dockerfile` | Shared multi-stage Vulkan server build. |
| `docker-bake.hcl` | Build target, unique image name, and local cache export. |
| `compose.yml` | Qwen defaults, shared cache mounts, UID/GID, and health check. |
| `scripts/manage.py` | Source, build, device discovery, runtime, and download CLI. |
| `.state/` | Ignored build history, exact runtime command, device probe, and download records. |

