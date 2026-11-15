# Copyright 2024 ComfyUI_ExtraModels contributors + drawatoon port
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
#
# This file defines the ComfyUI-native `DrawatoonMS` architecture: a
# `PixArtMS` (PixArt-Sigma) transformer with an extra parallel IP-adapter
# cross-attention layer in every block and an `IPAdapter` module that
# turns (text bboxes, character bboxes, MAGI embeddings) into ip tokens.
#
# It is the ComfyUI equivalent of `fumeisama/drawatoon-v1`'s
# `pixart_transformer_modified.py`, adapted to the ExtraModels pattern
# so the stock KSampler can drive it.
#
# Notes on the port:
#  * `ip_attn` uses `IPCrossAttention` (a subclass of ExtraModels'
#    `MultiHeadCrossAttention`) instead of diffusers' `Attention`, so this
#    pack does NOT depend on the `diffusers` pip package being installed.
#    The weight converter in `diffusers_convert.py` reshapes the diffusers
#    keys (`to_q`/`to_k`/`to_v`/`to_out.0`) into
#    (`q_linear`/`kv_linear`/`proj`) to match.
#  * `IPCrossAttention` overrides `forward` to properly handle the
#    `[B, seq_len]` boolean attention mask produced by `IPAdapter`. The
#    parent `MultiHeadCrossAttention` was designed for the text cross-attn
#    path, where the mask is a *list of ints* (`y_lens = [L, L, ...]`), and
#    crashes when handed a 2-D bool tensor with `B > 1`. Delegating via the
#    parent for equal-length batches (the common case after `pad_sequence`)
#    keeps behaviour identical; the per-batch fallback handles the rare
#    case where different batch elements have different ip-token counts.
#  * `forward` is overridden to make sure `**kwargs` reaches `forward_raw`,
#    otherwise `ip_hidden_states` / `ip_attention_mask` get dropped by the
#    parent `PixArtMS.forward` and layout conditioning silently no-ops.
#  * `forward_raw` moves `ip_hidden_states` / `ip_attention_mask` to
#    `x.device` and broadcasts single-sample ip conds (as produced by the
#    `DrawatoonSetIP` node) to the latent batch size. This lets the cond
#    metadata carry CPU-resident tensors that come from an offloaded model
#    without erroring at attention time.
#  * When NO `ip_hidden_states` is supplied (no `DrawatoonSetIP` on this
#    branch), `forward_raw` builds the ip tokens from empty bbox lists.
#    `IPAdapter.forward` on an empty sample emits a single
#    `void_ip_embed` token — the trained "no layout" case, which is
#    exactly what CFG subtraction needs on an uncond branch that was
#    left unwired.
#  * In `DrawatoonMSBlock.forward` the text cross-attn and the ip cross-attn
#    are computed from the SAME `x` input (parallel), matching the author's
#    patched block which does:
#        hidden_states = attn_output + ip_attn_output + hidden_states
#    Sequential application (cross-attn, THEN ip-attn on the updated x) would
#    put the ip branch out of distribution relative to training.

import torch
from torch import nn
from einops import rearrange
from torch.nn.utils.rnn import pad_sequence

from .PixArtMS import PixArtMS, PixArtMSBlock
from .PixArt_blocks import (
    MultiHeadCrossAttention,
    t2i_modulate,
)


# ---------------------------------------------------------------------------
# IPAdapter — verbatim port of the author's `IPAdapter`, with QoL tweaks:
#  * `forward` handles the empty-batch "void" case for text-only generation.
#  * RBFEmbedding accepts a tensor directly (author assumed a list).
# ---------------------------------------------------------------------------
class RBFEmbedding(nn.Module):
    def __init__(self, output_dim, num_kernels=32):
        super().__init__()
        self.means = nn.Parameter(torch.linspace(0, 1, num_kernels))
        self.scales = nn.Parameter(torch.ones(num_kernels) * 20)
        self.proj = nn.Linear(num_kernels * 4, output_dim)

    def forward(self, box):
        if not torch.is_tensor(box):
            box = torch.as_tensor(box, dtype=self.means.dtype, device=self.means.device)
        else:
            box = box.to(dtype=self.means.dtype, device=self.means.device)
        x = box.unsqueeze(-1) - self.means
        x = torch.exp(-0.5 * (x * self.scales.unsqueeze(0)) ** 2)
        x = x.reshape(-1)
        return self.proj(x)


