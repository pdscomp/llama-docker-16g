# syntax=docker/dockerfile:1
ARG UBUNTU_VERSION=24.04
ARG NODE_VERSION=24

FROM node:${NODE_VERSION} AS web
WORKDIR /web/tools/ui
COPY tools/ui/package.json tools/ui/package-lock.json ./
RUN npm ci
COPY tools/ui/ ./
RUN LLAMA_BUILD_NUMBER=0 npm run build

FROM ubuntu:${UBUNTU_VERSION} AS build
ARG BUILD_JOBS=8
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc-14 g++-14 build-essential cmake ninja-build git python3 pkg-config \
    libssl-dev ca-certificates ccache libvulkan-dev glslc spirv-headers && \
    rm -rf /var/lib/apt/lists/*
ENV CC=gcc-14 CXX=g++-14 CCACHE_DIR=/ccache
WORKDIR /src
COPY . .
COPY --from=web /web/tools/ui/dist tools/ui/dist
RUN --mount=type=cache,target=/ccache,id=llama-cpp-upstream-vulkan-ccache \
    cmake -S . -B /build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DGGML_VULKAN=ON \
      -DGGML_NATIVE=OFF \
      -DGGML_BACKEND_DL=ON \
      -DGGML_CPU_ALL_VARIANTS=ON \
      -DLLAMA_BUILD_TESTS=OFF \
      -DLLAMA_BUILD_SERVER=ON \
      -DLLAMA_OPENSSL=ON \
      -DCMAKE_REQUIRE_FIND_PACKAGE_OpenSSL=ON \
      -DCMAKE_C_COMPILER_LAUNCHER=ccache \
      -DCMAKE_CXX_COMPILER_LAUNCHER=ccache && \
    cmake --build /build --parallel "${BUILD_JOBS}" --target llama-server
RUN mkdir /out && cp /build/bin/llama-server /out/ && \
    find /build -name '*.so*' -exec cp -P {} /out/ \;

FROM ubuntu:${UBUNTU_VERSION} AS server
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libssl3t64 ca-certificates curl ffmpeg \
    libvulkan1 mesa-vulkan-drivers vulkan-tools && \
    rm -rf /var/lib/apt/lists/*
COPY --from=build /out/ /app/
WORKDIR /app
ENV LD_LIBRARY_PATH=/app \
    HOME=/runtime/home \
    LLAMA_ARG_HOST=0.0.0.0 \
    LLAMA_ARG_PORT=8080
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20m --retries=3 \
    CMD curl --fail --silent http://localhost:8080/health || exit 1
ENTRYPOINT ["/app/llama-server"]

