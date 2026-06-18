"""ContainerAgentExecutor — runs agent inside per-user Docker container.

Used when CONTAINER_MODE=true. Wraps ContainerBridge lifecycle (connect,
run_and_stream), feeds JSON dicts through adapt_container_message(), and
emits InternalEvent to the shared event pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.agent.options import build_agent_options
from src.agent.prompt import build_history_prompt, format_first_message_prompt
from src.container_bridge import ContainerBridge
from src.event_pipeline import EventContext, _finish_task, handle_task_error
from src.observation import ToolObserver

logger = logging.getLogger(__name__)


class ContainerAgentExecutor:
    """Execute agent tasks inside a per-user Docker container via WebSocket bridge."""

    def __init__(
        self,
        user_id: str,
        session_agents: dict[str, dict[str, Any]],
        buffer: Any,
        session_store: Any,
        skill_manager: Any,
        obs_store: Any,
        db: Any,
        container_manager: Any,
        user_workspace_dir_fn: Any,
        snapshot_output_files_fn: Any,
        get_cached_skills_fn: Any,
        get_cached_system_prompt_fn: Any,
        resolve_user_language_fn: Any,
        load_instinct_context_fn: Any,
    ) -> None:
        self.user_id = user_id
        self._session_agents = session_agents
        self._buffer = buffer
        self._session_store = session_store
        self._skill_manager = skill_manager
        self._obs_store = obs_store
        self._db = db
        self._cm = container_manager
        self._user_workspace_dir = user_workspace_dir_fn
        self._snapshot_output_files = snapshot_output_files_fn
        self._get_cached_skills = get_cached_skills_fn
        self._get_cached_system_prompt = get_cached_system_prompt_fn
        self._resolve_user_language = resolve_user_language_fn
        self._load_instinct_context = load_instinct_context_fn

    async def run(
        self,
        prompt: str,
        session_id: str,
        is_continuation: bool = False,
        attached_files: list[str] | None = None,
        language: str | None = None,
    ) -> None:
        """Execute an agent task inside the user's Docker container."""
        from src.agent_logger import AgentLogger  # noqa: PLC0415

        bridge = None
        agent_log = None

        try:
            t_start = time.monotonic()
            container_url = self._cm.ensure_container(self.user_id)
            logger.info(
                "Container task: user=%s session=%s url=%s continuation=%s",
                self.user_id, session_id, container_url, is_continuation,
            )

            # ── Skills + system prompt caching ────────────────────
            agent_state = self._session_agents.get(session_id)
            if agent_state is not None:
                cached_skills = agent_state.get("skills", {})
                cached_sp = agent_state.get("system_prompt", "")
                options = await build_agent_options(
                    self.user_id,
                    skills_override=cached_skills,
                    system_prompt_override=cached_sp,
                    language=language,
                    container_mode=True,
                )
                self._session_agents[session_id]["last_used"] = time.time()
            else:
                options = await build_agent_options(
                    self.user_id, language=language, container_mode=True,
                )
                skills = await self._get_cached_skills(self.user_id, session_id)
                resolved_lang = await self._resolve_user_language(self.user_id, language)
                instinct_ctx = await self._load_instinct_context(prompt, self._db)
                self._get_cached_system_prompt(
                    self.user_id, skills, self._user_workspace_dir(self.user_id),
                    resolved_lang, session_id, instinct_context=instinct_ctx,
                )
                if session_id not in self._session_agents:
                    self._session_agents[session_id] = {}
                self._session_agents[session_id]["last_used"] = time.time()

            agent_log = AgentLogger(user_id=self.user_id)
            agent_log.start_session(session_id, user_message=prompt)

            tool_observer = ToolObserver(self._obs_store, session_id, self.user_id)
            generated_files: list[dict[str, Any]] = []
            tool_use_names: dict[str, str] = {}

            ctx = EventContext(
                user_id=self.user_id,
                session_id=session_id,
                buffer=self._buffer,
                observer=tool_observer,
                skill_manager=self._skill_manager,
                generated_files=generated_files,
            )

            # ── Bridge setup ──────────────────────────────────────
            agent_state = self._session_agents.get(session_id, {})
            bridge = agent_state.get("bridge")
            if bridge is not None:
                bridge.container_url = container_url
            else:
                options_dict = {
                    "model": options.model,
                    "system_prompt": options.system_prompt,
                    "allowed_tools": options.allowed_tools,
                    "disallowed_tools": list(options.disallowed_tools),
                    "max_turns": options.max_turns,
                    "permission_mode": options.permission_mode,
                    "mcp_servers": options.mcp_servers,
                    "env": options.env,
                    "include_partial_messages": options.include_partial_messages,
                    "resume_session_id": options.resume_session_id,
                    "max_buffer_size": options.max_buffer_size,
                    "cwd": options.cwd,
                }
                bridge = ContainerBridge(
                    container_url=container_url,
                    session_id=session_id,
                    user_id=self.user_id,
                    buffer=self._buffer,
                    session_store=self._session_store,
                    skill_manager=self._skill_manager,
                    ctx=ctx,
                    model=options.model,
                    tool_use_names=tool_use_names,
                )
                await bridge.connect()

            # ── Build prompt ──────────────────────────────────────
            workspace = self._user_workspace_dir(self.user_id)
            pre_scan_snapshot = self._snapshot_output_files(workspace, session_id)
            if is_continuation:
                if self._session_store is not None:
                    history = await self._session_store.get_session_history(
                        self.user_id, session_id, after_index=0,
                    )
                else:
                    history = await self._buffer.get_history(
                        session_id, after_index=0, user_id=self.user_id,
                    )
                prompt_text = build_history_prompt(
                    history, prompt, language=language, session_id=session_id,
                )
            else:
                prompt_text = format_first_message_prompt(
                    prompt, attached_files, language, session_id,
                )

            # ── Run ────────────────────────────────────────────────
            try:
                await bridge.run_and_stream(prompt_text, options_dict)
            except ConnectionError:
                logger.warning(
                    "Container bridge connection dead for session %s, reconnecting...",
                    session_id,
                )
                await bridge.disconnect()
                await bridge.connect()
                await bridge.run_and_stream(prompt_text, options_dict)

            self._session_agents[session_id]["bridge"] = bridge

            await _finish_task(
                session_id=session_id,
                user_id=self.user_id,
                buffer=self._buffer,
                workspace=workspace,
                session_store=self._session_store,
                skill_manager=self._skill_manager,
                obs_store=self._obs_store,
                agent_log=agent_log,
                pre_scan_snapshot=pre_scan_snapshot or set(),
                result_event=bridge._result if bridge else None,
                language=language,
            )

        except Exception as exc:
            if isinstance(exc, asyncio.CancelledError) and bridge is not None:
                try:
                    await bridge.send_cancel()
                except Exception:
                    pass
            await handle_task_error(
                exc,
                session_id=session_id,
                user_id=self.user_id,
                buffer=self._buffer,
                obs_store=self._obs_store,
                agent_log=agent_log,
                cleanup_fn=None,
            )
