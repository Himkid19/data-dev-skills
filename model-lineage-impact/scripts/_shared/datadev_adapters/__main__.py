#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""``python -m datadev_adapters`` entry point."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
