"""MicroPython 1.29 baseline without frozen SCI-CALC application code."""

freeze("$(PORT_DIR)/modules")
include("$(MPY_DIR)/extmod/asyncio")
