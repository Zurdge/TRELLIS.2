from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PipelineType = Literal["512", "1024", "1024_cascade", "1536_cascade"]
ACTUAL_RESOLUTIONS: dict[PipelineType, tuple[int, ...]] = {
    "512": (512,),
    "1024": (1024,),
    "1024_cascade": (1024,),
    "1536_cascade": (1024, 1152, 1280, 1408, 1536),
}


def valid_actual_resolution(mode: PipelineType, resolution: object) -> bool:
    return type(resolution) is int and resolution in ACTUAL_RESOLUTIONS[mode]


class GenerationSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    pipeline_type: PipelineType = "512"
    seed: int = Field(ge=0, le=2_147_483_647)
    sampling_steps: int = Field(ge=8, le=24)
    texture_size: Literal[1024, 2048]
    decimation_target: int = Field(ge=50_000, le=500_000)

    @field_validator("texture_size", mode="before")
    @classmethod
    def texture_size_must_be_integer(cls, value):
        if type(value) is not int:
            raise ValueError("Expected an integer")
        return value


LEGACY_SETTINGS = GenerationSettings(seed=42, sampling_steps=12, texture_size=1024, decimation_target=100_000)
DEFAULT_SETTINGS = LEGACY_SETTINGS


def settings_part_schema():
    schema = GenerationSettings.model_json_schema()
    del schema["required"]
    for key, value in DEFAULT_SETTINGS.model_dump().items():
        schema["properties"][key]["default"] = value
    return {
        "type": "string",
        "description": "JSON object with optional settings applied to every image. Limited to 4096 bytes.",
        "contentMediaType": "application/json",
        "contentSchema": schema,
        "examples": ['{"pipeline_type":"1024_cascade","seed":0,"sampling_steps":16,"texture_size":2048,"decimation_target":250000}'],
    }