class RoPEPositionalEmbedding(nn.Module):
    def __init__(self, embedding_dim, base=10000):
        super().__init__()
        self.embedding_dim = embedding_dim
        assert embedding_dim % 2 == 0, "Embedding dimension must be even"
        half_dim = embedding_dim // 2
        freqs = 1.0 / (base ** (torch.arange(0, half_dim).float() / half_dim))
        self.register_buffer("freqs", freqs)

    def forward(self, x, positions):
        orig_dtype = x.dtype
        x = x.float()
        positions = positions.float()
        x_2d = rearrange(x, '... (d two) -> ... d two', two=2)
        positions = positions.unsqueeze(-1) * self.freqs.float()
        sin = positions.sin().unsqueeze(-1)
        cos = positions.cos().unsqueeze(-1)
        x_out = torch.cat([
            x_2d[..., 0:1] * cos - x_2d[..., 1:2] * sin,
            x_2d[..., 0:1] * sin + x_2d[..., 1:2] * cos,
        ], dim=-1)
        output = rearrange(x_out, '... d two -> ... (d two)')
        return output.to(orig_dtype)


class IPAdapter(nn.Module):
    """
    Encodes (text_bboxes, character_bboxes, MAGI_embeddings) into a sequence
    of tokens consumed by every block's parallel `ip_attn` cross-attention.

    If the batch has no layout at all, returns the `void_ip_embed` fallback —
    this makes plain text-to-image (no layout) work without extra plumbing.
    """
    def __init__(self):
        super().__init__()
        self.embedding_dim = 1152
        self.box_embedding = RBFEmbedding(self.embedding_dim)
        self.pos_embedding = RoPEPositionalEmbedding(self.embedding_dim)
        self.text_cls_embedding = nn.Embedding(1, self.embedding_dim)
        self.character_cls_embedding = nn.Embedding(4, self.embedding_dim)
        self.ref_embedding_proj = nn.Linear(768, 4 * self.embedding_dim)
        self.void_ip_embed = nn.Embedding(1, self.embedding_dim)
        self.negative_ip_embed = nn.Embedding(1, self.embedding_dim)
        self.norm = nn.LayerNorm(self.embedding_dim)

    def embed_text(self, box):
        box_embedding = self.box_embedding(box)
        return torch.stack([box_embedding, *self.text_cls_embedding.weight])

    def embed_character(self, character_bbox, reference_embedding):
        box_embedding = self.box_embedding(character_bbox)
        if reference_embedding is None:
            character_embedding = self.character_cls_embedding.weight
        else:
            character_embedding = self.ref_embedding_proj(
                reference_embedding.unsqueeze(0)
            )
            character_embedding = rearrange(character_embedding, "1 (c h) -> h c", h=4)
        return torch.stack([box_embedding, *character_embedding])

    def apply_position_embedding(self, embeddings):
        seq_length = embeddings.shape[0]
        positions = torch.arange(seq_length, device=embeddings.device, dtype=embeddings.dtype)
        return self.pos_embedding(embeddings, positions)

    def forward(
        self,
        batch_text_bboxes,
        batch_character_bboxes,
        batch_reference_embeddings,
        cfg_on_10_percent=False,
    ):
        ip_embeddings = []
        for batch_idx, (text_bboxes, character_bboxes, reference_embeddings) in enumerate(
            zip(batch_text_bboxes, batch_character_bboxes, batch_reference_embeddings)
        ):
            text_embeddings = [self.embed_text(box) for box in text_bboxes]
            character_embeddings = [
                self.embed_character(box, reference_embeddings[i])
                if i < len(reference_embeddings) else self.embed_character(box, None)
                for i, box in enumerate(character_bboxes)
            ]
            if len(text_embeddings) + len(character_embeddings) == 0:
                ip_embeddings.append(self.void_ip_embed.weight)
                continue
            ip_embedding = torch.cat(text_embeddings + character_embeddings, dim=0)
            ip_embeddings.append(self.apply_position_embedding(ip_embedding))

        ip_mask = [
            torch.ones(x.shape[0], dtype=torch.bool, device=x.device) for x in ip_embeddings
        ]
        ip_embeddings = pad_sequence(ip_embeddings, batch_first=True, padding_value=0)
        ip_mask = pad_sequence(ip_mask, batch_first=True, padding_value=0).bool()

        if cfg_on_10_percent:
            last_10_percent = int(len(ip_embeddings) * 0.1)
            ip_embeddings[-last_10_percent:] = self.negative_ip_embed.weight
            ip_mask[-last_10_percent:] = 0
            ip_mask[-last_10_percent:, :1] = 1

        return self.norm(ip_embeddings), ip_mask


