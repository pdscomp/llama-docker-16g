# Agent guide

Read this file before modifying the repository. This is an upstream-only
`llama.cpp` Vulkan harness for mixed AMD Radeon and NVIDIA Linux hosts. It builds
one image family, `llama-cpp-upstream-vulkan`, and manages one Compose service.

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

## Invariants

1. Build from the exact commit in `forks.json` and put the source SHA in dated
   image tags and OCI labels.
2. Move `vulkan-latest`/`latest` only after a successful build. Keep dated tags
   for rollback.
3. Keep Buildx cache exports under `.buildx-cache/upstream/vulkan`.
4. Run as the caller's UID/GID and share the host HF cache. Do not bake models,
   tokens, or host paths into the image.
5. Expose `/dev/dri` when present. Enable NVIDIA runtime only when both the
   runtime and an NVIDIA GPU are detected. Use
   `NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics` so Vulkan ICD support is
   injected.
6. Discover container-visible devices with `llama-server --list-devices`, reject
   software Vulkan implementations, and pass explicit `VulkanN` names. Never
   assume host PCI numbering equals container Vulkan numbering.
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
```

For runtime failures inspect, in order: `.state/active.json`,
`.state/vulkan-devices.json`, `./rc status`, and `docker logs
llama-docker-16g-llama-server-1`. For AMD visibility check `/dev/dri` and render
group access. For NVIDIA visibility check Docker's `nvidia` runtime and that the
graphics driver capability is present.

The default Qwen profile is close to a 16 GiB VRAM limit. Changes to context,
KV types, MTP, batch sizes, or device count require a real load test and memory
measurement before changing defaults.

