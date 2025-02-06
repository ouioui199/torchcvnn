# MIT License

# Copyright (c) 2025 Quentin Gabot, Jeremy Fix, Huy Nguyen

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
from abc import ABC, abstractmethod
from typing import Tuple, Union
from types import NoneType

# External imports
import torch
import numpy as np
from PIL import Image

# Internal imports
import torchcvnn.transforms.functional as F


class BaseTransform(ABC):
    """Base class for transforms that handle numpy arrays and tensors."""
    def __init__(self, dtype: str | NoneType = None) -> None:
        if dtype is not None:
            assert isinstance(dtype, str), "dtype should be a string"
            assert dtype in ["float32", "float64", "complex64", "complex128"], "dtype should be one of float32, float64, complex64, complex128"
            self.np_dtype = getattr(np, dtype)
            self.torch_dtype = getattr(torch, dtype)
    
    def __call__(self, x: np.ndarray | torch.Tensor) -> np.ndarray | torch.Tensor:
        """Apply transform to input."""
        # Ensure the input is in CHW format
        x = F.ensure_chw_format(x)
        if not isinstance(x, (np.ndarray, torch.Tensor)):
            raise ValueError("Element should be a numpy array or a tensor")
        elif isinstance(x, np.ndarray):
            return self.__call_numpy__(x)
        elif isinstance(x, torch.Tensor):
            return self.__call_torch__(x)

    @abstractmethod
    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        """Apply transform to numpy array."""
        raise NotImplementedError
    
    @abstractmethod
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        """Apply transform to torch tensor."""
        raise NotImplementedError
    

class LogAmplitude(BaseTransform):
    """
    Transform the amplitude of a complex tensor to a log scale between a min and max value.

    After this transform, the phases are the same but the magnitude is log transformed and
    scaled in [0, 1]

    Arguments:
        min_value: The minimum value of the amplitude range to clip
        max_value: The maximum value of the amplitude range to clip
    """

    def __init__(self, min_value: int | float = 0.02, max_value: int | float = 40, keep_phase: bool = True) -> None:
        self.min_value = min_value
        self.max_value = max_value
        self.keep_phase = keep_phase

    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        amplitude = np.abs(x)
        phase = np.angle(x)
        amplitude = np.clip(amplitude, self.min_value, self.max_value)
        transformed_amplitude = (
            np.log10(amplitude / self.min_value)
        ) / (np.log10(self.max_value / self.min_value))
        if self.keep_phase:
            return transformed_amplitude * np.exp(1j * phase)
        else:
            return transformed_amplitude
        
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        amplitude = torch.abs(x)
        phase = torch.angle(x)
        amplitude = torch.clip(amplitude, self.min_value, self.max_value)
        transformed_amplitude = (
            torch.log10(amplitude / self.min_value)
        ) / (np.log10(self.max_value / self.min_value))
        if self.keep_phase:
            return transformed_amplitude * torch.exp(1j * phase)
        else:
            return transformed_amplitude


class Amplitude(BaseTransform):
    """
    Transform a complex tensor into a real tensor, based on its amplitude.
    """
    def __init__(self, dtype: str) -> None:
        super().__init__(dtype)

    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        return torch.abs(x).to(self.torch_dtype)
    
    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        return np.abs(x).astype(self.np_dtype)


class RealImaginary(BaseTransform):
    """
    Transform a complex tensor into a real tensor, based on its real and imaginary parts.
    """
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        x = torch.stack([x.real, x.imag], dim=0) # CHW -> 2CHW
        x = x.flatten(0, 1) # 2CHW -> 2C*H*W
        return x
    
    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        x = np.stack([x.real, x.imag], axis=0) # CHW -> 2CHW
        x = x.reshape(-1, *x.shape[2:]) # 2CHW -> 2C*H*W
        return x


class RandomPhase(BaseTransform):
    """
    Transform a real tensor into a complex tensor, by applying a random phase to the tensor.
    """
    def __init__(self, dtype: str, centering: bool = False) -> None:
        super().__init__(dtype)
        self.centering = centering

    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        phase = torch.rand_like(x) * 2 * torch.pi
        if self.centering:
            phase = phase - torch.pi
        return (x * torch.exp(1j * phase)).to(self.torch_dtype)
    
    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        phase = np.random.rand(*x.shape) * 2 * np.pi
        if self.centering:
            phase = phase - np.pi
        return (x * np.exp(1j * phase)).astype(self.np_dtype)