# ---------------------------------------------------------------------------
# IPCrossAttention — subclass of MultiHeadCrossAttention that accepts a
# [B, seq_len] bool tensor mask (as produced by IPAdapter) in addition to
# the parent's list-of-ints format.
# ---------------------------------------------------------------------------
class IPCrossAttention(MultiHeadCrossAttention):
    def forward(self, x, cond, mask=None):
        if mask is None:
            seq_lens = None
        elif torch.is_tensor(mask):
            mask_bool = mask.to(torch.bool)
            seq_lens = mask_bool.sum(dim=-1).tolist()
        else:
            seq_lens = [int(sl) for sl in mask]

        if seq_lens is None:
            return super().forward(x, cond, None)

        B, N, C = x.shape
        if len(seq_lens) != B:
            raise ValueError(
                f"IPCrossAttention: mask has {len(seq_lens)} entries "
                f"but batch is {B}"
            )

        if all(sl == seq_lens[0] for sl in seq_lens):
            return super().forward(x, cond, seq_lens)

        outputs = []
        for b in range(B):
            xb = x[b:b+1]
            cond_b = cond[b, :seq_lens[b]].unsqueeze(0)
            out_b = super().forward(xb, cond_b, None)
            outputs.append(out_b)
        return torch.cat(outputs, dim=0)


# ---------------------------------------------------------------------------
# DrawatoonMSBlock
# ---------------------------------------------------------------------------
class DrawatoonMSBlock(PixArtMSBlock):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        hidden_size = self.hidden_size
        num_heads = self.attn.num_heads
        assert hidden_size % num_heads == 0
        self.ip_attn = IPCrossAttention(hidden_size, num_heads)
        self.ip_attn.proj.weight.data.zero_()
        self.ip_attn.proj.bias.data.zero_()

    def forward(
        self,
        x,
        y,
        t,
        mask=None,
        HW=None,
        ip_hidden_states=None,
        ip_attention_mask=None,
        **kwargs,
    ):
        B, N, C = x.shape

        shift_msa, scale_msa, gate_msa, shift_mlp, scale_mlp, gate_mlp = (
            self.scale_shift_table[None] + t.reshape(B, 6, -1)
        ).chunk(6, dim=1)

        x = x + self.drop_path(
            gate_msa * self.attn(t2i_modulate(self.norm1(x), shift_msa, scale_msa), HW=HW)
        )

        cross_out = self.cross_attn(x, y, mask)
        if ip_hidden_states is not None:
            ip_out = self.ip_attn(x, ip_hidden_states, ip_attention_mask)
            x = x + cross_out + ip_out
        else:
            x = x + cross_out

        x = x + self.drop_path(
            gate_mlp * self.mlp(t2i_modulate(self.norm2(x), shift_mlp, scale_mlp))
        )
        return x


