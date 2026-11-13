#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generic REST provider.

One operation = one HTTP request described by the config::

    "get_task_sql": {
      "method": "GET",
      "path": "/api/tasks/{task_id}/sql",
      "query": {"format": "json"},
      "response": {"path": "data", "item": "sql"}
    }

Use ``service`` to route an operation to one of several ``endpoints``.
"""

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .base import MetadataAdapter
from .errors import AdapterRequestError
from .mapping import render


class RestAdapter(MetadataAdapter):
    provider_name = "rest"

    def _headers(self):
        headers = dict(self.config.headers or {})
        headers.setdefault("Accept", "application/json")
        return headers

    def _request(self, method, url, body=None):
        self.last_endpoint = url
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        context = ssl.create_default_context() if self.config.verify_ssl else ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout, context=context) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise AdapterRequestError("HTTP %s %s %s：%s" % (exc.code, method, url, detail))
        except urllib.error.URLError as exc:
            raise AdapterRequestError("无法连接 %s：%s" % (url, exc.reason))
        if not raw:
            return None
        text = raw.decode("utf-8", "replace").strip()
        try:
            return json.loads(text)
        except ValueError:
            return text

    def _invoke(self, operation, spec, params):
        endpoint = self.config.endpoint_for(spec)
        method = spec.get("method") or "GET"
        path = render(spec.get("path") or ("/" + operation), params)
        url = endpoint.rstrip("/") + "/" + str(path).lstrip("/")

        query = render(spec.get("query") or {}, params)
        if isinstance(query, dict):
            query = {k: v for k, v in query.items() if v not in (None, "")}
            if query:
                url += ("&" if "?" in url else "?") + urllib.parse.urlencode(
                    query, doseq=True)

        body = render(spec.get("body"), params) if spec.get("body") is not None else None
        retries = max(0, int(self.config.retries))
        delay = 2.0
        last_error = ""
        for attempt in range(retries + 1):
            try:
                return self._request(method, url, body)
            except AdapterRequestError as exc:
                last_error = str(exc)
                retryable = any(code in last_error[:32] for code in ("429", "500", "502", "503", "504"))
                if attempt < retries and retryable:
                    time.sleep(min(delay, 15))
                    delay *= 2
                    continue
                raise
        raise AdapterRequestError("%s 重试仍失败：%s" % (operation, last_error))