class FFT2(BaseTransform):
    """Apply 2D Fast Fourier Transform to the image"""

    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        return F.applyfft2(x, axis=(-2, -1))
    
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        return torch.fft.fftshift(torch.fft.fft2(x), dim=(-2, -1))
    

class IFFT2(BaseTransform):
    """Apply 2D Inverse Fast Fourier Transform to the image"""

    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        return F.applyifft2(x, axis=(-2, -1))
    
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        return torch.fft.ifft2(torch.fft.ifftshift(x, dim=(-2, -1)))


class PadIfNeeded(BaseTransform):
    """
    Pad an image if its dimensions are smaller than specified minimum dimensions.

    This class extends BaseTransform and provides functionality to pad images 
    that are smaller than the specified minimum height and width. The padding
    can be applied with different border modes.

    Attributes:
        min_height (int): Minimum height requirement for the image
        min_width (int): Minimum width requirement for the image
        border_mode (str): Type of padding to apply ('constant', 'reflect', etc.)
        dtype (str | NoneType): Data type for the output (optional)
    """
    def __init__(
        self, 
        min_height: int,
        min_width: int,
        border_mode: str = "constant",
        pad_value: float = 0
    ) -> None:
        self.min_height = min_height
        self.min_width = min_width
        self.border_mode = border_mode
        self.pad_value = pad_value

    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        return F.padifneeded(x, self.min_height, self.min_width, self.border_mode, self.pad_value)
    
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        return F.padifneeded(x, self.min_height, self.min_width, self.border_mode, self.pad_value)


class FFTResize:
    """
    Resize a complex tensor to a given size. The resize is performed in the Fourier
    domain by either cropping or padding the FFT2 of the input array/tensor.

    Arguments:
        size: The target size of the resized tensor.
    """

    def __init__(self, size):
        self.size = size

    def __call__(
        self, array: Union[np.array, torch.tensor]
    ) -> Union[np.array, torch.Tensor]:

        is_torch = False
        if isinstance(array, torch.Tensor):
            is_torch = True
            array = array.numpy()

        real_part = array.real
        imaginary_part = array.imag

        def zoom(array):
            # Computes the 2D FFT of the array and center the zero frequency component
            array = np.fft.fftshift(np.fft.fft2(array))
            original_size = array.shape

            # Either center crop or pad the array to the target size
            target_size = self.size
            if array.shape[0] < target_size[0]:
                # Computes top and bottom padding
                top_pad = (target_size[0] - array.shape[0] + 1) // 2
                bottom_pad = target_size[0] - array.shape[0] - top_pad
                array = np.pad(array, ((top_pad, bottom_pad), (0, 0)))
            elif array.shape[0] > target_size[0]:
                top_crop = array.shape[0] // 2 - target_size[0] // 2
                bottom_crop = top_crop + target_size[0]
                array = array[top_crop:bottom_crop, :]

            if array.shape[1] < target_size[1]:
                left_pad = (target_size[1] - array.shape[1] + 1) // 2
                right_pad = target_size[1] - array.shape[1] - left_pad
                array = np.pad(array, ((0, 0), (left_pad, right_pad)))
            elif array.shape[1] > target_size[1]:
                left_crop = array.shape[1] // 2 - target_size[1] // 2
                right_crop = left_crop + target_size[1]
                array = array[:, left_crop:right_crop]

            # Computes the inverse 2D FFT of the array
            array = np.fft.ifft2(np.fft.ifftshift(array))
            scale = (target_size[0] * target_size[1]) / (
                original_size[0] * original_size[1]
            )

            return scale * array

        if len(array.shape) == 2:
            # We have a two dimensional tensor
            resized_real = zoom(real_part)
            resized_imaginary = zoom(imaginary_part)
        else:
            # We have three dimensions and therefore
            # apply the resize to each channel iteratively
            # We assume the first dimension is the channel
            resized_real = []
            resized_imaginary = []
            for real, imaginary in zip(real_part, imaginary_part):
                resized_real.append(zoom(real))
                resized_imaginary.append(zoom(imaginary))
            resized_real = np.stack(resized_real)
            resized_imaginary = np.stack(resized_imaginary)

        resized_array = resized_real + 1j * resized_imaginary

        # Convert the resized tensor back to a torch tensor if necessary
        if is_torch:
            resized_array = torch.as_tensor(resized_array)

        return resized_array