# ---------------------------------------------------------------------------
# DrawatoonMS
# ---------------------------------------------------------------------------
class DrawatoonMS(PixArtMS):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ip_adapter = IPAdapter()

        new_blocks = nn.ModuleList()
        for old in self.blocks:
            mlp_ratio_val = old.mlp.fc1.out_features / old.mlp.fc1.in_features
            qk_norm_val = not isinstance(old.attn.q_norm, nn.Identity)
            sr_ratio_val = getattr(old.attn, "sr_ratio", 1)
            sampling_val = getattr(old.attn, "sampling", None)

            nb = DrawatoonMSBlock(
                hidden_size = old.hidden_size,
                num_heads   = old.attn.num_heads,
                mlp_ratio   = mlp_ratio_val,
                drop_path   = 0.0,
                input_size  = (self.base_size, self.base_size),
                sampling    = sampling_val,
                sr_ratio    = sr_ratio_val,
                qk_norm     = qk_norm_val,
            )
            missing, unexpected = nb.load_state_dict(old.state_dict(), strict=False)
            assert len(unexpected) == 0, f"unexpected in parent load: {unexpected}"
            assert all("ip_attn" in k for k in missing), f"unexpected missing: {missing}"

            new_blocks.append(nb)
        self.blocks = new_blocks

    def forward(self, x, timesteps, context, img_hw=None, aspect_ratio=None, **kwargs):
        bs = x.shape[0]
        data_info = {}
        if img_hw is None:
            data_info["img_hw"] = torch.tensor(
                [[x.shape[2]*8, x.shape[3]*8]],
                dtype=self.dtype,
                device=x.device,
            ).repeat(bs, 1)
        else:
            data_info["img_hw"] = img_hw.to(dtype=x.dtype, device=x.device)

        data_info["aspect_ratio"] = torch.tensor(
            [[x.shape[2]/x.shape[3]]],
            dtype=self.dtype,
            device=x.device,
        ).repeat(bs, 1)

        if len(context.shape) == 3:
            context = context.unsqueeze(1)

        out = self.forward_raw(
            x = x.to(self.dtype),
            t = timesteps.to(self.dtype),
            y = context.to(self.dtype),
            data_info = data_info,
            **kwargs,
        )

        out = out.to(torch.float)
        eps, rest = out[:, :self.in_channels], out[:, self.in_channels:]
        return eps

    def forward_raw(
        self,
        x,
        t,
        y,
        mask=None,
        data_info=None,
        ip_hidden_states=None,
        ip_attention_mask=None,
        text_bboxes=None,
        character_bboxes=None,
        reference_embeddings=None,
        **kwargs,
    ):
        """
        Same signature as PixArtMS.forward_raw, but accepts and forwards the
        drawatoon layout conditions.

        If `ip_hidden_states` is None (no `DrawatoonSetIP` on this branch),
        empty bbox lists are fed to `IPAdapter`, which emits a single
        `void_ip_embed` token per sample — the trained no-layout case.
        That is the correct value for CFG subtraction on an unwired
        uncond branch.

        If `ip_hidden_states` IS provided (via `DrawatoonSetIP`), it's
        used directly.  Single-sample tensors are broadcast to the latent
        batch.
        """
        if ip_hidden_states is None:
            bs = x.shape[0]
            if text_bboxes is None:
                text_bboxes = [[] for _ in range(bs)]
            if character_bboxes is None:
                character_bboxes = [[] for _ in range(bs)]
            if reference_embeddings is None:
                reference_embeddings = [[] for _ in range(bs)]
            with torch.no_grad():
                ip_hidden_states, ip_attention_mask = self.ip_adapter(
                    text_bboxes, character_bboxes, reference_embeddings, False
                )

        # Device alignment + single-sample broadcast. The DrawatoonSetIP
        # node may produce [1, L, C] tensors on CPU (if the model is
        # offloaded at node-execution time); the sampler runs on GPU.
        if ip_hidden_states is not None:
            ip_hidden_states = ip_hidden_states.to(x.device)
            ip_attention_mask = ip_attention_mask.to(x.device)
            if ip_hidden_states.shape[0] == 1 and x.shape[0] > 1:
                ip_hidden_states = ip_hidden_states.repeat(x.shape[0], 1, 1)
                ip_attention_mask = ip_attention_mask.repeat(x.shape[0], 1)

        kwargs["ip_hidden_states"] = ip_hidden_states
        kwargs["ip_attention_mask"] = ip_attention_mask

        return super().forward_raw(
            x, t, y, mask=mask, data_info=data_info, **kwargs,
        )
