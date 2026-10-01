# Adding a serving preset

A serving preset is one recipe for serving one model on the platform: the model, the vLLM arguments, the resources and the memory it needs. The chart ships every file under [`helm/agent-platform-connectivity/files/model-serving/presets/`](../helm/agent-platform-connectivity/files/model-serving/presets/) as a preset ConfigMap, model-manager lists and loads them, and the portal's fit check sizes a GPU pool from them. An installation adds its own through `modelServing.presets` and drops shipped ones through `modelServing.shippedPresets`; this page is about the shipped set.

A shipped preset is a promise that the model serves on the hardware its description names *and* that it answers well enough to be worth serving. The render checks below hold the first; the benchmark holds the second.

## The preset file

One `ServingPreset` per file, the file named after `metadata.name`. The schema is [`serving-preset.schema.json`](../helm/agent-platform-connectivity/files/model-serving/serving-preset.schema.json); the existing presets are the examples. What a reviewer looks for:

- **`spec.description`** names the hardware the recipe was tuned on (GPU, instance type), the context length, the concurrency and the thinking default.
- **`spec.model`**: `id` is the Hugging Face repository, `storageUri` the signed model image (`oci://gsoci.azurecr.io/giantswarm/models/…`) or `hf://` for the Hub path; `capabilities` are informational tags (`chat`, `tools`, `reasoning`, `vision`, …); `tools` and `reasoning` are checked against the arguments' parsers.
- **`spec.args`** are complete and literal. The runtime template re-parses every argument through a shell, so a JSON value is single-quoted inside one argument (`"--default-chat-template-kwargs='{\"enable_thinking\": false}'"`). `--tensor-parallel-size` equals `spec.resources.gpus`.
- **`spec.requirements`**: `weightsGiB` is the checkpoint's size on the Hub, rounded up (at most 15 % above it); `overheadGiB` the KV cache, activations and runtime overhead at the preset's context and concurrency.
- **`spec.split.env`** is environment for a split placement only (one model tensor parallel across the nodes of a fast link): model-manager adds it to the leader and the workers after `modelServing.fastLinks[].env`, never to a single-node pod. It carries what was measured on a split alone, such as an all-reduce setting; environment for every placement goes in `spec.env`.
- **The model family.** Tool-call and reasoning parsers are facts of the model's architecture. A preset of a family [`model-families.yaml`](../helm/agent-platform-connectivity/files/model-serving/model-families.yaml) lacks needs its row first.

## The render checks

```sh
make verify-serving-slice    # schema keys, parsers of the family, arguments through the template's eval, GPUs = tensor parallelism
make verify-preset-weights   # weightsGiB against the Hub's size of spec.model.id (network)
make verify-wiring           # the count of shipped presets: raise it with the new file
make verify-gpu-pool verify-model-images
```

`make verify-all` runs them all, as CI does.

## The benchmark

Adding a preset, or materially changing one (model, revision, quantization, parsers, thinking default), comes with a run of the team's comparable model benchmark, `kubernetes-cka-v2` in giantswarm/agent-testing, against that preset **as the platform serves it**. Vendor benchmarks measure a different serving setup; the benchmark measures this recipe on this runtime, graded the same way as every preset already shipped.

- **Run it** with agent-testing's `scripts/benchmark_preset.py <preset>` on an installation (or the agentlab) that serves the branch's chart: it loads the preset through model-manager, runs the 100 questions over three epochs through the models Gateway, records the serving provenance (Hugging Face revision, preset, chart version, runtime image, accelerator, thinking) and unloads it. The experiment's README (`experiments/kubernetes-cka-v2/README.md` in agent-testing) is the procedure; its contract (questions, prompt, generation, judge) is fixed, so the score is comparable with every other run of the same task version and judge.
- **Keep it**: commit the eval log and the regenerated leaderboard to agent-testing (`results/kubernetes-cka-v2/`).
- **State it in the PR**: the mean score, the range (`epoch_min` to `epoch_max`), the judge, and a link to the committed result. The leaderboard puts it next to the shipped presets.

A preset the benchmark cannot run yet (no hardware of its class reachable) says so in the PR, and the score follows on the tracking issue before the preset is recommended anywhere.
