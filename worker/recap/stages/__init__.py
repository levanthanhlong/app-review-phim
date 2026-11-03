"""Registry of the pipeline stages (a name missing here would report `not_implemented`)."""

from __future__ import annotations

from recap.runner import Stage
from recap.stages.assemble import AssembleStage
from recap.stages.describe import DescribeStage
from recap.stages.export import ExportStage
from recap.stages.ingest import IngestStage
from recap.stages.music import MusicStage
from recap.stages.scenes import ScenesStage
from recap.stages.script import ScriptStage
from recap.stages.transcribe import TranscribeStage
from recap.stages.tts import TTSStage

STAGES: dict[str, Stage] = {
    stage.name: stage
    for stage in (
        IngestStage(),
        TranscribeStage(),
        ScenesStage(),
        DescribeStage(),
        ScriptStage(),
        TTSStage(),
        MusicStage(),
        AssembleStage(),
        ExportStage(),
    )
}
