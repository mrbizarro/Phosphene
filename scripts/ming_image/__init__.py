"""Phosphene's native MLX runner for Ming-Image-0.2-Design.

Pure MLX (no PyTorch, transformers, or diffusers at inference), compatible with
Phosphene's pinned mlx==0.31.1 runtime. Supports full aspect-ratio canvases at
Standard (~1 MP) and High (~4 MP) buckets, native RGBA PNG output, and
layer/block disk streaming for 16-24 GB Apple Silicon Macs.
"""
from __future__ import annotations

RUNNER_VERSION = "1"
