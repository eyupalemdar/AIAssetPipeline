#!/usr/bin/env python3
"""Compatibility wrapper for `bootstrap.py doctor`."""

from __future__ import annotations

import sys

import bootstrap


if __name__ == "__main__":
    sys.exit(bootstrap.main(["doctor", *sys.argv[1:]]))
