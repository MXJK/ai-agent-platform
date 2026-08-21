from __future__ import annotations

from pydantic import BaseModel, Field


class SkillUpsertRequest(BaseModel):
    content: str = Field(min_length=1, max_length=65_536)
    enabled: bool = True


class SkillEnabledRequest(BaseModel):
    enabled: bool


class SkillCommandResponse(BaseModel):
    name: str
    description: str
    usage: str | None
    aliases: list[str]


class SkillResponse(BaseModel):
    name: str
    qualified_name: str
    description: str
    source: str
    path: str
    enabled: bool
    editable: bool
    required_tools: list[str]
    command: SkillCommandResponse | None
    content: str | None


class SkillDiagnosticResponse(BaseModel):
    severity: str
    code: str
    source: str
    path: str
    message: str


class SkillRegistryResponse(BaseModel):
    root: str
    writable: bool
    skills: list[SkillResponse]
    diagnostics: list[SkillDiagnosticResponse]
