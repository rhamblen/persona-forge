# v0.8.7 — Training doesn't start a run the card can't finish

Your build died 5 minutes in with an out-of-memory error from `TrainLoraNode`. It wasn't
the trainer, and it wasn't your dataset. **Something else was using the GPU.**

## What was actually happening

At the moment training started, the 3090 had **8.4 GB already taken** by other tenants:

| Holder | VRAM |
|---|---|
| Ollama — `minicpm-v`, loaded by another app on your LAN | 4.7 GB |
| Immich's CUDA machine-learning server | most of the rest |

Training peaks at about **17.8 GB reserved**. 17.8 + 8.4 doesn't fit in 24 GB, so it ran
until the VAE encode asked for one more 58 MB and the card said no. Because the failure
landed in the VAE rather than the training loop, it read like a trainer bug.

## The bug on our side

Persona Forge *does* free VRAM before training — but it was freeing the wrong thing. It
called "unload the Ollama model", meaning the model **we** are configured to use
(`llama3.1`). The model actually sitting in VRAM had been loaded by something else. So the
log said `unloading Ollama model {llama3.1:latest}`, reported success, and 4.7 GB never
moved.

It now asks Ollama what it's actually holding and evicts **all of it**, logging how much
came back.

## And a gate, so this can't waste 37 minutes again

After freeing, PF checks the card before committing to the run. If there isn't enough, you
get this straight away instead of an OOM traceback five minutes later:

> not enough free VRAM to train: 17.0 GB free of 25.3 GB, need ~18 GB. Roughly 8.2 GB is
> held by other processes on this GPU. Ollama still holds minicpm-v:latest (4.7 GB). Free
> the card and start the build again.

Tunable via `MIN_TRAIN_VRAM_GB` (default 18, from the measured 17.8 GB peak). Set it to `0`
to switch the gate off. If ComfyUI can't be reached the gate lets the build through rather
than blocking it — it should never be the reason a good build doesn't start.

## Before you rebuild

Your card is **clear right now** (0.5 GB used). The Ollama model has been unloaded and
Immich has released its models, so a build started now has the full 24 GB.

Worth knowing: Immich's ML server and whatever loads `minicpm-v` will both take the GPU
back when they next have work. If a build gets blocked by the new gate, that's who to look
at first.

## Upgrade notes

Automatic — no compose change, no migration.

**Image:** `ghcr.io/rhamblen/persona-forge:0.8.7`

Full detail in [`CHANGELOG.md`](CHANGELOG.md).
