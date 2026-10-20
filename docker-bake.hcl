variable "BUILD_JOBS" { default = "8" }
variable "BUILD_TAG" { default = "dev" }
variable "IMAGE_REPOSITORY" { default = "ghcr.io/pdscomp/llama-docker-16g" }

group "default" { targets = ["upstream"] }

target "upstream" {
  context = "./sources/upstream"
  dockerfile = ".devops/vulkan-server.Dockerfile"
  target = "server"
  platforms = ["linux/amd64"]
  output = ["type=docker"]
  tags = ["${IMAGE_REPOSITORY}:vulkan-${BUILD_TAG}"]
  args = {
    BUILD_JOBS = BUILD_JOBS
  }
  cache-from = ["type=local,src=./.buildx-cache/upstream/vulkan"]
  cache-to = ["type=local,dest=./.buildx-cache/upstream/vulkan,mode=max"]
}

