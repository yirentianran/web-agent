"""LocalAgentExecutor — runs agent via ClaudeSDKClient directly in-process.

Used when CONTAINER_MODE=false. Wraps ClaudeSDKClient lifecycle (connect,
query, receive_response), feeds SDK messages through adapt_sdk_message(),
and emits InternalEvent to the shared event pipeline.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from pathlib import Path
from typing import Any

from claude_agent_sdk import CLIConnectionError, ClaudeSDKClient
from claude_agent_sdk.types import (
    ClaudeAgentOptions,
    HookContext,
    HookInput,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from src.agent.adapters.sdk import adapt_sdk_message
from src.agent.options import AgentOptions, build_agent_options
from src.agent.prompt import build_history_prompt, format_first_message_prompt
from src.event_pipeline import EventContext, _finish_task, handle_task_error, process_event
from src.observation import ToolObserver
from src.security.enforcer import SecurityEnforcer
from src.security.filters import BashCommandFilter, FileAccessFilter
from src.security.rate_limiter import tool_call_rate_limiter
from src.workspace_enforcement import (
    check_bash_command_for_external_writes,
    is_path_within_user_dir,
    normalize_write_path,
    rewrite_path_to_workspace,
)

logger = logging.getLogger(__name__)


class LocalAgentExecutor:
    """Execute agent tasks via ClaudeSDKClient in the main process."""

    def __init__(
        self,
        user_id: str,
        session_agents: dict[str, dict[str, Any]],
        buffer: Any,
        session_store: Any,
        skill_manager: Any,
        obs_store: Any,
        db: Any,
        pending_answers: dict[str, asyncio.Future],
        user_workspace_dir_fn: Any,
        user_data_dir_fn: Any,
        snapshot_output_files_fn: Any,
        get_cached_skills_fn: Any,
        get_cached_system_prompt_fn: Any,
        resolve_user_language_fn: Any,
        load_instinct_context_fn: Any,
        cleanup_fn: Any,
    ) -> None:
        self.user_id = user_id
        self._session_agents = session_agents
        self._buffer = buffer
        self._session_store = session_store
        self._skill_manager = skill_manager
        self._obs_store = obs_store
        self._db = db
        self._pending_answers = pending_answers
        self._user_workspace_dir = user_workspace_dir_fn
        self._user_data_dir = user_data_dir_fn
        self._snapshot_output_files = snapshot_output_files_fn
        self._get_cached_skills = get_cached_skills_fn
        self._get_cached_system_prompt = get_cached_system_prompt_fn
        self._resolve_user_language = resolve_user_language_fn
        self._load_instinct_context = load_instinct_context_fn
        self._cleanup = (
            functools.partial(cleanup_fn, session_agents=self._session_agents)
            if cleanup_fn is not None
            else None
        )

    async def run(
        self,
        prompt: str,
        session_id: str,
        is_continuation: bool = False,
        attached_files: list[str] | None = None,
        language: str | None = None,
    ) -> None:
        """Execute an agent task via ClaudeSDKClient."""
        from src.agent_logger import AgentLogger  # noqa: PLC0415
        from src.constants import DISABLED_TOOLS  # noqa: PLC0415
        import uuid  # noqa: PLC0415

        agent_log = AgentLogger(user_id=self.user_id)
        agent_log.start_session(session_id, user_message=prompt)

        workspace = self._user_workspace_dir(self.user_id)
        user_dir = self._user_data_dir(self.user_id)
        enforcer = SecurityEnforcer(
            user_id=self.user_id, workspace=workspace, user_dir=user_dir,
        )

        # ── can_use_tool callback ──────────────────────────────────
        async def can_use_tool_cb(
            tool_name: str,
            tool_input: dict[str, Any],
            _ctx: ToolPermissionContext,
        ) -> PermissionResult:
            if not tool_call_rate_limiter.allow(session_id):
                return PermissionResultDeny(
                    message="Tool call rate limit exceeded. Please wait before making more tool calls.",
                )
            if tool_name in DISABLED_TOOLS:
                return PermissionResultDeny(
                    message=f"{tool_name} is disabled. Use MCP fetch tools instead.",
                )
            if tool_name == "Write":
                file_path = str(tool_input.get("file_path", ""))
                if file_path and not is_path_within_user_dir(file_path, self.user_id):
                    return PermissionResultDeny(
                        message=f"File path '{file_path}' is outside the user directory. "
                        f"All files must be saved within the workspace or user data directory.",
                    )
            if tool_name == "Bash":
                cmd = str(tool_input.get("command", ""))
                error = check_bash_command_for_external_writes(cmd, workspace, user_dir)
                if error:
                    return PermissionResultDeny(message=error)
                allowed, reason = enforcer.check_bash(cmd)
                if not allowed:
                    return PermissionResultDeny(message=reason)
            if tool_name == "Read":
                file_path = str(tool_input.get("file_path", ""))
                if file_path:
                    allowed, reason = enforcer.check_read_path(file_path)
                    if not allowed:
                        return PermissionResultDeny(message=reason)

            agent_log.tool_call(tool_name, tool_input, session_id=session_id)

            # AskUserQuestion — wait for WebSocket answer
            if tool_name == "AskUserQuestion":
                await self._buffer.add_message(
                    session_id,
                    {
                        "type": "tool_use",
                        "name": "AskUserQuestion",
                        "id": f"ask_{uuid.uuid4().hex[:8]}",
                        "input": tool_input,
                    },
                    self.user_id,
                )
                answer_future: asyncio.Future = asyncio.get_event_loop().create_future()
                self._pending_answers[session_id] = answer_future
                try:
                    answer = await asyncio.wait_for(answer_future, timeout=300)
                    return PermissionResultAllow(
                        behavior="allow",
                        updated_input={"answers": answer},
                    )
                except TimeoutError:
                    return PermissionResultAllow(
                        behavior="allow",
                        updated_input={"answers": {"error": "timeout"}},
                    )
                finally:
                    self._pending_answers.pop(session_id, None)

            return PermissionResultAllow(behavior="allow")

        # ── PreToolUse hooks for path rewriting ────────────────────
        _INVALID_FILENAMES = frozenset({"null", "undefined", "none", ""})

        async def write_path_hook(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _context: HookContext,
        ) -> dict:
            tool_inp = hook_input.get("tool_input", {})
            file_path = str(tool_inp.get("file_path", ""))
            allowed, reason = enforcer.check_write_path(file_path)
            if not allowed:
                return {
                    "sync": True,
                    "continue_": True,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "decision": "reject",
                        "reason": reason,
                    },
                }
            if is_path_within_user_dir(file_path, self.user_id):
                return {"sync": True, "continue_": True}
            rewritten = rewrite_path_to_workspace(file_path, workspace)
            if rewritten == file_path:
                return {"sync": True, "continue_": True}
            logger.info("PreToolUse[Write]: '%s' → '%s'", file_path, rewritten)
            new_input = dict(tool_inp)
            new_input["file_path"] = rewritten
            return {
                "sync": True,
                "continue_": True,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": new_input,
                },
            }

        async def bash_path_hook(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _context: HookContext,
        ) -> dict:
            from src.workspace_enforcement import _rewrite_bash_command  # noqa: PLC0415

            cmd = str(hook_input.get("tool_input", {}).get("command", ""))
            if not cmd:
                return {"sync": True, "continue_": True}
            allowed, reason = BashCommandFilter.check(cmd)
            if not allowed:
                logger.debug("PreToolUse[Bash]: blocked info-leak command '%s'", cmd[:120])
                return {
                    "sync": True,
                    "continue_": True,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "decision": "reject",
                        "reason": "This operation is not permitted.",
                    },
                }
            rewritten = _rewrite_bash_command(cmd, workspace)
            if rewritten == cmd:
                return {"sync": True, "continue_": True}
            logger.info("PreToolUse[Bash]: rewrote '%s' → '%s'", cmd[:120], rewritten[:120])
            new_input = dict(hook_input.get("tool_input", {}))
            new_input["command"] = rewritten
            return {
                "sync": True,
                "continue_": True,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "updatedInput": new_input,
                },
            }

        async def read_path_hook(
            hook_input: HookInput,
            _tool_use_id: str | None,
            _context: HookContext,
        ) -> dict:
            from src.constants import MAX_READ_FILE_BYTES  # noqa: PLC0415

            tool_inp = hook_input.get("tool_input", {})
            file_path = str(tool_inp.get("file_path", ""))
            if not file_path:
                return {"sync": True, "continue_": True}
            allowed, reason = FileAccessFilter.check(file_path)
            if not allowed:
                logger.debug("PreToolUse[Read]: blocked sensitive file '%s'", file_path)
                return {
                    "sync": True,
                    "continue_": True,
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "decision": "reject",
                        "reason": "This operation is not permitted.",
                    },
                }
            if file_path and MAX_READ_FILE_BYTES > 0:
                resolved = Path(file_path)
                if not resolved.is_absolute():
                    resolved = workspace / file_path
                try:
                    file_size = resolved.stat().st_size
                    if file_size > MAX_READ_FILE_BYTES:
                        size_mb = file_size / (1024 * 1024)
                        limit_mb = MAX_READ_FILE_BYTES / (1024 * 1024)
                        logger.warning(
                            "PreToolUse[Read]: blocked oversized file '%s' (%.1fMB > %.1fMB)",
                            file_path, size_mb, limit_mb,
                        )
                        return {
                            "sync": True,
                            "continue_": True,
                            "hookSpecificOutput": {
                                "hookEventName": "PreToolUse",
                                "decision": "reject",
                                "reason": (
                                    f"File is {size_mb:.1f}MB. "
                                    f"The maximum allowed size for reading "
                                    f"is {limit_mb:.0f}MB. Please use Bash "
                                    f"commands like 'head' or 'split' to "
                                    f"process the file in smaller chunks."
                                ),
                            },
                        }
                except OSError:
                    pass
            return {"sync": True, "continue_": True}

        # ── Client lifecycle ───────────────────────────────────────
        agent_state = self._session_agents.get(session_id)
        client = agent_state["client"] if agent_state else None

        if client is not None:
            cached_skills = agent_state.get("skills", {})
            cached_sp = agent_state.get("system_prompt", "")
            options = await build_agent_options(
                self.user_id,
                skills_override=cached_skills,
                system_prompt_override=cached_sp,
                language=language,
                container_mode=False,
            )
        else:
            options = await build_agent_options(
                self.user_id, language=language, container_mode=False,
            )

        # Apply local-mode env overrides
        sdk_env = dict(options.env) if options.env else {}
        if options.user_data_dir:
            sdk_env["HOME"] = options.user_data_dir
        if options.skills_dir:
            sdk_env["CLAUDE_SKILLS_DIRS"] = options.skills_dir

        sdk_options = ClaudeAgentOptions(
            model=options.model,
            system_prompt=options.system_prompt,
            allowed_tools=options.allowed_tools,
            disallowed_tools=list(options.disallowed_tools),
            max_turns=options.max_turns,
            mcp_servers=options.mcp_servers,
            env=sdk_env if sdk_env else None,
            include_partial_messages=options.include_partial_messages,
            max_buffer_size=options.max_buffer_size,
            permission_mode=options.permission_mode,
            can_use_tool=can_use_tool_cb,
            hooks={
                "PreToolUse": [
                    {"matcher": "Write", "hooks": [write_path_hook]},
                    {"matcher": "Bash", "hooks": [bash_path_hook]},
                    {"matcher": "Read", "hooks": [read_path_hook]},
                ],
            },
        )

        if client is None:
            client = ClaudeSDKClient(sdk_options)

        try:
            if client is not None and agent_state is not None:
                if is_continuation:
                    if self._session_store is not None:
                        history = await self._session_store.get_session_history(
                            self.user_id, session_id, after_index=0,
                        )
                    else:
                        history = await self._buffer.get_history(
                            session_id, after_index=0, user_id=self.user_id,
                        )
                    full_prompt = build_history_prompt(
                        history, prompt, language=language, session_id=session_id,
                    )
                    if language:
                        lang_name = "中文" if language == "zh" else "English"
                        full_prompt = (
                            f"IMPORTANT: Your reply below, including all thinking blocks, "
                            f"must be in {lang_name}. Do not use "
                            f"{'英文' if language == 'zh' else 'Chinese'} in any part "
                            f"of your response.\n\n" + full_prompt
                        )
                else:
                    full_prompt = format_first_message_prompt(
                        prompt, attached_files, language, session_id,
                    )
                try:
                    await client.query(full_prompt)
                except CLIConnectionError:
                    logger.warning(
                        "Reused CLI dead for session %s, retrying with fresh client",
                        session_id,
                    )
                    await self._cleanup(session_id)
                    agent_state = None
                    options = await build_agent_options(
                        self.user_id, language=language, container_mode=False,
                    )
                    sdk_env = dict(options.env) if options.env else {}
                    if options.user_data_dir:
                        sdk_env["HOME"] = options.user_data_dir
                    if options.skills_dir:
                        sdk_env["CLAUDE_SKILLS_DIRS"] = options.skills_dir
                    sdk_options = ClaudeAgentOptions(
                        model=options.model,
                        system_prompt=options.system_prompt,
                        allowed_tools=options.allowed_tools,
                        disallowed_tools=list(options.disallowed_tools),
                        max_turns=options.max_turns,
                        mcp_servers=options.mcp_servers,
                        env=sdk_env if sdk_env else None,
                        include_partial_messages=options.include_partial_messages,
                        max_buffer_size=options.max_buffer_size,
                        permission_mode=options.permission_mode,
                        can_use_tool=can_use_tool_cb,
                    )
                    client = ClaudeSDKClient(sdk_options)
                    if is_continuation:
                        async def _retry_prompt_stream():
                            yield {
                                "type": "user",
                                "message": {"role": "user", "content": full_prompt},
                                "parent_tool_use_id": None,
                                "session_id": "default",
                            }
                        await client.connect(prompt=_retry_prompt_stream())
                    else:
                        await client.connect()
                        await client.query(full_prompt)
            elif is_continuation:
                if self._session_store is not None:
                    history = await self._session_store.get_session_history(
                        self.user_id, session_id, after_index=0,
                    )
                else:
                    history = await self._buffer.get_history(
                        session_id, after_index=0, user_id=self.user_id,
                    )
                full_prompt = build_history_prompt(
                    history, prompt, language=language, session_id=session_id,
                )
                if language:
                    lang_name = "中文" if language == "zh" else "English"
                    full_prompt = (
                        f"IMPORTANT: Your reply below, including all thinking blocks, "
                        f"must be in {lang_name}. Do not use "
                        f"{'英文' if language == 'zh' else 'Chinese'} in any part "
                        f"of your response.\n\n" + full_prompt
                    )

                async def prompt_stream():
                    yield {
                        "type": "user",
                        "message": {"role": "user", "content": full_prompt},
                        "parent_tool_use_id": None,
                        "session_id": "default",
                    }

                await client.connect(prompt=prompt_stream())
            else:
                await client.connect()
                prompt_text = format_first_message_prompt(
                    prompt, attached_files, language, session_id,
                )
                await client.query(prompt_text)

            # ── Cache skills + system prompt for reuse ─────────────
            if agent_state is None:
                skills = await self._get_cached_skills(self.user_id, session_id)
                resolved_lang = await self._resolve_user_language(self.user_id, language)
                instinct_ctx = await self._load_instinct_context(prompt, self._db)
                self._get_cached_system_prompt(
                    self.user_id, skills, workspace, resolved_lang, session_id,
                    instinct_context=instinct_ctx,
                )
                if session_id not in self._session_agents:
                    self._session_agents[session_id] = {}
                self._session_agents[session_id]["client"] = client
                self._session_agents[session_id]["last_used"] = time.time()
            else:
                self._session_agents[session_id]["last_used"] = time.time()

            # ── Main receive loop ─────────────────────────────────
            generated_files: list[dict[str, Any]] = []
            buffered_result: dict[str, Any] | None = None
            tool_observer = ToolObserver(self._obs_store, session_id, self.user_id)
            pre_scan_snapshot = self._snapshot_output_files(workspace, session_id)
            tool_use_names: dict[str, str] = {}

            ctx = EventContext(
                user_id=self.user_id,
                session_id=session_id,
                buffer=self._buffer,
                observer=tool_observer,
                skill_manager=self._skill_manager,
                generated_files=generated_files,
            )

            async for msg in client.receive_response():
                for event in adapt_sdk_message(msg, model=options.model, tool_use_names=tool_use_names):
                    if event.type == "result":
                        buffered_result = event.to_dict()
                        continue
                    await process_event(ctx, event)

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
                result_event=buffered_result,
                language=language,
            )

        except Exception as exc:
            await handle_task_error(
                exc,
                session_id=session_id,
                user_id=self.user_id,
                buffer=self._buffer,
                obs_store=self._obs_store,
                agent_log=agent_log,
                cleanup_fn=self._cleanup,
            )


async def cleanup_session_client(session_id: str, session_agents: dict) -> None:
    """Disconnect and remove a session's CLI subprocess or bridge from the pool."""
    agent = session_agents.pop(session_id, None)
    if agent is None:
        return
    client = agent.get("client")
    bridge = agent.get("bridge")
    if client is not None:
        try:
            await client.disconnect()
        except Exception:
            pass
    if bridge is not None:
        try:
            await bridge.disconnect()
        except Exception:
            pass
