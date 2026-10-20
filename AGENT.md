# Agent guide

Canonical remotes are `https://github.com/pdscomp/llama-docker-16g` and
`ghcr.io/pdscomp/llama-docker-16g`. Clones pull `vulkan-latest` by default;
local builds use the same repository name so Compose needs no changes.

Read this file before modifying the repository. This is an upstream-only
`llama.cpp` Vulkan harness for mixed AMD Radeon and NVIDIA Linux hosts. It builds
one image family, `ghcr.io/pdscomp/llama-docker-16g`, and manages one Compose
service.

## Authoritative files

- `forks.json`: upstream URL, branch, and exact commit. Never edit
  `sources/upstream` as the durable implementation; it is an ignored checkout.
- `docker/vulkan-server.Dockerfile`: shared Vulkan server image. `./rc build`
  copies it into `sources/upstream/.devops` before Bake.
- `docker-bake.hcl`: image name and local BuildKit cache exporters.
- `compose.yml`: model/runtime defaults and mounts. Hardware is deliberately
  absent because `scripts/manage.py` generates `.state/hardware.json` per host.
- `scripts/manage.py`: all source, image, device, and runtime orchestration.
- `.env.example`: documented user overrides. `.env` is private and ignored.
- `.github/workflows/publish-image.yml`: manual GHCR build and publication with
  the repository's package token and a persistent local Bake cache.

## Invariants

1. Build from the exact commit in `forks.json` and put the source SHA in dated
   image tags and OCI labels.
2. Move `vulkan-latest`/`latest` only after a successful build. Keep dated tags
   for rollback.
3. Keep Buildx cache exports under `.buildx-cache/upstream/vulkan`.
4. Run as the caller's UID/GID and share the host HF cache. Do not bake models,
   tokens, or host paths into the image. Keep shared model mounts labeled `z`
   and private runtime mounts labeled `Z` for SELinux hosts.
5. Expose `/dev/dri` when present. Add an NVIDIA Compose GPU reservation only
   when both the runtime and an NVIDIA GPU are detected. Request all NVIDIA
   driver capabilities so its Vulkan ICD can be injected on native Linux.
6. Discover container-visible devices with `llama-server --list-devices` and
   classify them with `vulkaninfo`. Reject software implementations and prefer
   one discrete GPU so an iGPU cannot throttle an automatic split. Preserve
   `VULKAN_DEVICES` as the explicit single/multi-GPU override. Never assume host
   PCI numbering equals container Vulkan numbering.
7. Keep one managed server service and save its exact command and selected
   devices in `.state/active.json`.
8. Redact `HF_TOKEN` from printed Compose config and never use it as a build arg.

## Common workflows

```bash
./rc fetch                    # materialize the pinned source
./rc update                   # advance forks.json; review before committing
./rc build --print            # inspect resolved Bake inputs
./rc build                    # build, cache, date-tag, and alias
./rc devices                  # inspect Vulkan devices visible in the image
./rc config [start options]   # inspect resolved runtime
./rc start [start options]    # recreate the one managed server
./rc status | ./rc logs | ./rc stop
python3 scripts/benchmark.py # cold prompt/generation sweep; writes RESULTS.json
```

For runtime failures inspect, in order: `.state/active.json`,
`.state/vulkan-devices.json`, `./rc status`, and `docker logs
llama-docker-16g-llama-server-1`. For AMD visibility check `/dev/dri` and render
group access. For NVIDIA visibility check Docker's `nvidia` runtime and that the
graphics driver capability is present.

The default Qwen profile is close to a 16 GiB VRAM limit. Changes to context,
KV types, MTP, batch sizes, or device count require a real load test and memory
measurement before changing defaults.

The default is text-only and passes `--no-mmproj`; `-hf` otherwise discovers,
downloads, and GPU-offloads a BF16 vision projector. Users can append
`--mmproj-auto` through `--extra-args` when they need vision.

