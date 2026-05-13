"""Foundation model evaluator for ASAL search.

Wraps CLIP (via open_clip) to embed rendered simulation frames into a
representation space where distance corresponds to perceptual difference.
The search algorithms use these embeddings to score diversity (illumination),
novelty (open-endedness), or target alignment (supervised search).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import torch
from PIL import Image

# open_clip provides the same CLIP models ASAL uses, in PyTorch
import open_clip


class CLIPEvaluator:
    """Embed simulation frames using CLIP ViT-B/32.

    Usage::

        evaluator = CLIPEvaluator()
        frames = substrate.rollout(theta, n_steps=2000, n_frames=20)
        embedding = evaluator.embed_rollout(frames)  # (512,) mean embedding
        embeddings = evaluator.embed_frames(frames)   # (20, 512)
    """

    def __init__(
        self,
        model_name: str = "ViT-B-32",
        pretrained: str = "openai",
        device: str | None = None,
    ):
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)

        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            model_name, pretrained=pretrained, device=self.device,
        )
        self.model.eval()

        # Get embedding dimension from model
        self._embed_dim = self.model.visual.output_dim

    @property
    def embed_dim(self) -> int:
        """Dimensionality of CLIP embeddings (typically 512)."""
        return self._embed_dim

    @torch.no_grad()
    def embed_frames(self, frames: list[np.ndarray]) -> np.ndarray:
        """Embed a list of RGB frames into CLIP space.

        Parameters
        ----------
        frames : list[np.ndarray]
            List of RGB images, each (H, W, 3) uint8.

        Returns
        -------
        np.ndarray
            Embeddings of shape (n_frames, embed_dim), L2-normalized.
        """
        # Convert numpy frames to PIL, apply CLIP preprocessing
        tensors = []
        for frame in frames:
            pil_img = Image.fromarray(frame)
            tensor = self.preprocess(pil_img)
            tensors.append(tensor)

        batch = torch.stack(tensors).to(self.device)
        embeddings = self.model.encode_image(batch)

        # L2 normalize (standard for CLIP similarity)
        embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)

        return embeddings.cpu().numpy()

    @torch.no_grad()
    def embed_text(self, texts: list[str]) -> np.ndarray:
        """Embed text prompts into CLIP space.

        Used for supervised target search (not needed for illumination).

        Parameters
        ----------
        texts : list[str]
            Natural language descriptions.

        Returns
        -------
        np.ndarray
            Embeddings of shape (n_texts, embed_dim), L2-normalized.
        """
        tokenizer = open_clip.get_tokenizer("ViT-B-32")
        tokens = tokenizer(texts).to(self.device)
        embeddings = self.model.encode_text(tokens)
        embeddings = embeddings / embeddings.norm(dim=-1, keepdim=True)
        return embeddings.cpu().numpy()

    def embed_rollout(self, frames: list[np.ndarray]) -> np.ndarray:
        """Embed a rollout's frames and return the mean embedding.

        This is the standard ASAL approach for representing a full
        simulation rollout as a single point in CLIP space.

        Parameters
        ----------
        frames : list[np.ndarray]
            Rendered frames from a simulation rollout.

        Returns
        -------
        np.ndarray
            Mean embedding of shape (embed_dim,), L2-normalized.
        """
        frame_embeddings = self.embed_frames(frames)
        mean_emb = frame_embeddings.mean(axis=0)
        mean_emb = mean_emb / np.linalg.norm(mean_emb)
        return mean_emb

    def embed_rollouts_batch(self, rollout_frames: list[list[np.ndarray]]) -> np.ndarray:
        """Embed multiple rollouts in one batched GPU call.

        Flattens all frames across all rollouts into a single batch,
        sends through CLIP once, then reshapes and computes per-rollout
        mean embeddings. Much higher GPU utilization than per-rollout calls.

        Parameters
        ----------
        rollout_frames : list[list[np.ndarray]]
            List of rollouts, each a list of RGB frames.

        Returns
        -------
        np.ndarray
            Mean embeddings of shape (n_rollouts, embed_dim), L2-normalized.
        """
        # Flatten all frames with a mapping back to rollout index
        all_frames = []
        boundaries = [0]
        for frames in rollout_frames:
            all_frames.extend(frames)
            boundaries.append(len(all_frames))

        # One batched GPU call for all frames
        all_embeddings = self.embed_frames(all_frames)

        # Compute per-rollout means
        n_rollouts = len(rollout_frames)
        result = np.zeros((n_rollouts, self._embed_dim))
        for i in range(n_rollouts):
            start, end = boundaries[i], boundaries[i + 1]
            mean_emb = all_embeddings[start:end].mean(axis=0)
            result[i] = mean_emb / np.linalg.norm(mean_emb)

        return result

    def pairwise_distances(self, embeddings: np.ndarray) -> np.ndarray:
        """Compute pairwise cosine distances between embeddings.

        Parameters
        ----------
        embeddings : np.ndarray
            Shape (n, embed_dim), L2-normalized.

        Returns
        -------
        np.ndarray
            Pairwise distance matrix of shape (n, n).
            Distance = 1 - cosine_similarity.
        """
        # Since embeddings are L2-normalized, cosine_sim = dot product
        sim = embeddings @ embeddings.T
        return 1.0 - sim
