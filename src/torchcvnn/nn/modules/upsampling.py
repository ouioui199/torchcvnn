# MIT License

# Copyright (c) 2023-2025 Jérémie Levi, Victor Dhédin, Jeremy Fix, Huy Nguyen

# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.

# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


# Standard imports
from typing import Optional, Tuple

# External imports
import torch
import torch.nn as nn
from torch.nn.common_types import _size_any_t, _ratio_any_t

# Internal imports
import torchcvnn.transforms.functional as F


class Upsample(nn.Module):

    """
    Works by applying independently the same upsampling to both the real and
    imaginary parts.

    Note:
        With pytorch 2.1, applying the nn.Upsample to a complex valued tensor raises
        an exception "compute_indices_weights_nearest" not implemented for 'ComplexFloat'
        So it basically splits the input tensors in its real and imaginery
        parts, applies nn.Upsample on both components and view them as complex.


    Arguments:
        size (int or Tuple[int] or Tuple[int, int] or Tuple[int, int, int], optional):
            output spatial sizes
        scale_factor (float or Tuple[float] or Tuple[float, float] or Tuple[float, float, float], optional):
            multiplier for spatial size. Has to match input size if it is a tuple.
        mode (str, optional): the upsampling algorithm: one of ``'nearest'``,
            ``'linear'``, ``'bilinear'``, ``'bicubic'`` and ``'trilinear'``.
            Default: ``'nearest'``
        align_corners (bool, optional): if ``True``, the corner pixels of the input
            and output tensors are aligned, and thus preserving the values at
            those pixels. This only has effect when :attr:`mode` is
            ``'linear'``, ``'bilinear'``, ``'bicubic'``, or ``'trilinear'``.
            Default: ``False``
        recompute_scale_factor (bool, optional): recompute the scale_factor for use in the
            interpolation calculation. If `recompute_scale_factor` is ``True``, then
            `scale_factor` must be passed in and `scale_factor` is used to compute the
            output `size`. The computed output `size` will be used to infer new scales for
            the interpolation. Note that when `scale_factor` is floating-point, it may differ
            from the recomputed `scale_factor` due to rounding and precision issues.
            If `recompute_scale_factor` is ``False``, then `size` or `scale_factor` will
            be used directly for interpolation.
    """

    def __init__(
        self,
        size: Optional[_size_any_t] = None,
        scale_factor: Optional[_ratio_any_t] = None,
        mode: str = "nearest",
        align_corners: Optional[bool] = None,
        recompute_scale_factor: Optional[bool] = None,
    ) -> None:
        super().__init__()
        self.up_module = nn.Upsample(
            size, scale_factor, mode, align_corners, recompute_scale_factor
        )

    def forward(self, z: torch.Tensor) -> torch.Tensor:
        """
        Applies the forward pass
        """
        up_real = self.up_module(z.real).unsqueeze(-1)
        up_imag = self.up_module(z.imag).unsqueeze(-1)
        up_z = torch.cat((up_real, up_imag), axis=-1)
        return torch.view_as_complex(up_z)


class UpsampleFFT(nn.Module):
    """Upsamples a tensor using Discrete Fourier Transform.
    This module performs upsampling by zero-padding in the frequency domain. It first applies
    DFT to transform the input to frequency domain, then zero-pads to the target size, and
    finally applies inverse DFT to get back to spatial domain. Optionally, it scales the output 
    amplitudes to maintain energy consistency between original and resized images.
    
    Upsampling works with both real-valued and complex-valued tensors. It returns real-valued tensors
    if the input is real-valued, and complex-valued tensors if the input is complex-valued.
    
    Args:
        scale: bool, optional. Scale the output to maintain energy consistency with 
            respect to input size. Default is True.
        size (tuple or int, optional): Target output size (H, W). If int, assumes square output.
            Either size or scale_factor must be specified, but not both.
        scale_factor (tuple or float, optional): Multiplier for spatial size.
            Either size or scale_factor must be specified, but not both.
    Shape:
        - Input: (C, H, W) or (N, C, H, W) or (N, C, D, H, W)
        - Output: (C, H_out, W_out) or (N, C, H_out, W_out) or (N, C, D, H_out, W_out)
            where H_out and W_out are determined by size or scale_factor
            
    Examples:
        >>> m = UpsampleFFT(size=(4, 4))
        >>> input = torch.randn(1, 3, 2, 2)
        >>> output = m(input)  # output size: (1, 3, 4, 4)
        >>> m = UpsampleFFT(scale_factor=(2.0, 2.0))
        >>> input = torch.randn(1, 3, 2, 2)
        >>> output = m(input)  # output size: (1, 3, 4, 4)
        
    Note:
        - Input tensor must be at least 2D
        - The last two dimensions are considered as spatial dimensions (H, W)
        - This method preserves frequency information better than interpolation-based methods
    """
    def __init__(
        self,
        scale: bool = True,
        size: Optional[_size_any_t] = None,
        scale_factor: Optional[_ratio_any_t] = None
    ) -> None:
        super().__init__()
        self.scale = scale
        # Validate input parameters
        if not (bool(size) ^ bool(scale_factor)):  # XOR operation
            raise ValueError("Exactly one of size or scale_factor must be specified")
        # Handle size parameter
        self.size = tuple([size] * 2) if isinstance(size, int) else size
        # Handle scale_factor parameter
        if scale_factor is not None:
            self.scale_factor = (float(scale_factor),) * 2 if isinstance(scale_factor, (int, float)) else scale_factor
        # Validate tuple lengths
        for param, name in [(self.size, 'size'), (self.scale_factor, 'scale_factor')]:
            if param is not None and isinstance(param, tuple) and len(param) != 2:
                raise ValueError(f"{name} must be an int or a tuple of length 2")
        
    def upsampling(self, z: torch.Tensor, ratio: float) -> torch.Tensor:
        # Apply Discrete Fourier Transform over the last two dimenstions, typically Height and Width
        z = F.applyfft2_torch(z)
        # Zero pad the input tensor to the desired size
        z = F.padifneeded(z, self.size[0], self.size[1])
        # Center crop the spectrum to the target size
        z = F.center_crop(z, self.size[0], self.size[1])
        # Apply Inverse Discrete Fourier Transform over the last two dimensions
        z = F.applyifft2_torch(z)
        # Scale the output tensor or not
        if self.scale:
            return z * ratio
        return z
        
    def forward(self, z: torch.Tensor) -> torch.Tensor:
        # Tensors should be at least 2D.
        if z.ndim < 2:
            raise ValueError("Input tensor should be at least 2D")
        # If the size is not specified, the output size will be computed as the input size multiplied by the scale factor
        if self.scale_factor is not None:
            self.size = (int(z.shape[-2] * self.scale_factor[0]), int(z.shape[-1] * self.scale_factor[1]))
        # Compute data scaling ratio
        original_size = z.shape[1] * z.shape[2]
        target_size = self.height * self.width
        ratio = target_size / original_size
        # Return complex-valued tensor if the input is complex-valued, otherwise return real-valued
        if z.is_complex():
            return self.upsampling(z, ratio)
        return self.upsampling(z, ratio).real
