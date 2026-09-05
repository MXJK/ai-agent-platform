from __future__ import annotations

import hashlib

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from ai_agent_platform.cogent.memory.service import MemoryConflictError, MemoryService
from ai_agent_platform.core import Settings, request_user_id
from ai_agent_platform.services import WorkspaceAccessDeniedError
from ai_agent_platform.services.workspace_service import WorkspaceNotFoundError


class FileMemoryWrite(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    scope: str = Field(pattern=r"^(user|project)$")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    type: str = Field(pattern=r"^(user|feedback|project|reference)$")
    description: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=24000)
    expected_hash: str | None = Field(default=None, max_length=64)


class FileMemoryDelete(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=128)
    scope: str = Field(pattern=r"^(user|project)$")
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,79}$")
    expected_hash: str | None = Field(default=None, max_length=64)


class MaintenanceRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    operation: str = Field(pattern=r"^(extract|consolidate)$")
    scope: str | None = Field(default=None, pattern=r"^(user|project)$")


def create_memory_router(session_service, memory_service: MemoryService,
                         workspace_service, workspace_access_service,
                         query_service, settings: Settings) -> APIRouter:
    router = APIRouter()

    def context(request: Request, workspace_id: str, *, write: bool=False):
        actor = request_user_id(request, settings)
        try:
            workspace = workspace_service.get(workspace_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(status_code=404, detail='workspace not found') from exc
        role = 'admin'
        if settings.auth_mode != 'disabled':
            try:
                workspace_access_service.authorize(
                    workspace_id=workspace_id, actor_user_id=actor,
                    required_role='editor' if write else 'viewer')
            except WorkspaceAccessDeniedError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            role = workspace_access_service.role_for(
                workspace_id=workspace_id, actor_user_id=actor) or 'viewer'
        return actor, workspace.root_path, role

    @router.get('/memory/files')
    def list_files(request: Request, workspace_id: str,
                   scope: str | None=Query(default=None, pattern=r"^(user|project)$")):
        actor, root, role = context(request, workspace_id)
        return {'files': [_public_file(item) for item in memory_service.list_files(
            workspace_root=root, actor_user_id=actor, role=role, scope=scope)]}

    @router.put('/memory/files')
    @router.post('/memory/files', status_code=status.HTTP_201_CREATED)
    def write_file(body: FileMemoryWrite, request: Request):
        actor, root, role = context(request, body.workspace_id, write=True)
        try:
            item = memory_service.upsert_file(
                workspace_root=root, actor_user_id=actor, role=role,
                scope=body.scope, name=body.name, kind=body.type,
                description=body.description, body=body.body,
                expected_hash=body.expected_hash,
                must_not_exist=request.method == 'POST')
        except MemoryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400,
                                detail=str(exc)) from exc
        return _public_file(item)

    @router.delete('/memory/files', status_code=status.HTTP_204_NO_CONTENT)
    def delete_file(body: FileMemoryDelete, request: Request):
        actor, root, role = context(request, body.workspace_id, write=True)
        try:
            deleted = memory_service.delete_file(
                workspace_root=root, actor_user_id=actor, role=role,
                scope=body.scope, name=body.name,
                expected_hash=body.expected_hash)
        except MemoryConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(status_code=403 if isinstance(exc, PermissionError) else 400,
                                detail=str(exc)) from exc
        if not deleted:
            raise HTTPException(status_code=404, detail='memory file not found')

    @router.get('/memory/index')
    def read_index(request: Request, workspace_id: str,
                   scope: str=Query(pattern=r"^(user|project)$")):
        actor, root, role = context(request, workspace_id)
        try:
            return memory_service.read_index(workspace_root=root,
                actor_user_id=actor, role=role, scope=scope)
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.post('/memory/maintenance', status_code=status.HTTP_202_ACCEPTED)
    def start_maintenance(body: MaintenanceRequest, request: Request):
        actor = request_user_id(request, settings)
        session = session_service.get_session(body.conversation_id)
        if session.user_id != actor:
            raise HTTPException(status_code=403, detail='conversation access denied')
        record = query_service.get_latest_run_for_actor(body.conversation_id, actor)
        context(request, record.workspace_id, write=True)
        if body.operation == 'extract':
            messages = session_service.list_messages(body.conversation_id)
            user = next((item.content for item in reversed(messages) if item.role == 'user'), '')
            answer = next((item.content for item in reversed(messages) if item.role == 'assistant'), '')
            memory_service.schedule_extract(record, user, answer)
        else:
            memory_service.schedule_consolidate(
                record, force=True, scope=body.scope)
        return {'operation': body.operation, 'scope': body.scope,
                'status': 'scheduled'}

    @router.get('/memory/maintenance')
    def maintenance_status(request: Request, workspace_id: str):
        actor, root, _ = context(request, workspace_id)
        jobs = []
        for record in memory_service.store.list_recent(limit=200):
            state = record.runtime_state
            if (state.get('internal_maintenance') and record.workspace_root == root
                    and state.get('owner') == actor):
                jobs.append({'id': record.run_id, 'operation': state.get('operation'),
                             'status': record.status, 'boundary': state.get('boundary'),
                             'error': record.error})
        return {'jobs': jobs[:50]}

    @router.get('/memory/conversations/search')
    def search_conversations(request: Request, q: str=Query(default='', max_length=500),
                             workspace_id: str | None=None,
                             session_id: str | None=None, limit: int=10):
        actor = request_user_id(request, settings)
        return {'hits': [item.__dict__ for item in session_service.search_conversations(
            user_id=actor, query=q, workspace_id=workspace_id,
            session_id=session_id, limit=max(1, min(limit, 50)))]}

    return router


def create_retired_memory_router() -> APIRouter:
    router = APIRouter()

    def retired():
        raise HTTPException(status_code=410,
            detail='Database memory was retired; use /memory/files and /memory/index')

    router.add_api_route('/users/me/memories', retired, methods=['GET', 'POST', 'PATCH', 'DELETE'])
    router.add_api_route('/users/me/memories/{rest:path}', retired,
                         methods=['GET', 'POST', 'PATCH', 'DELETE'])
    router.add_api_route('/users/me/memory-settings', retired, methods=['GET', 'PATCH'])
    router.add_api_route('/users/me/memory-scenes', retired, methods=['GET'])
    router.add_api_route('/users/me/profile', retired, methods=['GET'])
    router.add_api_route('/users/me/profile/{rest:path}', retired, methods=['GET', 'POST'])
    router.add_api_route('/workspaces/{workspace_id}/memories', retired,
                         methods=['GET', 'POST', 'PATCH', 'DELETE'])
    router.add_api_route('/workspaces/{workspace_id}/memories/{rest:path}', retired,
                         methods=['GET', 'POST', 'PATCH', 'DELETE'])
    router.add_api_route('/workspaces/{workspace_id}/memory-settings', retired,
                         methods=['GET', 'PATCH'])
    router.add_api_route('/workspaces/{workspace_id}/memory-jobs', retired,
                         methods=['GET'])
    return router


def _public_file(item):
    raw = item['text'].encode('utf-8')
    return {key: item[key] for key in ('id', 'scope', 'name', 'description', 'type',
                                       'text', 'mtime_ms')} | {
        'sha256': hashlib.sha256(raw).hexdigest()
    }


__all__ = ['create_memory_router', 'create_retired_memory_router']
