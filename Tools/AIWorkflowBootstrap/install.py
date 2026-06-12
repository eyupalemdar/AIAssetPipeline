#!/usr/bin/env python3
"""Compatibility wrapper for `bootstrap.py install`."""

from __future__ import annotations

import sys

import bootstrap


if __name__ == "__main__":
    sys.exit(bootstrap.main(["install", *sys.argv[1:]]))
