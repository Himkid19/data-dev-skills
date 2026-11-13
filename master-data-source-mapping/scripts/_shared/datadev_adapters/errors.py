#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Exceptions raised by the data-dev adapter layer."""


class AdapterError(RuntimeError):
    """Base class: any adapter failure that the caller should surface."""


class AdapterConfigError(AdapterError):
    """The adapter config is missing, malformed or does not describe an operation."""


class AdapterRequestError(AdapterError):
    """The remote service returned an error or an unreadable payload."""
