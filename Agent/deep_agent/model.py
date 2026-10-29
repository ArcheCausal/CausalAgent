"""DeepSeek Responses 的工具选择与明文推理历史兼容。"""

from collections.abc import AsyncIterator, Iterator
from typing import Any

import openai
from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.messages import AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI
from langchain_openai.chat_models.base import (
    _add_gateway_metadata,
    _astream_with_chunk_timeout,
    _construct_responses_api_input,
    _convert_responses_chunk_to_generation_chunk,
    _handle_openai_api_error,
    _handle_openai_bad_request,
)


def _convert_deepseek_responses_chunk(
    chunk: Any, current_index: int, current_output_index: int, current_sub_index: int,
    **kwargs: Any,
) -> tuple[int, int, int, ChatGenerationChunk | None]:
    """用 reasoning 完成事件的原始 content 补全框架遗漏的明文推理。

    LangChain 1.6.2 处理 summary/encrypted_content，不处理 reasoning_text。
    added 事件已建立空 reasoning block；done 只补 content，避免重复拼接
    delta 文本、item ID 或状态。文本始终来自供应商的真实完成事件。
    """
    if chunk.type == "response.output_item.done" and chunk.item.type == "reasoning":
        content = chunk.item.model_dump(exclude_none=True, mode="json").get("content")
        if content:
            if current_output_index != chunk.output_index:
                current_index += 1
            return current_index, chunk.output_index, current_sub_index, ChatGenerationChunk(
                message=AIMessageChunk(
                    content=[{"type": "reasoning", "content": content, "index": current_index}],
                    response_metadata={"model_provider": "openai", **(kwargs.get("metadata") or {})},
                ),
            )
    return _convert_responses_chunk_to_generation_chunk(
        chunk, current_index, current_output_index, current_sub_index, **kwargs,
    )


class DeepSeekResponsesChatOpenAI(ChatOpenAI):
    """思考时使用 auto，工具 schema 与业务完成门禁仍由框架校验。

    DeepSeek 拒绝思考模式下的 required 和指定工具；LangChain ToolStrategy
    会主动绑定 any（随后映射为 required），因此在模型绑定边界处理此差异。
    """

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> Any:
        effort = (self.reasoning or {}).get("effort")
        if effort not in (None, "none") and tool_choice not in (None, "auto", "none", False):
            tool_choice = "auto"
        return super().bind_tools(tools, tool_choice=tool_choice, **kwargs)

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if self._use_responses_api(payload):
            # DeepSeek 无服务端会话，必须发送完整历史；store=False 也不能触发
            # OpenAI 专用的明文 reasoning 丢弃规则。
            payload["input"] = _construct_responses_api_input(
                self._convert_input(input_).to_messages(),
            )
            payload.pop("previous_response_id", None)
        return payload

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[ChatGenerationChunk]:
        # ChatOpenAI 的路由直接调用父类方法，必须在这里选择本适配器。
        if self._use_responses_api({**kwargs, **self.model_kwargs}):
            return self._stream_responses(*args, **kwargs)
        return super()._stream(*args, **kwargs)

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        stream = self._astream_responses if self._use_responses_api({**kwargs, **self.model_kwargs}) else super()._astream
        async for chunk in stream(*args, **kwargs):
            yield chunk

    def _stream_responses(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        """沿用已锁定框架的流式契约，仅替换 DeepSeek reasoning 转换。"""
        self._ensure_sync_client_available()
        payload = self._get_request_payload(messages, stop=stop, **{**kwargs, "stream": True})
        headers: dict = {}
        base_generation_info: dict = {}
        try:
            if self.include_response_headers or self._uses_gateway:
                raw_response = self.root_client.with_raw_response.responses.create(**payload)
                stream = raw_response.parse()
                if self.include_response_headers:
                    headers = {"headers": dict(raw_response.headers)}
                _add_gateway_metadata(base_generation_info, raw_response)
            else:
                stream = self.root_client.responses.create(**payload)
            first = True
            current_index = current_output_index = current_sub_index = -1
            has_reasoning = False
            with stream as response:
                for chunk in response:
                    current_index, current_output_index, current_sub_index, generation = _convert_deepseek_responses_chunk(
                        chunk, current_index, current_output_index, current_sub_index,
                        schema=kwargs.get("response_format"), metadata=headers if first else {},
                        has_reasoning=has_reasoning, output_version=self.output_version,
                    )
                    if generation:
                        if first and base_generation_info:
                            generation.generation_info = {**base_generation_info, **(generation.generation_info or {})}
                        if run_manager:
                            run_manager.on_llm_new_token(generation.text, chunk=generation)
                        first = False
                        has_reasoning |= "reasoning" in generation.message.additional_kwargs
                        yield generation
        except openai.BadRequestError as exc:
            _handle_openai_bad_request(exc)
        except openai.APIError as exc:
            _handle_openai_api_error(exc)

    async def _astream_responses(
        self, messages: list[BaseMessage], stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None, **kwargs: Any,
    ) -> AsyncIterator[ChatGenerationChunk]:
        """保留请求取消、分块超时、回调、用量和供应商异常分类。"""
        payload = self._get_request_payload(messages, stop=stop, **{**kwargs, "stream": True})
        headers: dict = {}
        base_generation_info: dict = {}
        try:
            if self.include_response_headers or self._uses_gateway:
                raw_response = await self.root_async_client.with_raw_response.responses.create(**payload)
                stream = raw_response.parse()
                if self.include_response_headers:
                    headers = {"headers": dict(raw_response.headers)}
                _add_gateway_metadata(base_generation_info, raw_response)
            else:
                stream = await self.root_async_client.responses.create(**payload)
            first = True
            current_index = current_output_index = current_sub_index = -1
            has_reasoning = False
            async with stream as response:
                async for chunk in _astream_with_chunk_timeout(
                    response, self.stream_chunk_timeout, model_name=self.model_name,
                ):
                    current_index, current_output_index, current_sub_index, generation = _convert_deepseek_responses_chunk(
                        chunk, current_index, current_output_index, current_sub_index,
                        schema=kwargs.get("response_format"), metadata=headers if first else {},
                        has_reasoning=has_reasoning, output_version=self.output_version,
                    )
                    if generation:
                        if first and base_generation_info:
                            generation.generation_info = {**base_generation_info, **(generation.generation_info or {})}
                        if run_manager:
                            await run_manager.on_llm_new_token(generation.text, chunk=generation)
                        first = False
                        has_reasoning |= "reasoning" in generation.message.additional_kwargs
                        yield generation
        except openai.BadRequestError as exc:
            _handle_openai_bad_request(exc)
        except openai.APIError as exc:
            _handle_openai_api_error(exc)
