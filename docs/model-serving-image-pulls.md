# How a GPU node gets the serving runtime image

A predictor runs the `llm-d-cuda` runtime (vLLM and its CUDA stack), about 6.6 GB compressed and 15 GB unpacked. On a pool that scales from zero every serve starts on a node that has never seen it.

## What the platform does

- **A fast-to-pull variant.** The well-known `LLMInferenceServiceConfig`s run `gsoci.azurecr.io/giantswarm/llm-d-fast/llm-d-cuda`: the upstream image re-layered by [giantswarm/llm-d](https://github.com/giantswarm/llm-d) into zstd layers of at most 1.2 GB, which containerd pulls on several streams.
- **A pre-pull gated on the GPU.** `modelServing.prepull` renders a DaemonSet that pulls `modelServing.prepull.images` (the storage-initializer first, then the runtime) on every pool node once it carries `modelServing.prepull.gpuReadyLabel` (`nvidia.com/gpu.count`), so the GPU operator's pulls finish first and the runtime pull overlaps the weight download ([#545](https://github.com/giantswarm/agent-platform/issues/545), [#737](https://github.com/giantswarm/agent-platform/issues/737), [#807](https://github.com/giantswarm/agent-platform/issues/807)).
- **The download at boot.** A pool node created by cluster-manager fetches the same images into containerd's content store while it joins (gpu-node-pool's `pool.prefetchImages`, from `modelServing.prepull.images`), without unpacking; the pre-pull then only unpacks ([#812](https://github.com/giantswarm/agent-platform/issues/812)).

## Lazy pulling: evaluated, not adopted

The SOCI and stargz snapshotters were measured on cold g6 and g6e nodes in [#801](https://github.com/giantswarm/agent-platform/issues/801). Both start the container earlier, but vLLM then reads gigabytes of libraries through FUSE, so node Ready → predictor Ready got slower on every node type (SOCI +4–22 %, stargz +32–59 %). None is worth a node-level snapshotter on the serving path.

## Revisit when

- vLLM's import path becomes much smaller than the image: a slim runtime would cut the bytes read at start, and lazy pulling would then avoid most of the download.
- Nodes have the image cached, for example a node image with the runtime baked in.
