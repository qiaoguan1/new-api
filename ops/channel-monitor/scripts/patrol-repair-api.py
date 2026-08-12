#!/usr/bin/env python3
"""Executable wrapper for the loopback patrol control API."""

import sys

import patrol_api


if __name__ == "__main__":
    sys.exit(patrol_api.main())
