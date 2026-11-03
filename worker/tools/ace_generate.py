"""Generate one instrumental track with ACE-Step 1.5 (the music stage's fallback engine).

Runs with ACE-Step's own Python (tools/ACE-Step-1.5/.venv), not the worker's:

    <ace>/.venv/bin/python tools/ace_generate.py --project-root <ace> --caption "..." \
        --seconds 60 --seed 123 --out /abs/path/track.wav

Exits non-zero (message on stderr) when generation fails.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--config", default="acestep-v15-turbo")
    parser.add_argument("--caption", required=True)
    parser.add_argument("--seconds", required=True, type=float)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    from acestep.handler import AceStepHandler
    from acestep.inference import GenerationConfig, GenerationParams, generate_music
    from acestep.llm_inference import LLMHandler

    dit = AceStepHandler()
    dit.initialize_service(project_root=str(args.project_root), config_path=args.config, device="mps")
    llm = LLMHandler()  # not initialized: the caption goes straight to the DiT (no LM planning)
    params = GenerationParams(
        caption=args.caption,
        instrumental=True,
        lyrics="[Instrumental]",
        duration=args.seconds,
        seed=args.seed,
        thinking=False,
        use_cot_metas=False,
        use_cot_caption=False,
        use_cot_language=False,
    )
    config = GenerationConfig(batch_size=1, audio_format="wav", use_random_seed=False, seeds=[args.seed])
    with tempfile.TemporaryDirectory() as tmp:
        result = generate_music(dit, llm, params, config, save_dir=tmp)
        if not result.success or not result.audios:
            print(f"ACE-Step lỗi: {result.error}", file=sys.stderr)
            return 1
        shutil.move(result.audios[0]["path"], args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
