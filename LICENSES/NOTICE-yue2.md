# YuE2 notices

YuE2 by Multimodal Art Projection:
https://huggingface.co/m-a-p/YuE2-3B and
https://huggingface.co/m-a-p/YuE2-Vae.

The MLX port is vanch007/mlx-Yue (https://github.com/vanch007/mlx-Yue),
Apache-2.0, as is the upstream code. Phosphene downloads the generator
converted to MLX by that project from https://huggingface.co/vanch007/mlx-Yue2-3B
(BF16 tensors byte-identical to m-a-p/YuE2-3B; the 8-bit AR file is a
quantization of them) and the unchanged decoder from m-a-p/YuE2-Vae.
Phosphene does not re-host the weights.
Oobleck VAE and SnakeBeta are MIT; their notices travel in the downloaded pack.

The weights are CC BY-NC 4.0 plus an individual-creator permission: individual
creators may monetize songs they create; companies need a license for the
weights. See the complete, unmodified YuE2-MODEL_LICENSE.txt beside this notice
(upstream MODEL_LICENSE, 2026-09-16). The port's older vendored license is not
this permission's source.
