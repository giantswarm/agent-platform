# How a GPU node gets the serving runtime image

A predictor runs the `llm-d-cuda` runtime (vLLM and its CUDA stack): 6.6 GB compressed and about 15 GB unpacked in the `llm-d-fast/` variant the slice uses. On a pool that scales from zero, every serve starts on a node that has never seen that image. This page describes how the image reaches the node, and why containerd still pulls and unpacks it whole instead of lazily.

## What the platform does

- **A fast-to-pull variant.** The well-known `LLMInferenceServiceConfig`s run `gsoci.azurecr.io/giantswarm/llm-d-fast/llm-d-cuda`. It is the upstream image re-layered by [giantswarm/llm-d](https://github.com/giantswarm/llm-d) into zstd layers of at most 1.2 GB, so containerd pulls on several streams and decompresses faster than gzip.
- **A pre-pull gated on the GPU.** `modelServing.prepull` renders a DaemonSet that pulls the runtime on every pool node once GPU feature discovery has labelled it. The GPU operator's own pulls therefore finish first, and the runtime pull overlaps the weight download ([#545](https://github.com/giantswarm/agent-platform/issues/545), [#737](https://github.com/giantswarm/agent-platform/issues/737)).

## Lazy pulling: evaluated, not adopted

Lazy pulling mounts the image's layers over FUSE and fetches only the files the container reads, so the container starts before the image is on the node. The [SOCI snapshotter](https://github.com/awslabs/soci-snapshotter) does it with an index published beside the unchanged gzip image (SOCI index manifest v2). The [stargz snapshotter](https://github.com/containerd/stargz-snapshotter) does it with eStargz-encoded layers. Both were measured against the current setup on cold nodes in [#801](https://github.com/giantswarm/agent-platform/issues/801), in four configurations:

- **baseline**: overlayfs and the `llm-d-fast/` image;
- **SOCI**: SOCI v0.16.1, SOCI index v2, background fetch on;
- **stargz**: stargz v0.18.2, an eStargz image optimized on the runtime's import path;
- **SOCI parallel**: SOCI's parallel pull-and-unpack mode on the `llm-d-fast/` image, not lazy.

Each snapshotter was installed at node boot as containerd's CRI snapshotter. Every run served `qwen3-5-4b-hf` from `hf://` without a cache claim; times are seconds from node Ready.

| node | variant | runtime pull | predictor's main container starts | vLLM start | node Ready → predictor Ready | first request |
|---|---|---|---|---|---|---|
| g6.xlarge | baseline | 188 | 233 | 258 | **491** | 1.7 |
| g6.xlarge | SOCI | 16 | 137 | 375 | **512** (+4 %) | 2.7 |
| g6.xlarge | stargz | 3 | 182 | 468 | **650** (+32 %) | 2.7 |
| g6.2xlarge | baseline | 168 | 220 | 252 | **472** | 1.7 |
| g6.2xlarge | SOCI parallel | 97 | 210 | 250 | **460** (−3 %) | 1.6 |
| g6.2xlarge | SOCI | 15 | 134 | 359 | **493** (+4 %) | 2.6 |
| g6.2xlarge | stargz (two runs) | 3 | 182 | 458–477 | **640–660** (+36–40 %) | 0.5–3.2 |
| g6e.16xlarge (L40S) | baseline | 63 | 147 | 218 | **365** | 2.3 |
| g6e.16xlarge (L40S) | SOCI | 16 | 86 | 359 | **445** (+22 %) | 3.6 |
| g6e.16xlarge (L40S) | stargz | 3 | 136 | 446 | **582** (+59 %) | 4.2 |

**Lazy pulling makes a serve slower on every node type.** The container starts earlier (60–100 s with SOCI, 10–50 s with stargz), but vLLM's start then reads several gigabytes of Python, PyTorch and CUDA libraries through FUSE from the registry. That makes vLLM's start 105–140 s longer with SOCI and 205–230 s longer with stargz, more than the pull saved. A prefetch list helps less than it promises: stargz carried one, built by profiling the runtime's imports, and was still the slowest. SOCI's parallel mode is within the noise of a single run. None of the variants is worth a node-level snapshotter, its configuration on every pool node, a second image form on gsoci and FUSE on the serving path.

No form of the runtime pull can shorten node Ready → predictor Ready by 30 %, either. Remove the pull entirely and the path stays: the GPU becoming allocatable (50–65 s), the storage-initializer, the weight download and vLLM's own start (220–260 s on a fresh node).

**What the measurement found instead.** The predictor's storage-initializer image (96 MB) gates the weight download. Pulled beside the runtime, it takes 72–86 s on a g6.xlarge or g6.2xlarge; with nothing else pulling, 5–12 s. Pulling it first is [#807](https://github.com/giantswarm/agent-platform/issues/807).

## Revisit when

- vLLM's import path becomes much smaller than the image. A slim runtime would cut the bytes read at start, and lazy pulling would then avoid most of the download.
- Nodes have the image cached, for example a node image with the runtime baked in. That removes the pull without FUSE on the serving path.