class SpatialResize:
    """
    Resize a complex tensor to a given size. The resize is performed in the image space
    using a Bicubic interpolation.

    Arguments:
        size: The target size of the resized tensor.
    """

    def __init__(self, size):
        self.size = size

    def __call__(
        self, array: Union[np.array, torch.tensor]
    ) -> Union[np.array, torch.Tensor]:

        is_torch = False
        if isinstance(array, torch.Tensor):
            is_torch = True
            array = array.numpy()

        real_part = array.real
        imaginary_part = array.imag

        def zoom(array):
            # Convert the numpy array to a PIL image
            image = Image.fromarray(array)

            # Resize the image
            image = image.resize((self.size[1], self.size[0]))

            # Convert the PIL image back to a numpy array
            array = np.array(image)

            return array

        if len(array.shape) == 2:
            # We have a two dimensional tensor
            resized_real = zoom(real_part)
            resized_imaginary = zoom(imaginary_part)
        else:
            # We have three dimensions and therefore
            # apply the resize to each channel iteratively
            # We assume the first dimension is the channel
            resized_real = []
            resized_imaginary = []
            for real, imaginary in zip(real_part, imaginary_part):
                resized_real.append(zoom(real))
                resized_imaginary.append(zoom(imaginary))
            resized_real = np.stack(resized_real)
            resized_imaginary = np.stack(resized_imaginary)

        resized_array = resized_real + 1j * resized_imaginary

        # Convert the resized tensor back to a torch tensor if necessary
        if is_torch:
            resized_array = torch.as_tensor(resized_array)

        return resized_array


class PolSARtoTensor:
    """
    Transform a PolSAR image into a 3D torch tensor.
    """

    def __call__(self, element: Union[np.ndarray, dict]) -> torch.Tensor:
        if isinstance(element, np.ndarray):
            assert len(element.shape) == 3, "Element should be a 3D numpy array"
            if element.shape[0] == 3:
                return self._create_tensor(element[0], element[1], element[2])
            if element.shape[0] == 2:
                return self._create_tensor(element[0], element[1])
            elif element.shape[0] == 4:
                return self._create_tensor(
                    element[0], (element[1] + element[2]) / 2, element[3]
                )

        elif isinstance(element, dict):
            if len(element) == 3:
                return self._create_tensor(element["HH"], element["HV"], element["VV"])
            elif len(element) == 2:
                if "HH" in element:
                    return self._create_tensor(element["HH"], element["HV"])
                elif "VV" in element:
                    return self._create_tensor(element["HV"], element["VV"])
                else:
                    raise ValueError(
                        "Dictionary should contain keys HH, HV, VV or HH, VV"
                    )
            elif len(element) == 4:
                return self._create_tensor(
                    element["HH"], (element["HV"] + element["VH"]) / 2, element["VV"]
                )
        else:
            raise ValueError("Element should be a numpy array or a dictionary")

    def _create_tensor(self, *channels) -> torch.Tensor:
        return torch.as_tensor(
            np.stack(channels, axis=-1).transpose(2, 0, 1),
            dtype=torch.complex64,
        )


class Unsqueeze(BaseTransform):
    """
    Add a dimension to a tensor.

    Arguments:
        dim: The dimension of the axis/dim to extend
    """

    def __init__(self, dim: int) -> None:
        self.dim = dim

    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        return np.expand_dims(x, axis=self.dim)
    
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        return x.unsqueeze(dim=self.dim)


class ToTensor(BaseTransform):
    """
    Convert a numpy array to a tensor.
    """
    def __init__(self, dtype: str) -> None:
        super().__init__(dtype)

    def __call_numpy__(self, x: np.ndarray) -> np.ndarray:
        return torch.as_tensor(x, dtype=self.torch_dtype)
    
    def __call_torch__(self, x: torch.Tensor) -> torch.Tensor:
        return x.to(self.torch_dtype)