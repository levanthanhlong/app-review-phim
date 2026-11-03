"""Generate several Stable Audio 3 tracks in one process (the music stage's batch runner).

Runs with Stable Audio 3's own Python (tools/stable-audio-3/optimized/mlx/.venv):

    <sa3>/.venv/bin/python tools/sa3_batch.py --sa3 <sa3> --jobs jobs.json

jobs.json: {"model": "medium", "jobs": [{"prompt": "...", "seconds": 54.5, "seed": 7, "out": "/abs.wav"}]}

Same text-to-audio path as `sa3 --cfg 1` (8 pingpong steps, fp16 DiT, fp32 decoder), so a seed
gives the same audio as the CLI. Three phases, one model in memory at a time (like the CLI's
--free-models): encode all prompts, sample all latents (DiT loaded once per clip length), then
decode all. Prints {"sampled": out} after sampling and {"out": out, "elapsed_sec": s} when a file
is written. Exits non-zero on the first failure.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

STEPS = 8
SIGMA_MAX = 1.0
DECODERS = {"medium": "same-l", "sm-music": "same-s"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sa3", required=True, type=Path, help="stable-audio-3/optimized/mlx folder")
    parser.add_argument("--jobs", required=True, type=Path)
    args = parser.parse_args()
    sys.path[:0] = [str(args.sa3), str(args.sa3 / "scripts")]

    import mlx.core as mx
    import numpy as np
    import sa3_mlx as cli  # the CLI's own loaders and constants
    from models.defs.sa3_pipeline import (
        apply_prompt_padding,
        build_pingpong_schedule,
        load_conditioner_from_npz,
        patched_decode,
        sample_flow_pingpong,
    )
    from models.defs.t5gemma_mlx import T5Gemma
    from weights import ensure_local

    spec = json.loads(args.jobs.read_text(encoding="utf-8"))
    model = spec["model"]
    jobs = spec["jobs"]
    dtype = mx.float16

    def t_lat(seconds: float) -> int:
        return max(1, math.ceil(seconds * cli.SAMPLE_RATE / cli.SAMPLES_PER_LATENT))

    # 1. Encode every prompt, then free the text encoder.
    encoder = T5Gemma.from_npz(str(ensure_local(cli.T5GEMMA_NPZ_REL)))
    encoded = []
    for job in jobs:
        embeds, mask = encoder.encode([job["prompt"]], max_len=256)
        mx.eval(embeds, mask)
        encoded.append((embeds, mask))
    del encoder
    cli._free_to_pool()

    # 2. Sample every job's latents with the DiT (loaded once per clip length), then free it.
    ckpt = str(ensure_local(cli.DIT_CHOICES[model]["ckpt"]))
    padding_emb, secs_embedder = load_conditioner_from_npz(ckpt, prefix="cond.")
    sigmas = build_pingpong_schedule(STEPS, sigma_max=SIGMA_MAX, use_logsnr_shift=True)
    latents_by_job: dict[int, object] = {}
    dit, dit_len = None, None
    for index in sorted(range(len(jobs)), key=lambda i: t_lat(jobs[i]["seconds"])):  # fewest DiT reloads
        job, (embeds, mask) = jobs[index], encoded[index]
        length = t_lat(job["seconds"])
        if dit_len != length:
            dit = None
            cli._free_to_pool()
            dit, _ = cli.load_dit(model, T_lat=length, dtype=dtype, num_steps=STEPS)
            dit_len = length

        seconds_embed = secs_embedder(job["seconds"]).astype(dtype)
        padded = apply_prompt_padding(embeds.astype(dtype), mask, padding_emb.astype(dtype))
        cross_attn = mx.concatenate([padded, seconds_embed], axis=1)
        global_cond = seconds_embed[:, 0, :]
        mx.eval(cross_attn, global_cond)

        noise = mx.random.normal((1, 256, length), dtype=dtype, key=mx.random.key(job["seed"]))
        mx.eval(noise)

        def model_fn(x, t, dit=dit, cross_attn=cross_attn, global_cond=global_cond):  # type: ignore[no-untyped-def]
            return dit(x, t, cross_attn, global_cond, local_add_cond=None)

        latents = sample_flow_pingpong(model_fn, noise, sigmas, seed=job["seed"] + 1)
        mx.eval(latents)
        latents_by_job[index] = latents  # small (256 x T_lat): kept until the decoder runs
        print(json.dumps({"sampled": job["out"]}), flush=True)
    dit = None
    cli._free_to_pool()

    # 3. Decode everything with the decoder loaded once (never alongside the DiT: same peak RAM as the CLI).
    decoder, chunk_fn, (chunk, ovl) = cli.load_decoder(DECODERS[model], mx.float32)
    for index, job in enumerate(jobs):
        started = time.time()
        length = t_lat(job["seconds"])
        latents = latents_by_job.pop(index).astype(mx.float32)  # type: ignore[attr-defined]
        if length > chunk + 2 * ovl:
            patches = chunk_fn(decoder, latents, chunk, ovl)
        elif length % 2 == 0:
            patches = decoder(latents)
        elif length > 6:
            patches = chunk_fn(decoder, latents, 2, 2)
        else:
            patches = decoder(mx.concatenate([latents, latents[..., -1:]], axis=-1))[..., : length * 16]
        audio = patched_decode(patches, patch_size=256, channels=2)
        mx.eval(audio)
        audio_np = np.array(audio.astype(mx.float32))[0][..., : round(job["seconds"] * cli.SAMPLE_RATE)]
        cli.save_wav(job["out"], audio_np)
        print(json.dumps({"out": job["out"], "elapsed_sec": round(time.time() - started, 2)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
