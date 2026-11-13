#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""JSON-RPC MCP provider (stateless Streamable HTTP).

Works with any MCP server that exposes ``tools/call`` / ``tools/list``.  Which
tool backs each logical operation, and the argument names, come from the
config file; the default tool name equals the operation name.
"""

import json
import ssl
import time
import urllib.error
import urllib.request

from .base import MetadataAdapter
from .errors import AdapterRequestError
from .mapping import render


def _decode_body(raw, content_type=""):
    text = raw.decode("utf-8", "replace").strip()
    if "text/event-stream" in (content_type or "") or text.startswith(("event:", "data:")):
        payloads = []
        for line in text.splitlines():
            if line.startswith("data:"):
                payloads.append(line[5:].strip())
        for candidate in reversed(payloads):
            try:
                return json.loads(candidate)
            except ValueError:
                continue
        raise AdapterRequestError("无法解析 SSE 响应：%s" % text[:300])
    try:
        return json.loads(text)
    except ValueError:
        raise AdapterRequestError("服务返回的不是 JSON：%s" % text[:300])


class JsonRpcMcpAdapter(MetadataAdapter):
    provider_name = "jsonrpc-mcp"

    def _endpoint(self, spec):
        return self.config.endpoint_for(spec)

    def _headers(self):
        headers = dict(self.config.headers or {})
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("Accept", "application/json, text/event-stream")
        return headers

    def _post(self, url, payload):
        self.last_endpoint = url
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=self._headers())
        if self.config.verify_ssl:
            context = ssl.create_default_context()
        else:
            context = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout, context=context) as resp:
                return _decode_body(resp.read(), resp.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:500]
            raise AdapterRequestError("HTTP %s @ %s：%s" % (exc.code, url, body))
        except urllib.error.URLError as exc:
            raise AdapterRequestError("无法连接 %s：%s" % (url, exc.reason))

    def _rpc(self, url, payload, label):
        retries = max(0, int(self.config.retries))
        delay = 2.0
        last_error = ""
        for attempt in range(retries + 1):
            try:
                data = self._post(url, payload)
            except AdapterRequestError as exc:
                last_error = str(exc)
                if attempt < retries and ("429" in last_error or "50" in last_error[:40]):
                    time.sleep(min(delay, 15))
                    delay *= 2
                    continue
                raise
            if isinstance(data, dict) and data.get("error"):
                error = data["error"]
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise AdapterRequestError("%s 失败：%s" % (label, message))
            result = data.get("result") if isinstance(data, dict) else None
            if isinstance(result, dict) and result.get("isError"):
                blocks = result.get("content") or []
                message = "; ".join(b.get("text", "") for b in blocks if isinstance(b, dict))
                last_error = message or str(result)
                if attempt < retries and ("限流" in last_error or "rate" in last_error.lower()):
                    time.sleep(min(delay, 15))
                    delay *= 2
                    continue
                raise AdapterRequestError("%s 失败：%s" % (label, last_error))
            return result if result is not None else data
        raise AdapterRequestError("%s 重试仍失败：%s" % (label, last_error or "unknown"))

    def _invoke(self, operation, spec, params):
        url = self._endpoint(spec)
        tool = spec.get("tool") or operation
        arguments = render(spec.get("arguments") or {}, params)
        if spec.get("drop_empty", True) and isinstance(arguments, dict):
            arguments = {k: v for k, v in arguments.items() if v not in ("", None)}
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": tool, "arguments": arguments}}
        result = self._rpc(url, payload, "MCP tool %s" % tool)
        if isinstance(result, dict):
            if result.get("structuredContent") is not None:
                return result["structuredContent"]
            blocks = result.get("content") or []
            texts = [b.get("text", "") for b in blocks
                     if isinstance(b, dict) and b.get("type", "text") == "text"]
            text = "\n".join(t for t in texts if t)
            if text:
                try:
                    return json.loads(text)
                except ValueError:
                    return text
            return result
        return result

    # ------------------------------------------------------------------ extras
    def list_tools(self):
        """Return the raw ``tools/list`` payload (used by ``doctor``/debugging)."""
        spec = {"tool": "tools/list"}
        url = self._endpoint(spec)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        result = self._rpc(url, payload, "MCP tools/list")
        return (result or {}).get("tools", result)
